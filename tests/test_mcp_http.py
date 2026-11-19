"""托管 MCP-over-HTTP（src/entity_arena/mcp_http.py）：鉴权 + 协议 + 工具层复用。

不碰网络、不起 HTTP 服务：McpEndpoint.handle() 收 (method, headers, body)
回 (status, headers, body)，直接对话。工具层来自真实交付包 fixture
（build_tenant_cases --mcp-dir，同 test_mcp_delivery 单一事实来源）——
离线矩阵判定与 stdio 面/页面试玩台天然同源。HTTP 挂载面的行为
（路由/healthz/命名空间）在 tests/test_er_server.py 锁定。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import test_tenant_cases as tt  # noqa: E402  复用 fixture（单一事实来源）

from entity_arena import mcp_http  # noqa: E402

KEY = "ea-unit-test-key"


class Headers(dict):
    """headers 替身：只用到 get（生产侧是 email.message.Message，大小写不敏感）。"""


class HostedBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-mcp-http-"))
        tt._fixture_dir(cls.tmp)
        cls.scores = tt._fixture_scores(cls.tmp)
        cls.mcp_dir = cls.tmp / "mcp_bundle"
        tt.run("scripts/build_tenant_cases.py",
               "--records", str(cls.tmp / "records.csv"),
               "--fieldmap", str(cls.tmp / "fieldmap.csv"),
               "--scores", str(cls.scores),
               "--dataset-label", "Pilot Co records",
               "--truth-label", "Pilot labels",
               "--out", str(cls.tmp / "cases.json"),
               "--mcp-dir", str(cls.mcp_dir))
        cls.core = mcp_http.load_core(cls.mcp_dir, "https://emb.example/v1", None)
        cls.ep = mcp_http.McpEndpoint(KEY, cls.core)
        cls.meta = json.loads(
            (cls.mcp_dir / "er_meta.json").read_text(encoding="utf-8"))

    @classmethod
    def post(cls, msg, key=KEY, raw=None):
        body = raw if raw is not None else json.dumps(msg).encode("utf-8")
        hdrs = Headers({"Authorization": f"Bearer {key}"}) if key else Headers()
        return cls.ep.handle("POST", hdrs, body)

    @classmethod
    def result(cls, msg, key=KEY):
        status, hdrs, out = cls.post(msg, key=key)
        assert status == 200, out
        return json.loads(out.decode("utf-8"))

    @staticmethod
    def call(name, arguments=None):
        return {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}}}


class TestAuth(HostedBase):
    def test_no_key_401(self):
        status, hdrs, out = self.post({"jsonrpc": "2.0", "id": 1,
                                       "method": "ping"}, key=None)
        self.assertEqual(status, 401)
        self.assertIn("Bearer", dict(hdrs)["WWW-Authenticate"])
        self.assertIn("key", json.loads(out.decode())["error"])

    def test_wrong_key_401(self):
        status, _, _ = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                 key="ea-wrong")
        self.assertEqual(status, 401)

    def test_get_405_delete_200(self):
        hdrs = Headers({"Authorization": f"Bearer {KEY}"})
        self.assertEqual(self.ep.handle("GET", hdrs, b"")[0], 405)
        status, _, out = self.ep.handle("DELETE", hdrs, b"")
        self.assertEqual((status, out), (200, b"{}"))


class TestProtocol(HostedBase):
    def test_initialize_negotiates_and_gives_session(self):
        status, hdrs, out = self.post(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(status, 200)
        body = json.loads(out.decode())
        self.assertEqual(body["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(body["result"]["serverInfo"]["name"], "entity-er")
        self.assertTrue(dict(hdrs).get("Mcp-Session-Id"))

    def test_unsupported_version_gets_latest(self):
        body = self.result({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                            "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")

    def test_notification_202_empty(self):
        status, _, out = self.post({"jsonrpc": "2.0",
                                    "method": "notifications/initialized"})
        self.assertEqual((status, out), (202, b""))

    def test_ping(self):
        self.assertEqual(self.result(
            {"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"], {})

    def test_unknown_method(self):
        body = self.result({"jsonrpc": "2.0", "id": 4, "method": "no/such"})
        self.assertEqual(body["error"]["code"], -32601)

    def test_bad_json_parse_error(self):
        status, _, out = self.post(None, raw=b"{nope")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(out.decode())["error"]["code"], -32700)

    def test_non_object_rejected(self):
        status, _, out = self.post(None, raw=b"[1,2]")
        self.assertEqual(json.loads(out.decode())["error"]["code"], -32600)

    def test_tools_list_tenant_descriptions(self):
        body = self.result({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
        names = [t["name"] for t in body["result"]["tools"]]
        self.assertEqual(names, ["er_score_pair", "er_find_records", "er_about"])
        joined = json.dumps(body, ensure_ascii=False)
        self.assertIn("Pilot Co records", joined)   # 租户标签，不是 Febrl 村


class TestTools(HostedBase):
    def test_about_tenant_caliber(self):
        text = self.result(self.call("er_about"))["result"]["content"][0]["text"]
        self.assertIn(str(self.meta["model"]), text)
        self.assertIn(f"{float(self.meta['theta']):.4f}", text)   # 同 stdio 面口径
        self.assertNotIn("Febrl3", text)                          # 不回落演示数据

    def test_score_pair_offline_matches_matrix(self):
        pairs = json.loads((self.mcp_dir / "scores.json").read_text(
            encoding="utf-8"))["pairs"]
        a, b, s = pairs[0]
        text = self.result(self.call(
            "er_score_pair", {"a": a, "b": b}))["result"]["content"][0]["text"]
        self.assertIn(a, text)
        self.assertIn(f"{s:.4f}", text)   # 矩阵分数原样透出（离线=实时）

    def test_unknown_tool_invalid_params(self):
        body = self.result(self.call("nope"))
        self.assertEqual(body["error"]["code"], -32602)


class TestDegraded(unittest.TestCase):
    """交付包缺席：不回落 Febrl 演示数据，工具回上线指引。"""

    @classmethod
    def setUpClass(cls):
        cls.ep = mcp_http.McpEndpoint(KEY, None)

    def post(self, msg):
        return self.ep.handle(
            "POST", Headers({"Authorization": f"Bearer {KEY}"}),
            json.dumps(msg).encode())

    def test_states(self):
        self.assertFalse(self.ep.ready)
        self.assertFalse(self.ep.info()["ready"])
        self.assertNotIn("key", self.ep.info())          # healthz 不带 key
        self.assertEqual(self.ep.console_state()["key"], KEY)

    def test_tools_list_and_call_guidance(self):
        body = json.loads(self.post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[2].decode())
        self.assertEqual(len(body["result"]["tools"]), 3)
        out = json.loads(self.post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "er_about", "arguments": {}}})[2].decode())
        self.assertTrue(out["result"].get("isError"))
        self.assertIn("③–⑤", out["result"]["content"][0]["text"])


class TestKeyResolve(unittest.TestCase):
    def test_tiers_and_autogenerate(self):
        with tempfile.TemporaryDirectory(prefix="ea-key-") as d:
            wd = Path(d)
            self.assertEqual(mcp_http.resolve_key("k1", "k2", wd)[0], "k1")
            self.assertEqual(mcp_http.resolve_key(None, "k2", wd)[0], "k2")
            k3, src = mcp_http.resolve_key(None, None, wd)
            self.assertTrue(k3.startswith("ea-"))
            self.assertEqual((wd / ".mcp_key").read_text().strip(), k3)
            # 第二次解析读回同一个（稳定，重启不换）
            self.assertEqual(mcp_http.resolve_key(None, None, wd)[0], k3)
            self.assertIn(".mcp_key", src)

    def test_emb_url_from_train_state(self):
        with tempfile.TemporaryDirectory(prefix="ea-url-") as d:
            wd = Path(d)
            self.assertEqual(mcp_http.resolve_emb_url(wd, "https://d"),
                             "https://d")   # 无 state → default
            (wd / ".train").mkdir()
            (wd / ".train" / "state.json").write_text(
                json.dumps({"service_url": "https://deployed.example"}))
            self.assertEqual(mcp_http.resolve_emb_url(wd, "https://d"),
                             "https://deployed.example")


if __name__ == "__main__":
    unittest.main()
