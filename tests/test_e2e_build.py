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
# Febrl3 实测轨数据（平台部署修复后由 fetch 脚本写入入库溯源文件）
VILLAGE_SCORES = ROOT / "data_open" / "village_scores.json"
HAS_OPEN_CASES = VILLAGE_SCORES.exists()


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
        # 网络视图 Febrl3 实测轨：由已入库的成对分数确定性重建（无需服务在线）。
        # 数据未接入前（平台部署修复中）该步跳过，页面自动只出合成轨。
        if HAS_OPEN_CASES:
            for d in (cls.d1, cls.d2):
                run("scripts/build_open_cases.py",
                    "--village", str(ROOT / "data_open" / "village.json"),
                    "--scores", str(VILLAGE_SCORES),
                    "--out", str(d / "open_cases.json"))
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

    @unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库（平台部署修复后接入）")
    def test_open_cases_deterministic(self):
        """Febrl3 实测轨：同分数两次重建逐字节一致（--scores 只读复算）。"""
        self.assertEqual((self.d1 / "open_cases.json").read_bytes(),
                         (self.d2 / "open_cases.json").read_bytes())

    @unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库（平台部署修复后接入）")
    def test_explain_real_cards_in_page(self):
        """Explain 实测卡必须打进页面：R1/R2 卡、基线不可部署注脚、?case= 深链。"""
        for marker in ('"pair_id":"R1"', '"pair_id":"R2"', "exp.realonly",
                       "qs.get('case')", "Febrl3"):
            self.assertIn(marker, self.html)

    def test_page_markers_present(self):
        for marker in ('id="view-arena"', 'id="view-network"', 'id="view-explain"',
                       'id="view-scoreboard"', 'id="openBench"', 'id="netTrack"',
                       "switchNetTrack", "openNetSide", "const DATA = {"):
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
