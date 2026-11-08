"""开源基准轨：平台报告解析 → open_benchmark.json 的正确性与确定性。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data_open" / "eval_reports"


def run(reports: Path, out: Path) -> None:
    subprocess.run([sys.executable, "scripts/update_open_benchmark.py",
                    "--reports", str(reports), "--out", str(out)],
                   check=True, cwd=ROOT, stdout=subprocess.DEVNULL)


class TestOpenBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out = Path(cls.tmp) / "open_benchmark.json"
        run(REPORTS, cls.out)
        cls.data = json.loads(cls.out.read_text(encoding="utf-8"))

    def test_three_models_with_real_numbers(self):
        keys = [m["key"] for m in self.data["models"]]
        self.assertEqual(keys, ["inf15", "nemotron", "gemma"])
        by = {m["key"]: m for m in self.data["models"]}
        # 与平台报告原文一致的关键数字（防解析列错位）
        self.assertAlmostEqual(by["gemma"]["before"]["ndcg10"], 0.980, places=3)
        self.assertAlmostEqual(by["gemma"]["after"]["ndcg10"], 1.000, places=3)
        self.assertAlmostEqual(by["inf15"]["before"]["recall5"], 0.978, places=3)
        self.assertAlmostEqual(by["inf15"]["after"]["recall5"], 0.997, places=3)
        self.assertAlmostEqual(by["nemotron"]["after"]["recall10"], 1.000, places=3)

    def test_win_tie_loss_totals_equal_test_queries(self):
        for m in self.data["models"]:
            self.assertEqual(sum(m["win_tie_loss"]), 850, m["key"])

    def test_after_not_worse_than_before(self):
        # 本数据集上微调后任何主指标都不应变差（变差说明解析错列）
        for m in self.data["models"]:
            for k, v in m["before"].items():
                self.assertGreaterEqual(m["after"][k], v - 1e-9, f"{m['key']}.{k}")

    def test_deterministic(self):
        out2 = Path(self.tmp) / "again.json"
        run(REPORTS, out2)
        self.assertEqual(out2.read_bytes(), self.out.read_bytes())

    def test_baseline_drift_raises(self):
        """post 报告基线列与独立基线评测不一致 → 必须拒绝（口径漂移）。"""
        bad = Path(self.tmp) / "bad_reports"
        bad.mkdir()
        for p in REPORTS.iterdir():
            (bad / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        target = bad / "post-gemma.md"
        target.write_text(
            target.read_text(encoding="utf-8").replace("| NDCG@10 | 0.980 | 1.000 |", "| NDCG@10 | 0.970 | 1.000 |"),
            encoding="utf-8",
        )
        with self.assertRaises(subprocess.CalledProcessError):
            run(bad, Path(self.tmp) / "bad.json")


if __name__ == "__main__":
    unittest.main()
