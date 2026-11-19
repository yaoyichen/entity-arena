"""er-server 一体化服务：单进程双面（演示页 + 在线打分 + 客户控制台）的组合行为。

覆盖：
- 挂载拓扑：/ 出演示页（深链 query 照带）、/console/ 出控制台 SPA、
  /console（无斜杠）302 补斜杠且保留 ?theme= 查询；
- 前缀剥除委托：/console/api/*、/console/download/* 全部落到 er_console 原
  handler（state shape / 人话 400/404 原样），控制台 SPA 全相对路径契约
  （fetch('api/state')）在合并源下成立；
- 命名空间边界：根级 /api/* 只认 POST /api/score（play 面），其余 404 带
  「控制台在 /console/api/」的人话指路；
- /healthz 组件状态：页面 built / 在线打分 live+theta / 控制台 steps+train_phase；
- 在线打分透传（stub 注入，不碰网络）：有令牌 200；无令牌实例 503 人话、
  healthz 如实报 live=false（失败不静默）；OPTIONS 预检 204（play 面同一份）；
- 托管 MCP 挂载面（/mcp）：Bearer 鉴权边界、initialize→Mcp-Session-Id、
  GET 405/DELETE 200、healthz mcp 块（不带 key）、控制台 /api/state 回显
  （协议与工具层的深测在 tests/test_mcp_http.py）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from entity_arena.console import Workspace  # noqa: E402
from entity_arena import mcp_http  # noqa: E402
from er_server import serve_for_tests  # noqa: E402

DEMO_PAGE = b"<!DOCTYPE html><html><title>entity-arena demo</title></html>"


def stub_score(texts):
    return {"score": 0.5, "model": "stub", "theta": 0.9, "dims": 3}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _get(url):
    try:
        with _OPENER.open(url, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _get_json(url):
    code, _, body = _get(url)
    return code, json.loads(body)


def _post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _OPENER.open(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class _Base(unittest.TestCase):
    """两个实例共用：score_fn 有/无（有令牌/无令牌），页面临时文件不碰 build/。"""

    score_fn = staticmethod(stub_score)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.page = root / "demo.html"
        cls.page.write_bytes(DEMO_PAGE)
        cls.ws = Workspace(root / "workdir")
        cls.srv, cls.port = serve_for_tests(cls.score_fn, cls.page, cls.ws,
                                            label="Acme 试点")
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()


class TestRouting(_Base):
    """挂载拓扑与前缀委托（score_fn=stub）。"""

    def test_root_serves_demo_page(self):
        code, headers, body = _get(self.base + "/")
        self.assertEqual((code, body), (200, DEMO_PAGE))
        self.assertIn("text/html", headers.get("Content-Type", ""))

    def test_root_deep_link_query_passthrough(self):
        code, _, body = _get(self.base + "/?view=play&theme=dark")
        self.assertEqual((code, body), (200, DEMO_PAGE))   # 深链随便带（play 面契约）

    def test_console_spa_mounted_with_label(self):
        code, headers, body = _get(self.base + "/console/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = body.decode("utf-8")
        self.assertIn("ER Service Console", html)
        self.assertIn("Acme 试点", html)

    def test_console_redirect_keeps_query(self):
        code, headers, _ = _get(self.base + "/console?theme=dark")
        self.assertEqual(code, 302)
        self.assertEqual(headers.get("Location"), "/console/?theme=dark")

    def test_console_api_state_via_prefix(self):
        code, data = _get_json(self.base + "/console/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(data["label"], "Acme 试点")       # er_console /api/state 同一份
        self.assertIn("steps", data)
        self.assertIn("train_cfg", data)                   # HTTP 层注入的训练配置面

    def test_console_post_human_400(self):
        code, data = _post_json(self.base + "/console/api/proxy-score",
                                {"a": "nope", "b": "nada"})
        self.assertEqual(code, 400)
        self.assertTrue(data.get("errors"))                # 人话错误走 console 面

    def test_console_download_missing_package_human_404(self):
        code, data = _get_json(self.base + "/console/download/package.zip")
        self.assertEqual(code, 404)
        self.assertTrue(data.get("errors"))

    def test_root_api_namespace_boundary(self):
        code, _, body = _get(self.base + "/api/state")     # 根级没有控制台端点
        self.assertEqual(code, 404)
        self.assertIn(b"/console/api/", body)              # 人话指路
        code, data = _post_json(self.base + "/api/unknown", {})
        self.assertEqual(code, 404)                        # play 面人话 404

    def test_score_endpoint_stub(self):
        code, data = _post_json(self.base + "/api/score", {"a": "aa", "b": "bb"})
        self.assertEqual(code, 200)
        self.assertEqual(data["model"], "stub")

    def test_options_preflight(self):
        req = urllib.request.Request(self.base + "/api/score", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 204)

    def test_healthz(self):
        code, data = _get_json(self.base + "/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["demo_page"]["built"])
        self.assertTrue(data["live_score"]["live"])
        self.assertIn("theta", data["live_score"])
        steps = data["console"]["steps"]
        self.assertIn("upload", steps)                     # 五步键齐
        self.assertIn("deploy", steps)
        self.assertEqual(data["console"]["train_phase"], "idle")
        self.assertEqual(data["console"]["label"], "Acme 试点")


class TestNoToken(_Base):
    """无令牌实例：在线打分诚实 503，其余面不受影响（降级不静默）。"""

    score_fn = staticmethod(None)

    def test_score_503_human_message(self):
        code, data = _post_json(self.base + "/api/score", {"a": "aa", "b": "bb"})
        self.assertEqual(code, 503)
        self.assertIn("token", json.dumps(data).lower())

    def test_healthz_reports_live_false(self):
        code, data = _get_json(self.base + "/healthz")
        self.assertEqual(code, 200)
        self.assertFalse(data["live_score"]["live"])

    def test_console_still_up_without_token(self):
        code, _ = _get_json(self.base + "/console/api/state")
        self.assertEqual(code, 200)


class TestMcpMount(unittest.TestCase):
    """/mcp 挂载面：鉴权边界 + healthz/控制台回显（降级核足够，不拖 bundle fixture）。"""

    KEY = "ea-mount-test"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.page = root / "demo.html"
        cls.page.write_bytes(DEMO_PAGE)
        cls.ws = Workspace(root / "workdir")
        cls.ep = mcp_http.McpEndpoint(cls.KEY, None)   # 降级核：路由面已够
        cls.srv, cls.port = serve_for_tests(stub_score, cls.page, cls.ws,
                                            label="Mcp mount", mcp=cls.ep)
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def _mcp(self, payload, headers):
        req = urllib.request.Request(
            self.base + "/mcp", data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with _OPENER.open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _auth(self):
        return {"Authorization": f"Bearer {self.KEY}"}

    def test_unauthorized_401(self):
        code, _, body = self._mcp({"jsonrpc": "2.0", "id": 1,
                                   "method": "ping"}, {})
        self.assertEqual(code, 401)
        self.assertIn("key", json.loads(body)["error"])

    def test_initialize_over_http(self):
        code, headers, body = self._mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}}, self._auth())
        self.assertEqual(code, 200)
        self.assertTrue(headers.get("Mcp-Session-Id"))
        self.assertEqual(json.loads(body)["result"]["protocolVersion"],
                         "2025-06-18")

    def test_get_405_delete_200(self):
        req = urllib.request.Request(self.base + "/mcp", method="GET",
                                     headers=self._auth())
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 405)
        req = urllib.request.Request(self.base + "/mcp", method="DELETE",
                                     headers=self._auth())
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)

    def test_healthz_mcp_block_without_key(self):
        code, data = _get_json(self.base + "/healthz")
        self.assertEqual(code, 200)
        self.assertFalse(data["mcp"]["ready"])          # 降级核如实报
        self.assertTrue(data["mcp"]["auth"])
        self.assertNotIn(self.KEY, json.dumps(data))    # healthz 绝不带 key

    def test_console_state_echoes_mcp(self):
        code, data = _get_json(self.base + "/console/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(data["mcp"]["key"], self.KEY)  # ⑤ 部署页一行接入卡
        self.assertEqual(data["mcp"]["ready"], False)


if __name__ == "__main__":
    unittest.main()
