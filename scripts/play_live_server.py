#!/usr/bin/env python3
"""试玩台「直接输入」在线打分 sidecar：本地同源转发 → 部署的微调模型。

**日常演示只需这一条命令**（页面过期/缺失自动重建 → 起服务 → 自动开浏览器）：
  python3 scripts/play_live_server.py

为什么需要它（2026-11-09 实测）：平台网关（istio-envoy）没配 CORS——
浏览器直接 fetch 会被预检 401 / 响应无 Access-Control-Allow-Origin 双重掐死。
本服务把演示页与打分接口挂在同一源下：页面 fetch 相对路径 api/score，
零外链契约不破（demo.html 里没有任何 URL 字符串）。

参数：--port 8787 --model febrl3-er-gemma --url <deployed> --no-open（只起服务不弹窗）
     --no-build（缺/过期页面时也不自动重建，直接按现状服务）

令牌解析顺序（load_token）：--token > EMB_TOKEN 环境变量 >
本机文件 ~/.config/entity-arena/emb_token（chmod 600，仓库外——令牌绝不进
页面、不进仓库）。首次配置：
  mkdir -p ~/.config/entity-arena
  printf '%s' '<平台令牌>' > ~/.config/entity-arena/emb_token
  chmod 600 ~/.config/entity-arena/emb_token

接口：
  GET  /            → build/demo.html（非 /api 的 GET 一律回页面，深链 query 随便带）
  POST /api/score   → {"a":"...","b":"..."} → {"score":余弦,"model":...,"theta":...,"dims":...}
                      两条文本一次 embed 调用，input_type=document（与取样矩阵同口径）
缺令牌：页面照常可看、挑村记录对照常离线打分；「直接输入」提交会得到
503 与一句人话提示（失败不静默）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.embed_client import (  # noqa: E402
    DEFAULT_MODEL, DEFAULT_URL, EmbedError, cosine, embed, load_theta,
)

PAGE = ROOT / "build" / "demo.html"
TOKEN_FILE = Path.home() / ".config" / "entity-arena" / "emb_token"
# 页面的构建输入：任一比 demo.html 新 → 过期，自动重建（一个命令全包的关键）
BUILD_INPUTS = [
    ROOT / "scripts" / "build_demo.py",
    ROOT / "src" / "entity_arena" / "page.py",
    ROOT / "src" / "entity_arena" / "theme.py",
    *sorted((ROOT / "data").glob("*.json")),
    ROOT / "data" / "records.jsonl",
]
# fresh clone 引导（W5）：data/ 是生成物（gitignore），三个入口产物都能从
# 已入库输入确定性重建（seed 42，~40s）。inputs 是该步的已入库前置——缺席即
# 跳过（village_scores 缺席的降级路径交给 build_demo 原有人话提示，不硬跑）。
DATA_STEPS = (
    (ROOT / "data" / "network.json",                 # 合成轨全量（make_mock_data 一并产出）
     (ROOT / "scripts" / "make_mock_data.py",), ()),
    (ROOT / "data" / "open_benchmark.json",
     (ROOT / "scripts" / "update_open_benchmark.py",),
     (ROOT / "data_open" / "eval_reports",)),
    (ROOT / "data" / "open_cases.json",
     (ROOT / "scripts" / "build_open_cases.py",),
     (ROOT / "data_open" / "village_scores.json",)),
)


def ensure_data(auto_build: bool = True, steps=DATA_STEPS) -> str | None:
    """fresh clone 引导：data/ 缺失 → 按序三步确定性重建。

    只在「产物缺失且已入库输入齐全」时执行对应步骤；返回 None=无需重建，
    否则返回状态描述（steps 参数供测试注入，行为同 score_fn 的 stub 模式）。
    """
    todo = [(out, cmds) for out, cmds, inputs in steps
            if not out.exists() and all(p.exists() for p in inputs)]
    if not todo:
        return None
    if not auto_build:
        return "data missing: " + ", ".join(o.name for o, _ in todo)
    import subprocess
    for out, cmds in todo:
        for cmd in cmds:
            print(f"[entity-arena] {out.name} 缺失 → {Path(cmd).name} 确定性重建…", flush=True)
            subprocess.run([sys.executable, str(cmd)], check=True)
    return "rebuilt data (" + "+".join(o.name for o, _ in todo) + ")"


def ensure_page(page: Path, auto_build: bool = True) -> str:
    """demo.html 缺失或构建输入更新过 → 自动重建（免「先 build 再 serve」两步）。

    fresh clone 首跑先走 ensure_data 补 data/（确定性三步）。返回状态描述
    （"up to date" / "missing" / "rebuilt (…)" / "data missing: …"）；构建失败
    会 check=True 直接抛错——失败不静默。
    """
    data_state = ensure_data(auto_build=auto_build)
    if data_state is not None and not auto_build:
        return data_state  # 数据都没齐，页面状态无从谈起——如实报告
    if page.exists():
        stale = [p for p in BUILD_INPUTS
                 if p.exists() and p.stat().st_mtime > page.stat().st_mtime]
        if not stale:
            return "up to date"
        reason = f"inputs newer: {stale[0].name}"
    else:
        reason = "missing"
    if not auto_build:
        return reason
    import subprocess
    print(f"[entity-arena] demo.html {reason} → 自动重建…", flush=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_demo.py"),
                    "--out", str(page)], check=True)
    return f"rebuilt ({reason})"


def load_token(cli: str | None = None, env: str | None = None, path: Path | None = None):
    """令牌解析：--token > EMB_TOKEN 环境变量 > 本机令牌文件（仓库外）。

    返回 (token, 来源描述)；三处都没有 → (None, None)（诚实降级为 OFF）。
    """
    if cli:
        return cli, "--token"
    if env:
        return env, "EMB_TOKEN 环境变量"
    p = path or TOKEN_FILE
    try:
        tok = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if tok:
        return tok, str(p)
    return None, None


def theta() -> float:
    """与 live_pair_score / er_mcp_server 同源（单源 embed_client.load_theta）：
    优先用最新构建产物里的 θ，缺席/坏损兜底 THETA_FALLBACK。"""
    return load_theta(ROOT / "data" / "open_cases.json")


class ScoreUnavailable(RuntimeError):
    """打分不可用（没令牌 / 服务不可达）→ 503/502，带人话原因。"""


def live_score(url: str, model: str, token: str | None):
    """返回 score_fn(texts)（部署服务实时余弦）；没令牌时返回 None（诚实降级）。"""
    if not token:
        return None

    def score_fn(texts: list[str]) -> dict:
        try:
            vec = embed(url, model, token, texts)
        except EmbedError as e:
            raise ScoreUnavailable(f"部署服务连续失败：{e}") from e
        return {"score": cosine(vec[0], vec[1]), "model": model,
                "theta": theta(), "dims": len(vec[0])}
    return score_fn


def build_handler(score_fn, page_path: Path):
    """构造 HTTP handler（score_fn 可注入 stub，测试不碰网络）。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "entity-arena-play/1.0"

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")  # 宽容本地任意挂载方式
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # 预检放行（页面若经反向代理挂别处也能用）
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if self.path.startswith("/api/"):
                self._json(404, {"error": "unknown api endpoint"})
                return
            try:
                page = page_path.read_bytes()
            except OSError:
                self._json(503, {"error": f"demo page not built yet: {page_path} "
                                          "(run scripts/build_demo.py first)"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            if self.path.split("?")[0] != "/api/score":
                self._json(404, {"error": "unknown api endpoint"})
                return
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except (ValueError, TypeError):
                self._json(400, {"error": "body must be JSON"})
                return
            if not isinstance(req, dict):      # 合法 JSON 非对象（如 [1,2]）：人话 400
                self._json(400, {"error": "body must be a JSON object"})
                return
            a, b = req.get("a"), req.get("b")
            if not isinstance(a, str) or not isinstance(b, str) or not a.strip() or not b.strip():
                self._json(400, {"error": "both a and b must be non-empty strings"})
                return
            if score_fn is None:
                self._json(503, {"error": "live scoring is OFF: no token found. "
                                          f"Put the platform token in {TOKEN_FILE} "
                                          "(chmod 600), or pass --token / EMB_TOKEN. "
                                          "Village-rid pairs keep working offline in the page."})
                return
            try:
                out = score_fn([a, b])
            except ScoreUnavailable as e:
                self._json(502, {"error": str(e)})
                return
            self._json(200, out)

        def log_message(self, fmt, *args):  # 安静一点：只记接口调用，页面请求不刷屏
            if "/api/" in (args[0] if args else ""):
                super().log_message(fmt, *args)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--token", default=None, help="默认取环境变量 EMB_TOKEN")
    ap.add_argument("--page", type=Path, default=PAGE, help="演示页路径（默认 build/demo.html）")
    ap.add_argument("--no-open", dest="open", action="store_false",
                    help="只起服务不弹浏览器（默认启动即自动打开页面）")
    ap.add_argument("--no-build", dest="build", action="store_false",
                    help="不自动重建缺失/过期的 demo.html（按现状直接服务）")
    args = ap.parse_args()

    token, tok_src = load_token(args.token, os.environ.get("EMB_TOKEN"))
    score_fn = live_score(args.url, args.model, token)
    addr = f"http://{args.host}:{args.port}"
    def say(msg): print(msg, flush=True)
    say(f"[entity-arena] 试玩台在线打分 sidecar（唯一要起的进程：页面+打分都在这）")
    page_state = ensure_page(args.page, auto_build=args.build)
    say(f"  page   → {addr}   ← 从这里打开演示页（试玩台「直接输入」即可实时打分）")
    say(f"  page   = {args.page}（{page_state}）")
    say(f"  score  = {'live: ' + args.model + ' @ 部署服务（令牌：' + tok_src + '）' if score_fn else 'OFF（没找到令牌：写入 ' + str(TOKEN_FILE) + ' 或用 --token / EMB_TOKEN）'}")
    say(f"  theta  = {theta():.5f}（与页面/全量矩阵同源）")
    if args.open:
        webbrowser.open(addr)
    try:
        httpd = ThreadingHTTPServer((args.host, args.port),
                                    build_handler(score_fn, args.page))
    except OSError as e:
        say(f"  ✗ {args.host}:{args.port} 起不来（{e}）——多半是旧 sidecar 还在跑。")
        say(f"    先停旧的：lsof -ti tcp:{args.port} | xargs kill；或换端口 --port 8788")
        raise SystemExit(1) from e
    httpd.serve_forever()


def serve_for_tests(score_fn, page_path: Path, port: int = 0):
    """起一个临时实例（测试用；port=0 由系统分配）。返回 (server, port)。"""
    srv = ThreadingHTTPServer(("127.0.0.1", port), build_handler(score_fn, page_path))
    if port == 0:
        return srv, srv.server_address[1]
    return srv, port


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
