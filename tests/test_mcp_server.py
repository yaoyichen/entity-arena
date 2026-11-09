"""entity-er MCP server：stdio JSON-RPC 握手 → 工具清单 → 离线打分/检索/口径。

不联网：rid 对打分走 data_open/village_scores.json 预取样矩阵（确定性），
自由文本无令牌时必须给出可读的 isError 引导。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HAS_DATA = (ROOT / "data_open" / "village_scores.json").exists() and \
           (ROOT / "data_open" / "village.json").exists()


class McpClient:
    def __init__(self):
        env = {k: v for k, v in os.environ.items() if k != "EMB_TOKEN"}
        self.proc = subprocess.Popen(
            [sys.executable, "scripts/er_mcp_server.py"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=ROOT, text=True, bufsize=1, env=env)

    def request(self, method, params=None):
        req = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            req["params"] = params
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call_tool(self, name, args=None):
        r = self.request("tools/call", {"name": name, "arguments": args or {}})
        assert "result" in r, r
        return r["result"]

    def tool_text(self, name, args=None):
        res = self.call_tool(name, args)
        return res["content"][0]["text"], res.get("isError", False)

    def close(self):
        self.proc.stdin.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)


@unittest.skipUnless(HAS_DATA, "data_open/village(+_scores).json 未入库")
class TestMcpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = McpClient()
        init = cls.client.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "unittest"}})
        cls.init_result = init["result"]
        cls.client.notify("notifications/initialized")

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_initialize_handshake(self):
        self.assertEqual(self.init_result["serverInfo"]["name"], "entity-er")
        self.assertIn("tools", self.init_result["capabilities"])
        self.assertEqual(self.init_result["protocolVersion"], "2025-06-18")

    def test_initialize_echoes_older_protocol(self):
        c = McpClient()
        r = c.request("initialize", {"protocolVersion": "2024-11-05"})
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        c.close()

    def test_tools_list(self):
        r = self.client.request("tools/list")
        names = [t["name"] for t in r["result"]["tools"]]
        self.assertEqual(names, ["er_score_pair", "er_find_records", "er_about"])
        for tool in r["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_about_mentions_theta_and_model(self):
        text, err = self.client.tool_text("er_about")
        self.assertFalse(err)
        self.assertIn("febrl3-er-gemma", text)
        self.assertIn("θ", text)
        self.assertIn("228 records", text)
        self.assertIn("offline", text)

    def test_find_records_substring(self):
        text, err = self.client.tool_text("er_find_records", {"q": "morrison"})
        self.assertFalse(err)
        self.assertIn("rec-1012-dup-0", text)
        self.assertIn("er_score_pair", text)  # 必须带下一步指引

    def test_find_records_no_match_is_tool_error(self):
        text, err = self.client.tool_text("er_find_records", {"q": "zzz-no-such-person"})
        self.assertTrue(err)
        self.assertIn("no village record matches", text)

    def test_score_pair_offline_matches_matrix(self):
        # 期望值直接取自入库矩阵，防服务器私改口径
        scores = json.loads(
            (ROOT / "data_open" / "village_scores.json").read_text(encoding="utf-8"))
        a, b, expected = scores["pairs"][0]
        theta = json.loads(
            (ROOT / "data" / "open_cases.json").read_text(encoding="utf-8"))["theta"]
        text, err = self.client.tool_text("er_score_pair", {"a": a, "b": b})
        self.assertFalse(err)
        self.assertIn(f"cosine similarity = {expected:.4f}", text)
        self.assertIn(f"decision line θ    = {theta:.4f}", text)
        self.assertIn("VERDICT: LINKED", text)  # 矩阵首对 0.9918 ≥ θ
        self.assertIn("Febrl3 truth: SAME identity — assertion CORRECT", text)
        self.assertIn("pre-sampled real matrix", text)

    def test_score_pair_entity_id_gives_rid_hint(self):
        text, err = self.client.tool_text("er_score_pair", {"a": "476", "b": "rec-1012-dup-0"})
        self.assertTrue(err)
        self.assertIn("entity id", text)
        self.assertIn("rec-476-dup-0", text)

    def test_score_pair_free_text_without_token_guided(self):
        text, err = self.client.tool_text("er_score_pair",
                                          {"a": "name: x", "b": "name: y"})
        self.assertTrue(err)
        self.assertIn("EMB_TOKEN", text)
        self.assertIn("er_find_records", text)

    def test_unknown_tool_is_invalid_params(self):
        r = self.client.request("tools/call", {"name": "nope", "arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_unknown_method(self):
        r = self.client.request("bogus/method")
        self.assertEqual(r["error"]["code"], -32601)

    def test_ping(self):
        self.assertEqual(self.client.request("ping")["result"], {})


if __name__ == "__main__":
    unittest.main()
