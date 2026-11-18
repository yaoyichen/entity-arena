"""embed_client 单元测试：单源三件套（ssl_ctx / cosine / load_theta）+ embed 重试。

本模块是 MCP 交付包里唯一的共享拷贝，这些行为锁定后，交付包与仓库两侧同源。
网络不真连：urlopen/time.sleep 全部 mock。
"""

from __future__ import annotations

import json
import ssl
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from entity_arena import embed_client
from entity_arena.embed_client import (
    DEFAULT_MODEL, DEFAULT_URL, RETRIES, THETA_FALLBACK, EmbedError,
    cosine, embed, load_theta, ssl_ctx,
)


def _resp(payload: dict) -> mock.MagicMock:
    """可 with 的伪响应：__enter__ 返回自身、read() 给 JSON 字节。"""
    r = mock.MagicMock()
    r.__enter__.return_value = r
    r.__exit__.return_value = False
    r.read.return_value = json.dumps(payload).encode("utf-8")
    return r


class TestCosine(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertAlmostEqual(cosine([1.0, 2.0], [2.0, 4.0]), 1.0)
        # 与 cases_core 旧实现逐位同算式（sum(x*y)/sqrt/…）
        a, b = [0.3, -1.2, 0.7], [1.5, 0.4, -0.9]
        self.assertAlmostEqual(cosine(a, b),
                               sum(x * y for x, y in zip(a, b)) /
                               (sum(x * x for x in a) ** 0.5 * sum(x * x for x in b) ** 0.5))

    def test_zero_vector_guard(self):
        self.assertEqual(cosine([0.0, 0.0], [1.0, 2.0]), 0.0)
        self.assertEqual(cosine([1.0, 2.0], [0.0, 0.0]), 0.0)
        self.assertEqual(cosine([], []), 0.0)


class TestSslCtx(unittest.TestCase):
    def test_pins_tls12_min_and_max(self):
        ctx = ssl_ctx()
        self.assertIs(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertIs(ctx.maximum_version, ssl.TLSVersion.TLSv1_2)


class TestEmbed(unittest.TestCase):
    def _run(self):
        return embed(DEFAULT_URL, DEFAULT_MODEL, "tok", ["a", "b"])

    def test_success_sorted_by_index(self):
        # 服务乱序返回（index=1 在前）→ 结果按 index 排好
        payload = {"data": [
            {"index": 1, "embedding": [0.2, 0.2]},
            {"index": 0, "embedding": [0.1, 0.1]},
        ]}
        with mock.patch.object(embed_client.urllib.request, "urlopen",
                                return_value=_resp(payload)) as up:
            out = self._run()
        self.assertEqual(out, [[0.1, 0.1], [0.2, 0.2]])
        self.assertEqual(up.call_count, 1)
        # 请求体口径：input_type=document / float
        body = json.loads(up.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(body["input_type"], "document")
        self.assertEqual(body["encoding_format"], "float")
        self.assertEqual(body["model"], DEFAULT_MODEL)

    def test_retries_then_success_with_backoff(self):
        payload = {"data": [{"index": 0, "embedding": [0.5]}]}
        with mock.patch.object(embed_client.urllib.request, "urlopen",
                               side_effect=[urllib.error.URLError("boom"), _resp(payload)]) as up, \
             mock.patch.object(embed_client.time, "sleep") as slp:
            out = self._run()
        self.assertEqual(out, [[0.5]])
        self.assertEqual(up.call_count, 2)
        self.assertEqual(slp.call_args_list, [mock.call(3)])  # 退避 3*attempt

    def test_gives_up_after_retries(self):
        with mock.patch.object(embed_client.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("down")) as up, \
             mock.patch.object(embed_client.time, "sleep"):
            with self.assertRaises(EmbedError):
                self._run()
        self.assertEqual(up.call_count, RETRIES)


class TestLoadTheta(unittest.TestCase):
    def test_reads_theta_from_cases_json(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "open_cases.json"
            p.write_text(json.dumps({"theta": 0.8123}), encoding="utf-8")
            self.assertEqual(load_theta(p), 0.8123)

    def test_missing_file_falls_back(self):
        self.assertEqual(load_theta(Path("/nonexistent/open_cases.json")), THETA_FALLBACK)
        self.assertEqual(THETA_FALLBACK, 0.9208)

    def test_bad_json_or_missing_key_falls_back(self):
        with TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_theta(bad), THETA_FALLBACK)
            nokey = Path(d) / "nokey.json"
            nokey.write_text(json.dumps({"records": []}), encoding="utf-8")
            self.assertEqual(load_theta(nokey), THETA_FALLBACK)
            # 路径参数收 str 也可
            self.assertEqual(load_theta(str(bad)), THETA_FALLBACK)


if __name__ == "__main__":
    unittest.main()
