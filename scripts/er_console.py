#!/usr/bin/env python3
"""er-console：客户自助服务控制台（W6/W7，单进程，纯标准库，同源零外链）。

给客户看的干净界面——① 数据（上传+初版展示同页）→ ③ 模型训练 →
④ 效果展示 → ⑤ 服务部署——能力展示走 play_live_server.py，互不掺和。
逻辑在 src/entity_arena/console.py（Workspace）+ console_train.py（Trainer：
W7 直连平台编排）；本文件只是薄 HTTP 壳（模式同 er_gateway.py）。

用法：
  python3 scripts/er_console.py --workdir .tmp/console_acme --label "Acme pilot"
  python3 scripts/er_console.py --workdir .tmp/console_acme \
      --api-url http://127.0.0.1:8790/v1/er/score --api-key '<发给客户的静态键>'
  # W7 直连平台训练（缺令牌自动回落人工模式，行为不变）：
  TRAIN_TOKEN='Bearer …' python3 scripts/er_console.py --workdir … --label …

工作区布局（W7 直连模式下 ③ 自动生成产物；人工模式操作员拷入，④⑤ 都自动点亮）：
  <workdir>/uploads/…            客户上传原件（records/fieldmap/pairs）
  <workdir>/package/…            intake 校验 + 训练包（run_intake 产物）
  <workdir>/package.zip          GET /download/package.zip 按需确定性打包
  <workdir>/.train/state.json    平台任务指针（job_id/服务地址等外部状态）
  <workdir>/cases.json (+demo.html+data/)  build_tenant_cases 产物（④ 效果）
  <workdir>/mcp_bundle/…         MCP 交付七件套（⑤ 部署）

路由（错误全人话 JSON，绝不静默）：
  GET  /                     → 控制台 SPA（theme.py 双主题 token）
  GET  /api/state            → 步骤状态 + intake 报告 + 初版预览 + 效果数字 + 训练配置面
  GET  /api/network          → 网络三态数据（原始/基线代理/微调，来自 cases.network）
  GET  /api/sample           → Febrl 示例数据上传载荷（一键载入用）
  GET  /api/train[?models=1] → 训练实时状态（?models=1 附平台基模列表）
  POST /api/train/start      → 上传包→平台开训 {base_model, output_name?, serving_name?}
  POST /api/train/deploy     → 部署训练好的模型
  POST /api/train/build      → 全对打分→θ 扫描→cases/demo/MCP 落工作区（④⑤ 点亮）
  POST /api/upload           → {records:{name,b64}, fieldmap:{…}, pairs?:{…}}
                               → 200 {report} | 400 {errors:[人话…]}
  POST /api/proxy-score      → {a:rid, b:rid} → 训练前基线代理分（非模型，页面标注）
  GET  /download/package.zip → 确定性训练上传包（可直接 upload_dataset）
  GET  /download/mcp/<file>  → MCP 交付包内文件（防路径穿越）
  GET  /demo/                → 工作区 demo.html（build_tenant_cases --demo-dir 产物）

令牌边界（三层，绝不落盘/进页面/进工作区）：
- --api-key 只是展示给该租户客户的静态键（er_gateway 注册表同源）；
- 平台训练令牌 --train-token > TRAIN_TOKEN > EMB_TOKEN > ~/.claude.json 的
  inf-retriever Authorization（本机已配好，启动打印来源）；用于 MCP 上传/开训/
  部署 与取样向量的 EMB_TOKEN 子进程环境——只活在进程内存与环境变量里。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena import intake, theme  # noqa: E402
from entity_arena.console import (ConsoleError, Workspace, render_console,  # noqa: E402
                                  sample_files)
from entity_arena.console_train import Trainer  # noqa: E402
from entity_arena.train_client import DEFAULT_URL as TRAIN_URL, TrainClient  # noqa: E402

DEFAULT_WORKDIR = ROOT / ".tmp" / "er_console"


def load_train_token(cli: str | None = None) -> tuple[str | None, str | None]:
    """平台训练令牌：--train-token > TRAIN_TOKEN > EMB_TOKEN > ~/.claude.json。

    与 play_live_server.load_token 同风格（返回 (token, 来源)），但多一级
    ~/.claude.json 的 inf-retriever Authorization（本机操作台已配好的连接器令牌）。
    """
    if cli:
        return cli, "--train-token"
    env = os.environ.get("TRAIN_TOKEN") or os.environ.get("EMB_TOKEN")
    if env:
        return env, "TRAIN_TOKEN/EMB_TOKEN 环境变量"
    p = Path.home() / ".claude.json"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    srv = (cfg.get("mcpServers") or {}).get("inf-retriever")
    if not srv:
        proj = (cfg.get("projects") or {}).get(str(ROOT), {})
        srv = (proj.get("mcpServers") or {}).get("inf-retriever")
    tok = ((srv or {}).get("headers") or {}).get("Authorization")
    if tok:
        return tok, "~/.claude.json（inf-retriever 连接器）"
    return None, None


# 取样向量的 EMB_TOKEN 三级解析复用 play_live_server（不抄一份，同 er_gateway）
_spec = importlib.util.spec_from_file_location(
    "play_live_server", ROOT / "scripts" / "play_live_server.py")
_pls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pls)
load_emb_token = _pls.load_token


def build_handler(ws: Workspace, label: str, api_url: str | None = None,
                  api_key: str | None = None, trainer: Trainer | None = None):
    """构造 HTTP handler（Workspace 是纯文件推导，测试给临时目录即可）。"""
    page = render_console(theme.CSS, label).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "er-console/1.0"

        # ---- 基件
        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except (ValueError, TypeError):
                raise ConsoleError(["body 必须是 JSON"]) from None
            if not isinstance(req, dict):
                raise ConsoleError(["body 必须是 JSON 对象"]) from None
            return req

        def _bytes(self, data: bytes, ctype: str, download: str | None = None) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)

        # ---- GET
        def do_GET(self):
            path = self.path.split("?")[0]
            try:
                if path in ("", "/"):
                    self._bytes(page, "text/html; charset=utf-8")
                elif path == "/api/state":
                    out = ws.state(label, api_url, api_key)
                    out["train_cfg"] = (trainer.config() if trainer
                                        else {"can_train": False, "can_embed": False})
                    out["train"] = (trainer.file_state() if trainer
                                    else {"phase": "idle"})
                    self._json(200, out)
                elif path == "/api/network":
                    self._json(200, ws.network())
                elif path == "/api/sample":
                    self._json(200, sample_files())
                elif path == "/api/train":
                    if trainer is None:
                        self._json(200, {"phase": "idle", "can_train": False})
                    else:
                        out = trainer.status()
                        if "models=1" in self.path:
                            out["models"] = [m.get("name")
                                             for m in trainer.models() if m.get("name")]
                        self._json(200, out)
                elif path == "/demo" or path == "/demo/":
                    demo = ws.root / "demo.html"
                    if not demo.exists():
                        raise ConsoleError(
                            ["工作区还没有 demo.html——先跑 build_tenant_cases.py "
                             "--demo-dir（或把产物拷进工作区）"])
                    self._bytes(demo.read_bytes(), "text/html; charset=utf-8")
                elif path == "/download/package.zip":
                    if not (ws.root / "package" / "intake_report.json").exists():
                        raise ConsoleError(
                            ["训练包尚未构建——先在 ① 上传记录与字段角色表"])
                    zp = intake.zip_package(ws.root / "package")
                    self._bytes(zp.read_bytes(), "application/zip",
                                download="package.zip")
                elif path.startswith("/download/mcp/"):
                    name = path[len("/download/mcp/"):]
                    bundle = (ws.root / "mcp_bundle").resolve()
                    target = (bundle / name).resolve()
                    if not str(target).startswith(str(bundle) + "/") or "/" in name:
                        raise ConsoleError([f"非法文件名：{name!r}"])
                    if not target.is_file():
                        raise ConsoleError(
                            [f"MCP 交付包里没有 {name!r}（先跑 build_tenant_cases.py "
                             "--mcp-dir，或把交付包拷进工作区 mcp_bundle/）"])
                    ctype = ("text/markdown; charset=utf-8" if name.endswith(".md")
                             else "application/octet-stream")
                    self._bytes(target.read_bytes(), ctype, download=name)
                else:
                    raise ConsoleError([f"unknown path: {self.path}"])
            except ConsoleError as e:
                self._json(404, {"errors": e.errors})
            except Exception as e:            # 兜底不裸崩：500 人话 JSON + 服务端打栈
                traceback.print_exc()
                self._json(500, {"errors": [f"服务器内部错误（{type(e).__name__}: {e}）"
                                            "——详情见控制台日志"]})

        # ---- POST
        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            try:
                if path == "/api/upload":
                    req = self._body()
                    report = ws.upload(req.get("records"), req.get("fieldmap"),
                                       req.get("pairs"))
                    self._json(200, {"report": report})
                elif path == "/api/proxy-score":
                    req = self._body()
                    out = ws.proxy_score(str(req.get("a") or ""),
                                         str(req.get("b") or ""))
                    self._json(200, out)
                elif path == "/api/train/start":
                    if trainer is None:
                        raise ConsoleError(["平台训练未配置（缺令牌）——走人工模式下载 zip"])
                    req = self._body()
                    out = trainer.start(str(req.get("base_model") or ""),
                                        req.get("output_name"),
                                        req.get("serving_name"))
                    self._json(200, out)
                elif path == "/api/train/deploy":
                    if trainer is None:
                        raise ConsoleError(["平台训练未配置（缺令牌）——无法自动部署"])
                    self._json(200, trainer.deploy())
                elif path == "/api/train/build":
                    if trainer is None:
                        raise ConsoleError(["平台训练未配置（缺令牌）——无法自动生成效果数据"])
                    self._json(200, trainer.build())
                else:
                    raise ConsoleError([f"unknown api endpoint: {path}"])
            except ConsoleError as e:
                self._json(400, {"errors": e.errors})
            except Exception as e:            # 兜底不裸崩：500 人话 JSON + 服务端打栈
                traceback.print_exc()
                self._json(500, {"errors": [f"服务器内部错误（{type(e).__name__}: {e}）"
                                            "——详情见控制台日志"]})

        def log_message(self, fmt, *args):
            if "/api/" in (args[0] if args else ""):
                super().log_message(fmt, *args)

    return Handler


def serve_for_tests(ws: Workspace, label: str = "test", port: int = 0,
                    api_url: str | None = None, api_key: str | None = None,
                    trainer: Trainer | None = None):
    """起一个临时实例（测试用；port=0 由系统分配）。返回 (server, port)。"""
    srv = ThreadingHTTPServer(
        ("127.0.0.1", port),
        build_handler(ws, label, api_url=api_url, api_key=api_key, trainer=trainer))
    return srv, srv.server_address[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR,
                    help=f"客户工作区目录（默认 {DEFAULT_WORKDIR}，仓库外/忽略皆可）")
    ap.add_argument("--label", default="ER workspace",
                    help="顶栏工作区名称（展示用；也作平台任务/输出名的默认前缀）")
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--api-url", default=None,
                    help="⑤ 部署页展示的网关打分端点（如 http://host:8790/v1/er/score）")
    ap.add_argument("--api-key", default=None,
                    help="⑤ 部署页展示的租户静态 key（er_gateway 注册表同源；"
                         "绝不放平台 EMB_TOKEN）")
    ap.add_argument("--train-url", default=TRAIN_URL,
                    help="训练平台 MCP 端点（默认 inf-retriever）")
    ap.add_argument("--train-token", default=None,
                    help="平台训练令牌（默认 TRAIN_TOKEN > EMB_TOKEN > ~/.claude.json；"
                         "只进进程内存，绝不落盘/进页面）")
    ap.add_argument("--no-train", action="store_true",
                    help="禁用直连训练（回到 W6 人工模式）")
    ap.add_argument("--open", action="store_true", help="启动即开浏览器")
    args = ap.parse_args()

    ws = Workspace(args.workdir)
    addr = f"http://{args.host}:{args.port}"

    def say(msg): print(msg, flush=True)

    trainer = None
    if not args.no_train:
        tok, src = load_train_token(args.train_token)
        emb_tok, emb_src = load_emb_token(None, os.environ.get("EMB_TOKEN"))
        if tok:
            trainer = Trainer(args.workdir, TrainClient(args.train_url, tok),
                              token=emb_tok or None, label=args.label)
            say("[er-console] 客户自助服务控制台（W7 直连平台训练）")
            say(f"  train    = {args.train_url}（令牌来源：{src}）")
            if emb_tok:
                say(f"  embed    = 取样向量令牌来源：{emb_src}")
            else:
                say("  embed    = ⚠ 没找到取样令牌（EMB_TOKEN）——③ 的「生成效果"
                    "数据」一步会提示补令牌，其余不受影响")
        else:
            say("[er-console] 客户自助服务控制台（人工模式：未找到平台令牌，"
                "③ 走下载 zip + 操作员中介）")
    else:
        say("[er-console] 客户自助服务控制台（人工模式：--no-train）")

    say(f"  workdir  = {args.workdir.resolve()}")
    st = {s["key"]: s["state"] for s in ws.steps()}
    say("  steps    = " + " · ".join(f"{k}:{st[k]}" for k in
                                     ("upload", "preview", "train", "effect", "deploy")))
    tr = trainer.file_state() if trainer else {"phase": "idle"}
    if tr.get("phase") not in (None, "idle"):
        say(f"  phase    = {tr.get('phase')}"
            + (f" · {tr.get('output_name')}" if tr.get("output_name") else ""))
    if args.api_url:
        say(f"  api      = {args.api_url}（部署页展示）")
    say(f"  → 控制台 {addr}/  （数据 /api/upload，训练 /api/train/*，"
        "训练包 /download/package.zip）")
    if args.open:
        import webbrowser
        webbrowser.open(addr + "/")

    try:
        httpd = ThreadingHTTPServer((args.host, args.port),
                                    build_handler(ws, args.label,
                                                  api_url=args.api_url,
                                                  api_key=args.api_key,
                                                  trainer=trainer))
    except OSError as e:
        say(f"  ✗ {args.host}:{args.port} 起不来（{e}）——多半是旧控制台还在跑。")
        say(f"    先停旧的：lsof -ti tcp:{args.port} | xargs kill；或换端口 --port 8802")
        raise SystemExit(1) from e
    httpd.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
