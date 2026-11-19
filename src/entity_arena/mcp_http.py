"""mcp_http：托管 MCP-over-HTTP 传输层（纯标准库）。

把 scripts/er_mcp_server.py 的工具层（er_score_pair / er_find_records / er_about）
原样挂到 HTTP 上——客户一条命令接入，免下载免配置：

  claude mcp add --transport http entity-er https://<host>/mcp \
      --header "Authorization: Bearer <key>"

设计要点：

- **工具层零改动复用**：importlib 载入 scripts/er_mcp_server.py，env 预设
  （ER_VILLAGE/ER_SCORES/ER_META 指向工作区 mcp_bundle/）→ exec → env 恢复。
  er_mcp_server.py 文件本身一字不动，stdio 面的 Febrl 默认路径字节锁
  （tests/test_mcp_server.py）不受影响。bundle 缺席时不回落 Febrl 默认路径
  （那是演示数据，不是本工作区的口径）——换 _DegradedCore：工具可列出、
  调用回「先完成 ③④⑤」的人话指引，注册命令始终有效。
- **鉴权**：每工作区静态 key（`Authorization: Bearer <key>`，常数时间比较）。
  解析序 --mcp-key > MCP_KEY 环境变量 > `<workdir>/.mcp_key`（缺席自动生成，
  chmod 600，best-effort）。key 展示在控制台 ⑤ 部署页（发给客户的静态键，
  同 er_gateway 的 api_key 语义）；healthz 绝不带 key。
- **令牌边界不变**：自由文本在线打分用**服务端**令牌（er_server 已载入的
  同一份，三级解析），只在进程内传给工具层模块实例——客户侧永远只拿静态
  key，平台 EMB_TOKEN 绝不出现在响应/页面/注册命令里。
- **在线 URL 解析**：ER_EMB_URL 环境变量 > 工作区 `.train/state.json` 的
  service_url（③ 训练部署出来的就是它）> DEFAULT_URL——根治离线包
  「URL 默认 gemma、model 默认 meta」的错配坑（托管模式两头都来自
  同一工作区状态）。
- **协议**：Streamable HTTP 的 JSON 模式——POST 单条 JSON-RPC，响应
  application/json；initialize 回 Mcp-Session-Id（无状态：生成即弃，
  后续请求不校验）；notifications → 202 空体；GET → 405（不提供
  server-sent 流）；DELETE → 200（无状态，受理即释）。协议版本协商与
  scripts/er_mcp_server.py 同一份 SUPPORTED_PROTOCOLS 口径。
- **传输与协议分层**：McpEndpoint.handle() 收 (method, headers, body)
  回 (status, headers, body)，不碰 socket——测试免起 HTTP 服务；
  er_server 里十行胶水把它挂到 /mcp。
"""

from __future__ import annotations

import hmac
import importlib.util
import json
import os
import secrets
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 与 scripts/er_mcp_server.py 的 SUPPORTED_PROTOCOLS 同口径（镜像，不 import——
# 载入核心才有原表，协商要在降级态也能答）
PROTOCOLS = ("2025-06-18", "2025-03-26", "2025-01-28", "2024-11-05")

PARSE_ERROR, INVALID_REQUEST = -32700, -32600
METHOD_NOT_FOUND, INVALID_PARAMS = -32601, -32602

_JSON = [("Content-Type", "application/json; charset=utf-8")]


# ---------- key 管理 ----------
def resolve_key(explicit: str | None, env: str | None,
                workdir: Path) -> tuple[str, str]:
    """静态 key 三级解析：--mcp-key > MCP_KEY > <workdir>/.mcp_key（自动生成）。

    返回 (key, 来源描述)。生成物 chmod 600（best-effort，平台 fs 可能不理会）。
    """
    if explicit:
        return explicit, "--mcp-key"
    if env:
        return env, "MCP_KEY"
    kf = Path(workdir) / ".mcp_key"
    try:
        key = kf.read_text(encoding="utf-8").strip()
        if key:
            return key, str(kf)
    except OSError:
        pass
    key = "ea-" + secrets.token_hex(16)
    try:
        kf.write_text(key + "\n", encoding="utf-8")
        try:
            kf.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass   # 只读 fs：进程内仍可用（重启换 key，可接受）
    return key, str(kf)


