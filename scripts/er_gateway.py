#!/usr/bin/env python3
"""er-gateway：多租户 ER 打分网关（MaaS API 原型，单进程，纯标准库）。

一个进程同时是：① 各租户 demo 页的同源静态托管（浏览器直连上游没 CORS，
  打分必须同源转发——play_live_server 模式的多租户版）；② MaaS API。

用法（注册表在仓库外，θ/模型名从各租户 cases 产物读，永不手调）：
  python3 scripts/er_gateway.py --registry ~/.config/entity-arena/er_tenants.json

注册表结构（JSON；api_key 是发给该租户客户的静态键，令牌另行走三级解析
--token > EMB_TOKEN > ~/.config/entity-arena/emb_token，都不进仓库）：
  {
    "default_tenant": "acme",
    "tenants": {
      "acme": {
        "label": "Acme records",
        "endpoint": "https://<平台部署服务地址>",
        "model": "acme-er-model",
        "cases": "/abs/path/open_cases.json",
        "fieldmap": "/abs/path/fieldmap.csv",
        "page": "/abs/path/demo.html",
        "api_key": "发给客户的静态键"
      }
    }
  }

路由：
  GET  /                        → 落地页：租户清单 + 各自入口
  GET  /t/<name>/               → 该租户 demo 页（注册表 page；没配则 404 人话）
  POST /t/<name>/api/score      → 演示页同源打分（页面 fetch 相对路径，免 api_key）
  POST /api/score               → default_tenant（Febrl 主页 raw 文本兼容路径）
  POST /v1/er/score             → {"tenant":…, "a":…, "b":…}（a/b=结构化对象或文本；
                                  Authorization: Bearer <该租户 api_key>）
  POST /v1/er/embed             → {"tenant":…, "inputs":[…]}（≤32/批，document 口径）
  POST /v1/er/resolve           → {"tenant":…, "records":[{record_id?, 字段…}]} →
                                  θ 断言链归并组件 + borderline 对清单（≤256 条/批）
错误全部人话 JSON：404 未知租户 / 400 字段或入参 / 401·403 键 / 429 并发护栏
（bench 锚点：16 并发触上游 429，默认在途上限 8）/ 502 上游失败 / 503 无令牌。
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.embed_client import DEFAULT_MODEL, DEFAULT_URL  # noqa: E402
from entity_arena.gateway import Gateway, GatewayError, RegistryError, load_registry  # noqa: E402

# 令牌解析与 play_live_server 同一套（三级、仓库外文件）——加载复用，不抄一份。
_spec = importlib.util.spec_from_file_location(
    "play_live_server", ROOT / "scripts" / "play_live_server.py")
_pls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pls)
load_token = _pls.load_token

DEFAULT_REGISTRY = Path.home() / ".config" / "entity-arena" / "er_tenants.json"


def _landing(gw: Gateway) -> bytes:
    rows = []
    for name in sorted(gw.tenants):
        t = gw.tenants[name]
        # 注册表是运维侧输入，但 label/model/name 仍统一转义——注入防线不该依赖上游卫生
        esc_name, esc_label, esc_model = html.escape(name), html.escape(t.label), html.escape(t.model)
        page = (f'<a href="/t/{esc_name}/">/t/{esc_name}/</a>' if t.page
                else f"<code>/t/{esc_name}/</code>（未配 page）")
        dflt = " · default" if name == gw.default_tenant else ""
        rows.append(
            f"<tr><td>{esc_label}<br><small>{esc_name}{dflt}</small></td>"
            f"<td><code>{esc_model}</code></td><td>{t.theta:.4f}</td>"
            f"<td>{page}</td><td><code>POST /v1/er/score</code> 等</td></tr>")
    # （局部变量别叫 html——模块顶 import html 的转义函数要用，撞名会 UnboundLocal）
    page = (
        "<!doctype html><meta charset='utf-8'><title>er-gateway</title>"
        "<style>body{font:14px/1.6 -apple-system,system-ui,sans-serif;max-width:880px;"
        "margin:40px auto;padding:0 16px;color:#1a2233}table{border-collapse:collapse;"
        "width:100%}td,th{border-bottom:1px solid #dde3ec;padding:10px 8px;text-align:left;"
        "vertical-align:top}code{background:#f0f3f8;padding:1px 5px;border-radius:4px;"
        "font-size:12px}small{color:#68748a}</style>"
        "<h2>er-gateway · 多租户 ER 打分网关</h2>"
        "<table><tr><th>租户</th><th>模型</th><th>θ（cases 产物同源）</th>"
        f"<th>演示页</th><th>MaaS API</th></tr>{''.join(rows)}</table>"
        "<p><small>θ 与模型名逐租户从 build_tenant_cases 产物读取（不手调）；"
        "结构化输入按该租户 fieldmap 序列化，与训练同口径。"
        "并发护栏与错误口径见 scripts/er_gateway.py 文档串。</small></p>")
    return page.encode("utf-8")


def build_handler(gw: Gateway):
    """构造 HTTP handler（Gateway 已可注入 stub embed_fn，测试不碰网络）。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "er-gateway/1.0"

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except (ValueError, TypeError):
                raise GatewayError(400, "body 必须是 JSON") from None
            if not isinstance(req, dict):
                raise GatewayError(400, "body 必须是 JSON 对象") from None
            return req

        def _bearer(self) -> str | None:
            h = self.headers.get("Authorization") or ""
            return h[7:].strip() if h.startswith("Bearer ") else None

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Tenant")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path.startswith("/api/") or path.startswith("/v1/"):
                self._json(404, {"error": "unknown api endpoint"})
                return
            if path in ("", "/"):
                body = _landing(gw)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # /t/<name> 或 /t/<name>/demo.html → 该租户演示页
            parts = path.lstrip("/").split("/")
            if len(parts) == 2 and parts[0] == "t" and parts[1] in gw.tenants:
                t = gw.tenants[parts[1]]
                if not t.page:
                    self._json(404, {"error": f"租户「{t.name}」未配置 page（注册表加 page 路径）"})
                    return
                try:
                    page = t.page.read_bytes()
                except OSError as e:
                    self._json(503, {"error": f"租户页面读不了：{t.page}：{e}"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            self._json(404, {"error": f"unknown path: {self.path}"})

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            try:
                if path == "/api/score":                      # Febrl 主页兼容（默认租户）
                    req = self._body()
                    out = gw.score(None, req.get("a"), req.get("b"))
                elif path.startswith("/t/") and path.endswith("/api/score"):
                    name = path[3:-len("/api/score")]
                    if name not in gw.tenants:
                        raise GatewayError(404, f"未知租户「{name}」（可用：{sorted(gw.tenants)}）")
                    req = self._body()
                    out = gw.score(name, req.get("a"), req.get("b"))
                elif path in ("/v1/er/score", "/v1/er/embed", "/v1/er/resolve"):
                    req = self._body()
                    t = gw.tenant(req.get("tenant") or self.headers.get("X-Tenant"))
                    gw.check_key(t, self._bearer())
                    if path == "/v1/er/score":
                        out = gw.score(t.name, req.get("a"), req.get("b"))
                    elif path == "/v1/er/embed":
                        out = gw.embed_records(t.name, req.get("inputs"))
                    else:
                        out = gw.resolve(t.name, req.get("records"))
                else:
                    raise GatewayError(404, f"unknown api endpoint: {path}")
            except GatewayError as e:
                self._json(e.code, {"error": e.message})
                return
            self._json(200, out)

        def log_message(self, fmt, *args):
            if "/api/" in (args[0] if args else "") or "/v1/" in (args[0] if args else ""):
                super().log_message(fmt, *args)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                    help=f"租户注册表 JSON（默认 {DEFAULT_REGISTRY}，仓库外）")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token", default=None, help="默认取环境变量 EMB_TOKEN")
    ap.add_argument("--max-concurrent", type=int, default=8,
                    help="上游 embeddings 在途上限（bench 锚点 16 并发触 429；默认 8）")
    ap.add_argument("--url", default=DEFAULT_URL, help=argparse.SUPPRESS)  # 兼容占位
    ap.add_argument("--model", default=DEFAULT_MODEL, help=argparse.SUPPRESS)
    ap.add_argument("--open", action="store_true", help="启动即开浏览器（默认只打印地址）")
    args = ap.parse_args()

    try:
        tenants, default = load_registry(args.registry)
    except RegistryError as e:
        print(f"✗ 注册表不合法：\n{e}", file=sys.stderr)
        raise SystemExit(1) from None

    token, tok_src = load_token(args.token, os.environ.get("EMB_TOKEN"))
    gw = Gateway(tenants=tenants, token=token, default_tenant=default,
                 max_concurrent=args.max_concurrent)

    def say(msg): print(msg, flush=True)
    say("[er-gateway] 多租户 ER 打分网关")
    say(f"  registry = {args.registry}")
    for name in sorted(tenants):
        t = tenants[name]
        say(f"  tenant   {name:12s} θ={t.theta:.4f}  model={t.model}"
            f"  page={'/t/' + name + '/' if t.page else '—'}"
            f"{'  (default)' if name == default else ''}")
    if token:
        say(f"  token    = OK（{tok_src}）")
    else:
        say("  token    = OFF——在线打分会 503；写入 ~/.config/entity-arena/emb_token"
            "（chmod 600）或用 --token / EMB_TOKEN")
    say(f"  limit    = 上游在途 ≤ {args.max_concurrent}（超限 429 人话重试提示）")
    addr = f"http://{args.host}:{args.port}"
    say(f"  → 落地页 {addr}/  （租户演示页 /t/<name>/，API /v1/er/score|embed|resolve）")
    if args.open:
        import webbrowser
        webbrowser.open(addr + "/")

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), build_handler(gw))
    except OSError as e:
        say(f"  ✗ {args.host}:{args.port} 起不来（{e}）——多半是旧网关还在跑。")
        say(f"    先停旧的：lsof -ti tcp:{args.port} | xargs kill；或换端口 --port 8791")
        raise SystemExit(1) from e
    httpd.serve_forever()


def serve_for_tests(gw: Gateway, port: int = 0):
    """起一个临时实例（测试用；port=0 由系统分配）。返回 (server, port)。"""
    srv = ThreadingHTTPServer(("127.0.0.1", port), build_handler(gw))
    return srv, srv.server_address[1]


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
