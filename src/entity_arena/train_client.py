"""INF retriever 训练平台 MCP-over-HTTP 客户端（er_console ③ 打通用，纯标准库）。

与 .tmp/mcp_call.sh 同一套已验证通路（2026-11-08/11-17 实测）：
- Streamable HTTP：先 ``initialize``（protocolVersion 2025-03-26）从响应头取
  ``Mcp-Session-Id``，再 ``notifications/initialized``，之后 ``tools/call`` 带
  session 头；响应是 SSE（``data: {...}`` 行）。
- "Session not found" → 重新握手一次再重试（会话过期自愈）。
- 平台网关掐 OpenSSL 3.x 默认 ClientHello → 强制 TLS1.2（同 embed_client）。

令牌：调用方注入（er_console 三级解析），本模块不落盘、不打印。
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

DEFAULT_URL = "https://mcp-inf-retriever-train.openapi-inspire.inf.tech/mcp"
TIMEOUT_S = 300                      # 与 .tmp/mcp_call.sh 同档（上传/状态都可能慢）
_PROTOCOL = "2025-03-26"


class TrainError(RuntimeError):
    """平台调用失败（人话 message，可直接进 ConsoleError）。"""


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _sse_messages(text: str) -> list[dict]:
    """SSE 响应体 → JSON 对象列表（data: 行 + 兜底整体 JSON）。"""
    out: list[dict] = []
    for ln in text.splitlines():
        if ln.startswith("data:"):
            payload = ln[5:].strip()
            if payload:
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    if not out:                        # 平台偶尔回普通 JSON（非 SSE）
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                out.append(obj)
        except ValueError:
            pass
    return out


class TrainClient:
    """一个平台会话（线程安全约定：er_console 单进程串行调用即可）。"""

    def __init__(self, url: str, token: str, timeout: int = TIMEOUT_S):
        self.url = url.rstrip("/")
        self.token = token if token.startswith("Bearer ") else f"Bearer {token}"
        self.timeout = timeout
        self._session: str | None = None
        self._id = 0

    # ---- 底层
    def _post(self, body: dict, want_session: bool = False) -> tuple[list[dict], str | None]:
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": self.token,
                     "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream",
                     **({"Mcp-Session-Id": self._session} if self._session else {})},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=_ssl_ctx()) as resp:
                text = resp.read().decode("utf-8")
                sid = resp.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise TrainError(f"平台返回 HTTP {e.code}：{detail}") from None
        except (urllib.error.URLError, ssl.SSLError, OSError) as e:
            raise TrainError(f"连不上训练平台（{self.url}）：{e}") from None
        return _sse_messages(text), (sid if want_session else None)

    def _handshake(self) -> None:
        body = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL, "capabilities": {},
                           "clientInfo": {"name": "er-console", "version": "1.0"}}}
        msgs, sid = self._post(body, want_session=True)
        if not sid:
            raise TrainError("平台握手没回 Mcp-Session-Id（服务异常？稍后再试）")
        self._session = sid
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, arguments: dict) -> dict:
        """tools/call → 解包 result（structuredContent 优先，其次 content[].text）。"""
        self._id += 1
        rid = self._id
        body = {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}}
        if self._session is None:
            self._handshake()
        msgs, _ = self._post(body)
        reply = next((m for m in msgs if m.get("id") == rid and
                     ("result" in m or "error" in m)), None)
        if reply is None and any("Session not found" in json.dumps(m) for m in msgs):
            self._handshake()                     # 会话过期自愈（一次）
            msgs, _ = self._post(body)
            reply = next((m for m in msgs if m.get("id") == rid and
                         ("result" in m or "error" in m)), None)
        if reply is None:
            raise TrainError(f"平台 {tool} 没有返回结果（响应异常）")
        if "error" in reply:
            raise TrainError(f"平台 {tool} 报错：{reply['error'].get('message')}")
        result = reply["result"]
        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", [])]
            raise TrainError(f"平台 {tool} 执行失败：{' '.join(texts)[:400]}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for c in result.get("content", []):
            txt = c.get("text", "")
            try:
                obj = json.loads(txt)
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                pass
            if txt.strip():
                return {"text": txt}
        return {}

    # ---- 业务面（参数名对齐 skill references/api.md）
    def upload_dataset(self, zip_bytes: bytes, name: str) -> dict:
        import base64
        return self.call("upload_dataset",
                         {"content_base64": base64.b64encode(zip_bytes).decode("ascii"),
                          "name": name})

    def create_finetune_job(self, dataset_ids: list[str], base_model_name: str,
                            output_name: str, *, serving_name: str | None = None,
                            use_hard_negatives: bool = True,
                            evaluate: bool = True) -> dict:
        args = {"dataset_ids": dataset_ids, "base_model_name": base_model_name,
                "output_name": output_name, "mode": "train",
                "use_hard_negatives": use_hard_negatives, "evaluate": evaluate}
        if serving_name:
            args["serving_name"] = serving_name
        return self.call("create_finetune_job", args)

    def get_job_status(self, job_id: str, include_log: bool = False) -> dict:
        return self.call("get_job_status",
                         {"job_id": job_id, **({"include_log": True}
                                               if include_log else {})})

    def deploy_model(self, *, job_id: str | None = None,
                     model_name: str | None = None,
                     serving_name: str | None = None) -> dict:
        args: dict = {}
        if job_id:
            args["job_id"] = job_id
        elif model_name:
            args["model_name"] = model_name
        else:
            raise TrainError("deploy_model 需要 job_id 或 model_name")
        if serving_name:
            args["serving_name"] = serving_name
        return self.call("deploy_model", args)

    def list_models(self) -> list[dict]:
        """list_resources(types=["models"]) → [{name, profile?}…]（base_models 列表）。"""
        out = self.call("list_resources", {"types": ["models"]})
        m = out.get("models")
        items = (m.get("base_models") if isinstance(m, dict) else m) or []
        return [x for x in items if isinstance(x, dict) and x.get("name")]

    def list_services(self) -> list[dict]:
        out = self.call("list_resources", {"types": ["services"]})
        svc = out.get("services")
        items = (svc.get("services") if isinstance(svc, dict) else svc) or []
        return [x for x in items if isinstance(x, dict)]
