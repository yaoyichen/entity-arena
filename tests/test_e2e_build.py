"""端到端：数据生成 → 页面构建。确定性重建 + 单文件零外链契约。"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = ("records.jsonl", "results.json", "metrics.json", "network.json",
              "queries.json", "pairs.json", "meta.json", "entities.json")


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="entity-arena-e2e-")
        cls.d1 = Path(cls.tmp) / "run1"
        cls.d2 = Path(cls.tmp) / "run2"
        run("scripts/make_mock_data.py", "--seed", "42", "--out", str(cls.d1))
        run("scripts/make_mock_data.py", "--seed", "42", "--out", str(cls.d2))
        # 开源基准轨数据不是 seed 生成的（真实实测）：由已入库的平台报告确定性重建
        run("scripts/update_open_benchmark.py", "--out", str(cls.d1 / "open_benchmark.json"))
        cls.out = Path(cls.tmp) / "demo.html"
        run("scripts/build_demo.py", "--data-dir", str(cls.d1), "--out", str(cls.out))
        cls.html = cls.out.read_text(encoding="utf-8")

    def test_regeneration_byte_identical(self):
        """同 seed 两次生成，8 个数据文件必须逐字节一致。"""
        for name in DATA_FILES:
            self.assertEqual((self.d1 / name).read_bytes(), (self.d2 / name).read_bytes(),
                             f"{name} 不是确定性的")

    def test_all_data_files_written(self):
        for name in DATA_FILES:
            self.assertTrue((self.d1 / name).exists(), f"缺 {name}")

    def test_page_markers_present(self):
        for marker in ('id="view-arena"', 'id="view-network"', 'id="view-explain"',
                       'id="view-scoreboard"', "const DATA = {"):
            self.assertIn(marker, self.html)

    def test_zero_external_links(self):
        """离线契约：成品页不得引用任何外部资源。

        唯一例外：SVG 命名空间常量 http://www.w3.org/2000/svg ——
        它是 W3C 标识符（createElementNS 用），浏览器不会发起请求。
        """
        body = self.html.replace("http://www.w3.org/2000/svg", "")
        self.assertNotIn("http://", body)
        self.assertNotIn("https://", body)
        self.assertNotIn("<link", body.lower())
        self.assertNotIn("<img", body.lower())

    def test_no_unreplaced_placeholders(self):
        self.assertNotIn("{{", self.html)

    def test_script_blocks_wellformed(self):
        """每个 script 块独立看都应是平衡的 JS（粗检：花括号/圆括号配平）。"""
        blocks = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertGreaterEqual(len(blocks), 1)
        for i, b in enumerate(blocks):
            self.assertEqual(b.count("{"), b.count("}"), f"script#{i} 花括号不配平")
            self.assertEqual(b.count("("), b.count(")"), f"script#{i} 圆括号不配平")
            self.assertNotIn("<body>", b)

    def test_build_is_single_file_reasonable_size(self):
        kb = self.out.stat().st_size / 1024
        self.assertGreater(kb, 50, "页面异常地小 —— 数据可能没打进去")
        self.assertLess(kb, 500, "页面异常地大")


if __name__ == "__main__":
    unittest.main()
