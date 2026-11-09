"""play_live_server（试玩台在线打分 sidecar）行为锁定。

不碰网络：score_fn 注入 stub；HTTP 层用真实 ThreadingHTTPServer + urllib 过一遍。
锁的是：同源转发的接口契约（打分/错误口径）、缺令牌的诚实降级（503 带人话）、
页面服务、预检放行。"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "play_live_server", ROOT / "scripts" / "play_live_server.py")
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

PAGE = ROOT / "build" / "demo.html"


def _post(url: str, payload) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class TestPlayLiveServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = lambda texts: {"score": 0.9371, "model": "stub-model",
                                  "theta": srv.theta(), "dims": 768}
        cls.server, cls.port = srv.serve_for_tests(cls.stub, PAGE)
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.offline_server, cls.offline_port = srv.serve_for_tests(None, PAGE)
        cls.offline_base = f"http://127.0.0.1:{cls.offline_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        threading.Thread(target=cls.offline_server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.offline_server.shutdown()

    def test_score_ok(self):
        code, out = _post(self.base + "/api/score", {"a": "name: a", "b": "name: b"})
        self.assertEqual(code, 200)
        self.assertAlmostEqual(out["score"], 0.9371)
        self.assertEqual(out["model"], "stub-model")
        self.assertGreater(out["theta"], 0.9)   # θ 与页面同源（open_cases.json）
        self.assertEqual(out["dims"], 768)

    def test_theta_matches_page_source(self):
        """θ 兜底逻辑：读不到 open_cases.json 也得回 0.9208（不静默崩溃）。"""
        self.assertIsInstance(srv.theta(), float)
        self.assertGreater(srv.theta(), 0.9)

    def test_rejects_bad_payload(self):
        for payload in ({}, {"a": "x"}, {"a": " ", "b": "y"}, {"a": 1, "b": 2}):
            code, out = _post(self.base + "/api/score", payload)
            self.assertEqual(code, 400, payload)
            self.assertIn("error", out)

    def test_no_token_honest_503(self):
        """缺 EMB_TOKEN：503 + 人话提示（页面照常可看、rid 对离线打分不受影响）。"""
        code, out = _post(self.offline_base + "/api/score", {"a": "x", "b": "y"})
        self.assertEqual(code, 503)
        self.assertIn("EMB_TOKEN", out["error"])

    def test_embed_failure_maps_502(self):
        def boom(texts):
            raise srv.ScoreUnavailable("部署服务连续失败：x")
        server, port = srv.serve_for_tests(boom, PAGE)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            code, out = _post(f"http://127.0.0.1:{port}/api/score", {"a": "x", "b": "y"})
            self.assertEqual(code, 502)
            self.assertIn("部署服务", out["error"])
        finally:
            server.shutdown()

    def test_serves_demo_page(self):
        """GET / 出演示页（sidecar 的存在意义：页面与接口同源，绕开网关无 CORS）。"""
        if not PAGE.exists():
            self.skipTest("build/demo.html 未构建")
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            body = r.read()
        self.assertEqual(r.status, 200)
        self.assertIn(b"id=\"view-play\"", body)

    def test_preflight_allowed(self):
        req = urllib.request.Request(self.base + "/api/score", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 204)
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")

    def test_unknown_api_404(self):
        req = urllib.request.Request(self.base + "/api/other",
                                     data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("should 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_live_score_factory_none_without_token(self):
        self.assertIsNone(srv.live_score("u", "m", None))

    def test_load_token_priority(self):
        """令牌解析：--token > EMB_TOKEN 环境变量 > 本机文件（仓库外）> OFF。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "emb_token"
            f.write_text("tok-file\n", encoding="utf-8")
            self.assertEqual(srv.load_token("cli", "env", f), ("cli", "--token"))
            self.assertEqual(srv.load_token(None, "env", f), ("env", "EMB_TOKEN 环境变量"))
            self.assertEqual(srv.load_token(None, None, f), ("tok-file", str(f)))
            blank = Path(d) / "blank"
            blank.write_text("   \n", encoding="utf-8")
            self.assertEqual(srv.load_token(None, None, blank), (None, None))
            self.assertEqual(srv.load_token(None, None, Path(d) / "missing"), (None, None))

    def test_ensure_page_detects_state(self):
        """一个命令全包的关键：页面缺失/输入更新要能被识别（这里只测探测，不真构建）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "demo.html"
            self.assertEqual(srv.ensure_page(missing, auto_build=False), "missing")
            fresh = Path(d) / "fresh.html"
            fresh.write_text("x", encoding="utf-8")
            time.sleep(0.01)
            # 刚写的新页面：构建输入（仓库里的旧文件）不应比它新
            self.assertEqual(srv.ensure_page(fresh, auto_build=False), "up to date")
            # 人为把一个输入改到未来 → 必须报过期
            future = Path(d) / "future.html"
            future.write_text("x", encoding="utf-8")
            import os as _os
            old = time.time() + 10
            _os.utime(srv.BUILD_INPUTS[0], (old, old))
            try:
                self.assertEqual(srv.ensure_page(future, auto_build=False)[:7], "inputs ")
            finally:
                _os.utime(srv.BUILD_INPUTS[0], None)  # 恢复当前时间，别污染仓库状态


if __name__ == "__main__":
    unittest.main()