def resolve_emb_url(workdir: Path, default: str) -> str:
    """在线打分服务地址：ER_EMB_URL > .train/state.json service_url > default。"""
    env = os.environ.get("ER_EMB_URL")
    if env:
        return env
    try:
        state = json.loads(
            (Path(workdir) / ".train" / "state.json").read_text(encoding="utf-8"))
        url = (state or {}).get("service_url")
        if url:
            return str(url)
    except (OSError, ValueError):
        pass
    return default


# ---------- 工具层载入 ----------
def load_core(bundle_dir: Path, emb_url: str | None,
              emb_token: str | None):
    """importlib 载入 er_mcp_server，env 预设指向工作区交付包；缺件返回 None。

    er_mcp_server 在 import 期读 env 定路径/模型（ER_META→meta.model），
    所以先预设再 exec、随后恢复——进程 env 不被污染，模块实例持有自己的
    常量（在线 URL/令牌只进这个实例，绝不外泄）。
    """
    bundle = Path(bundle_dir)
    need = ("records.json", "scores.json", "er_meta.json")
    if not all((bundle / f).exists() for f in need):
        return None
    preset = {
        "ER_VILLAGE": str(bundle / "records.json"),
        "ER_SCORES": str(bundle / "scores.json"),
        "ER_META": str(bundle / "er_meta.json"),
    }
    if emb_url:
        preset["ER_EMB_URL"] = emb_url
    if emb_token:
        preset["EMB_TOKEN"] = emb_token
    saved = {k: os.environ.get(k) for k in preset}
    try:
        os.environ.update(preset)
        spec = importlib.util.spec_from_file_location(
            "er_mcp_server_hosted", ROOT / "scripts" / "er_mcp_server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)   # type: ignore[union-attr]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return mod


class _DegradedCore:
    """交付包缺席时的替身核：工具可列出，调用回上线指引（不回落 Febrl 演示数据）。"""

    SERVER_NAME = "entity-er"
    SERVER_VERSION = "1.1.0"
    EMB_MODEL = None

    @staticmethod
    def _tools() -> list[dict]:
        return [
            {"name": "er_score_pair",
             "description": "Score two records (real cosine + θ verdict + field "
                            "diff). Waiting on workspace artifacts — see "
                            "er_about for setup guidance.",
             "inputSchema": {"type": "object",
                             "properties": {"a": {"type": "string"},
                                            "b": {"type": "string"}},
                             "required": ["a", "b"]}},
            {"name": "er_find_records",
             "description": "Search this workspace's records by substring. "
                            "Waiting on workspace artifacts.",
             "inputSchema": {"type": "object",
                             "properties": {"q": {"type": "string"},
                                            "limit": {"type": "integer"}}}},
            {"name": "er_about",
             "description": "Provenance & config: model, θ origin, mode status.",
             "inputSchema": {"type": "object", "properties": {}}},
        ]

    @staticmethod
    def _handle_call(name, arguments):
        return {"content": [{"type": "text", "text":
                "workspace delivery data not built yet (mcp_bundle/ absent) — "
                "finish console steps ③–⑤ (train → build artifacts); this "
                "endpoint starts serving them automatically, no re-register "
                "needed."}], "isError": True}


# ---------- 端点（协议层，不碰 socket） ----------
class McpEndpoint:
    """一个工作区一个端点：鉴权 + JSON-RPC 分发（镜像 er_mcp_server._dispatch）。"""

    def __init__(self, key: str, core, protocols: tuple = PROTOCOLS):
        self.key = key
        self.core = core if core is not None else _DegradedCore()
        self.protocols = tuple(protocols)
        # 降级核没有 ToolError；空元组 except 子句永不命中，分发逻辑保持单一
        self._tool_error = getattr(self.core, "ToolError", ())

    # ---- 状态面（healthz / 控制台）----
    @property
    def ready(self) -> bool:
        return not isinstance(self.core, _DegradedCore)

    def info(self) -> dict:
        """healthz 用：绝不带 key。"""
        return {"path": "/mcp", "auth": True, "ready": self.ready,
                "model": getattr(self.core, "EMB_MODEL", None),
                "live_free_text": bool(getattr(self.core, "EMB_TOKEN", None))}

    def console_state(self) -> dict:
        """控制台 ⑤ 部署页用：带 key（发给客户的静态键，同 er_gateway 语义）。"""
        return {"enabled": True, "path": "/mcp", "key": self.key,
                "ready": self.ready}

    # ---- HTTP 语义 ----
    def handle(self, method: str, headers, body: bytes):
        """一次 HTTP 请求 → (status, [(header, value)], body_bytes)。"""
        if not self._authed(headers):
            return 401, [("WWW-Authenticate", 'Bearer realm="entity-er"'),
                         *_JSON], json.dumps({
                "error": "missing or invalid API key — pass the workspace MCP "
                         "key: Authorization: Bearer <key> (console ⑤ deploy "
                         "page)"}, ensure_ascii=False).encode("utf-8")
        if method == "GET":
            return 405, [("Allow", "POST, DELETE"), *_JSON], json.dumps({
                "error": "no server-sent stream; POST JSON-RPC messages here"
            }).encode("utf-8")
        if method == "DELETE":
            # 无状态服务：会话即释（Mcp-Session-Id 不校验，受理即答）
            return 200, _JSON, b"{}"
        if method != "POST":
            return 405, [("Allow", "POST, DELETE"), *_JSON], json.dumps({
                "error": "method not allowed"}).encode("utf-8")

        try:
            msg = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._err(None, PARSE_ERROR, "invalid JSON")
        if not isinstance(msg, dict):
            return self._err(None, INVALID_REQUEST, "message must be an object")
        if "id" not in msg:
            # notification（notifications/initialized 等）：受理不回包
            return 202, [], b""
        return self._dispatch(msg)

    def _authed(self, headers) -> bool:
        auth = (headers.get("Authorization") or "").strip() if headers else ""
        want = "Bearer " + self.key
        return hmac.compare_digest(auth.encode("utf-8"), want.encode("utf-8"))

    # ---- JSON-RPC 分发 ----
    def _dispatch(self, msg: dict):
        method, rid = msg.get("method"), msg["id"]
        extra: list[tuple[str, str]] = []
        if method == "initialize":
            want = (msg.get("params") or {}).get("protocolVersion")
            version = want if want in self.protocols else self.protocols[0]
            # 无状态：会话号生成即弃（客户端带着来我们也不校验，但规范要求回一个）
            extra = [("Mcp-Session-Id", uuid.uuid4().hex)]
            return self._ok(rid, {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.core.SERVER_NAME,
                               "version": self.core.SERVER_VERSION},
            }, extra)
        if method == "tools/list":
            return self._ok(rid, {"tools": self.core._tools()}, extra)
        if method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            try:
                return self._ok(
                    rid, self.core._handle_call(
                        name, (msg.get("params") or {}).get("arguments")), extra)
            except KeyError:
                return self._err(rid, INVALID_PARAMS, f"unknown tool: {name}")
            except self._tool_error as e:   # 工具层人话错误 → isError 结果
                return self._ok(rid, self._text(str(e), is_error=True), extra)
            except Exception as e:          # 工具内部崩了也不许弄死协议层
                return self._ok(rid, self._text(
                    f"internal tool error: {type(e).__name__}: {e}",
                    is_error=True), extra)
        if method == "ping":
            return self._ok(rid, {}, extra)
        return self._err(rid, METHOD_NOT_FOUND, f"method not found: {method}")

    @staticmethod
    def _text(text: str, is_error: bool = False) -> dict:
        out = {"content": [{"type": "text", "text": text}]}
        if is_error:
            out["isError"] = True
        return out

    @staticmethod
    def _ok(rid, result: dict, extra: list | None = None):
        return 200, [*((extra or [])), *_JSON], _jdump(
            {"jsonrpc": "2.0", "id": rid, "result": result})

    @staticmethod
    def _err(rid, code: int, message: str):
        return 400 if code == PARSE_ERROR else 200, _JSON, _jdump(
            {"jsonrpc": "2.0", "id": rid,
             "error": {"code": code, "message": message}})


def _jdump(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")
