#!/usr/bin/env python3
"""er-server：一体化产品服务（单进程双面，纯标准库，同源零外链）。

对外只起这一个进程——能力演示与客户工作台同源挂载，一个基地址交给客户：

  python3 scripts/er_server.py                                     # 开箱即用
  python3 scripts/er_server.py --workdir .tmp/console_acme --label "Acme pilot"

fresh clone 零准备：启动先走 play_live_server 的 ensure_data/ensure_page
（data/ 缺失三步确定性重建 → demo.html 缺失/过期自动构建），再起服务。

路由（两个既有薄壳 importlib 复用，逻辑零拷贝——同 er_gateway 复用
play_live_server.load_token 的先例）：
  GET  /            → build/demo.html 能力演示页（play_live_server 同一面；
                      非 /api、非 /console 的 GET 一律回页面，深链 ?view= 随便带）
  POST /api/score   → 部署模型实时余弦打分（play_live_server 同一 sidecar 语义）
  GET  /console/    → 客户控制台 SPA（er_console 同一面，五步服务流程）
  ANY  /console/…   → 控制台全部端点。SPA 的 fetch/href 全是相对路径
                      （fetch('api/state')、href="download/package.zip"），
                      服务端把 /console 前缀剥掉再委托原 handler——
                      er_console.py 与 play_live_server.py 两个文件零改动
  GET  /healthz     → 组件状态 JSON（页面 / 在线打分 / 控制台步骤 / 训练 phase）

分界约定不变：能力展示归 play 面、客户五步服务面归 console 面；本文件只是
把两个面装进同一进程同一端口（模式同 er_gateway 的「单进程三合一」）。

安全边界：默认只绑 127.0.0.1。控制台接口无鉴权（/console/api/train/start
会花 GPU）——跨机器暴露必须前置带鉴权的反向代理；绑非环回地址时启动会
大声警告。令牌边界与两个薄壳各自相同：打分令牌 > 训练令牌绝不进页面/仓库。

参数：--port 8788 --host 127.0.0.1 --no-open；演示面 --url/--model/--token/
--page/--no-build（同 play_live_server）；控制台面 --workdir/--label/--api-url/
--api-key/--train-url/--train-token/--no-train（同 er_console）。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.console import Workspace  # noqa: E402
from entity_arena.embed_client import DEFAULT_MODEL, DEFAULT_URL  # noqa: E402
from entity_arena.train_client import DEFAULT_URL as TRAIN_URL  # noqa: E402

DEFAULT_PORT = 8788
CONSOLE_PREFIX = "/console"


def _load_sibling(name: str):
    """importlib 载入 scripts/ 下的既有薄壳：逻辑单源，绝不抄一份。"""
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_pls = _load_sibling("play_live_server")   # ensure_page/load_token/live_score/build_handler
_con = _load_sibling("er_console")         # setup_trainer/build_handler


def build_handler(score_fn, page_path: Path, ws: Workspace, label: str,
                  api_url: str | None = None, api_key: str | None = None,
                  trainer=None):
    """构造一体化 handler：play 面 + console 面两个 Handler 类多继承，按路径分发。

    控制台整面挂 /console/ 前缀——SPA 全相对路径，服务端剥前缀后委托原
    handler；两个薄壳的行为（人话错误、CORS 预检、日志口径）原样保留。
    """
    play_cls = _pls.build_handler(score_fn, page_path)
    con_cls = _con.build_handler(ws, label, api_url=api_url, api_key=api_key,
                                 trainer=trainer)

    class Handler(play_cls, con_cls):
        # MRO = Handler → play → console → BaseHTTPRequestHandler：
        # _json/_body/_bytes 等基件按面各自继承，签名同构无冲突
        server_version = "er-server/1.0"

        def _strip(self) -> None:
            # "/console/api/state?models=1" → "/api/state?models=1"（查询串保留）
            self.path = self.path[len(CONSOLE_PREFIX):] or "/"

        def _redirect_console(self) -> None:
            q = self.path.split("?", 1)
            loc = CONSOLE_PREFIX + "/" + (("?" + q[1]) if len(q) > 1 else "")
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _healthz(self) -> None:
            try:
                payload = {
                    "service": "er-server", "ok": True,
                    "demo_page": {"path": str(page_path),
                                  "built": page_path.exists()},
                    "live_score": {"live": score_fn is not None,
                                   "theta": _pls.theta()},
                    "console": {
                        "label": label, "workdir": str(ws.root),
                        "steps": {s["key"]: s["state"] for s in ws.steps()},
                        "train_phase": ((trainer.file_state() or {}).get("phase")
                                        if trainer else "idle")},
                }
                self._json(200, payload)
            except Exception as e:   # healthz 自己也不能裸崩：500 人话 JSON
                self._json(500, {"ok": False,
                                 "error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/healthz":
                self._healthz()
            elif path == CONSOLE_PREFIX:
                self._redirect_console()
            elif path.startswith(CONSOLE_PREFIX + "/"):
                self._strip()
                con_cls.do_GET(self)
            elif path.startswith("/api/"):
                # 根级只有 POST /api/score（play 面）；指路到控制台命名空间
                self._json(404, {"error": "unknown api endpoint — demo scoring "
                                          "is POST /api/score; console APIs "
                                          "live under /console/api/"})
            else:
                play_cls.do_GET(self)

        def do_POST(self):
            if self.path.split("?")[0].startswith(CONSOLE_PREFIX + "/"):
                self._strip()
                con_cls.do_POST(self)
            else:
                # /api/score 在线打分；其余 POST 回 play 面的人话 404
                play_cls.do_POST(self)

        def do_OPTIONS(self):
            play_cls.do_OPTIONS(self)   # /api/score 预检放行（play 面同一份）

        def log_message(self, fmt, *args):
            line = args[0] if args else ""
            if "/api/" in line:         # 安静一点：只记接口调用（同两个薄壳）
                super().log_message(fmt, *args)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # ---- 共用
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", dest="open", action="store_false",
                    help="只起服务不弹浏览器（默认启动即打开演示页）")
    # ---- 演示面（同 play_live_server）
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--token", default=None, help="默认取环境变量 EMB_TOKEN")
    ap.add_argument("--page", type=Path, default=_pls.PAGE,
                    help="演示页路径（默认 build/demo.html）")
    ap.add_argument("--no-build", dest="build", action="store_false",
                    help="不自动重建缺失/过期的 demo.html（按现状直接服务）")
    # ---- 控制台面（同 er_console）
    ap.add_argument("--workdir", type=Path, default=_con.DEFAULT_WORKDIR,
                    help=f"客户工作区目录（默认 {_con.DEFAULT_WORKDIR}）")
    ap.add_argument("--label", default="ER workspace",
                    help="顶栏工作区名称（也作平台任务/输出名的默认前缀）")
    ap.add_argument("--api-url", default=None,
                    help="⑤ 部署页展示的网关打分端点（如 http://host:8790/v1/er/score）")
    ap.add_argument("--api-key", default=None,
                    help="⑤ 部署页展示的租户静态 key（绝不放平台 EMB_TOKEN）")
    ap.add_argument("--train-url", default=TRAIN_URL,
                    help="训练平台 MCP 端点（默认 inf-retriever）")
    ap.add_argument("--train-token", default=None,
                    help="平台训练令牌（默认 TRAIN_TOKEN > EMB_TOKEN > ~/.claude.json）")
    ap.add_argument("--no-train", action="store_true",
                    help="禁用直连训练（控制台 ③ 回到人工模式）")
    args = ap.parse_args()

    def say(msg): print(msg, flush=True)
    addr = f"http://{args.host}:{args.port}"
    say("[er-server] entity-arena 一体化产品服务（单进程：演示页 + 在线打分 + 客户控制台）")

    # 演示面：fresh clone 数据引导 + 页面自动重建 + 令牌（全部复用 play_live_server）
    page_state = _pls.ensure_page(args.page, auto_build=args.build)
    token, tok_src = _pls.load_token(args.token, os.environ.get("EMB_TOKEN"))
    score_fn = _pls.live_score(args.url, args.model, token)

    # 控制台面：工作区 + 直连训练接线（er_console.setup_trainer 同一份）
    ws = Workspace(args.workdir)
    trainer, tr_lines = _con.setup_trainer(
        args.workdir, args.label, args.train_url,
        train_token=args.train_token, no_train=args.no_train)

    say(f"  演示页   → {addr}/            ← 能力展示（试玩台「直接输入」实时打分）")
    say(f"  控制台   → {addr}{CONSOLE_PREFIX}/    ← 客户工作台（数据→训练→效果→部署）")
    say(f"  健康检查 → {addr}/healthz")
    say(f"  page    = {args.page}（{page_state}）")
    say("  score   = " + (f"live: {args.model} @ 部署服务（令牌：{tok_src}）"
                         if score_fn else
                         "OFF（没找到令牌：写入 ~/.config/entity-arena/emb_token "
                         "或用 --token / EMB_TOKEN）"))
    say(f"  theta   = {_pls.theta():.5f}（与页面/全量矩阵同源）")
    say(f"  workdir = {args.workdir.resolve()}")
    st = {s["key"]: s["state"] for s in ws.steps()}
    say("  steps   = " + " · ".join(f"{k}:{st[k]}" for k in
                                    ("upload", "preview", "train", "effect", "deploy")))
    say("  train   = " + tr_lines[0])
    for ln in tr_lines[1:]:
        say(f"  {ln}")
    if args.api_url:
        say(f"  api     = {args.api_url}（控制台 ⑤ 部署页展示）")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        say(f"  ⚠ 对外绑定 {args.host}：控制台接口无鉴权，/console/api/train/start 会花 GPU——")
        say("    跨机器暴露必须前置带鉴权的反向代理（详见 scripts/er_console.py 头注）")

    if args.open:
        webbrowser.open(addr + "/")
    try:
        httpd = ThreadingHTTPServer(
            (args.host, args.port),
            build_handler(score_fn, args.page, ws, args.label,
                          api_url=args.api_url, api_key=args.api_key,
                          trainer=trainer))
    except OSError as e:
        say(f"  ✗ {args.host}:{args.port} 起不来（{e}）——多半是旧服务还在跑。")
        say(f"    先停旧的：lsof -ti tcp:{args.port} | xargs kill；或换端口 --port {args.port + 1}")
        raise SystemExit(1) from e
    httpd.serve_forever()


def serve_for_tests(score_fn, page_path: Path, ws: Workspace, label: str = "test",
                    port: int = 0, api_url: str | None = None,
                    api_key: str | None = None, trainer=None):
    """起一个临时实例（测试用；port=0 由系统分配）。返回 (server, port)。"""
    srv = ThreadingHTTPServer(
        ("127.0.0.1", port),
        build_handler(score_fn, page_path, ws, label, api_url=api_url,
                      api_key=api_key, trainer=trainer))
    return srv, srv.server_address[1]


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
