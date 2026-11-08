"""build_open_cases.py 的确定性测试：fixture 自带（不依赖真实分数文件）。

真实数据链路的溯源测试见 test_e2e_build.py（用 data_open/village_scores.json）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_open_cases.py"


def make_fixture(tmp: Path) -> tuple[Path, Path]:
    """6 条记录 / 3 个实体：E1 双记录、E2 双记录、E3 双记录。

    分数设计（确定性）：
      E1 内部对 0.95、E3 内部对 0.90（过线）
      E2 内部对 0.50 + 三对 E1-E2 0.50（θ 降到 0.50 会全断言，精度 1/2）
      一对 E1-E2 0.93（θ=0.90 时也过线 → 误连）
      其余跨实体对 0.10
    → θ 扫描：0.50 档 F1=2/3（prec 1/2）与 0.90 档 F1=2/3（prec 2/3）平局，
      精度优先 → θ=0.90：false_links=1（0.93 那对）、missed=1（E2 内部 0.50）。
    """
    records = [
        {"rid": f"rec-{i}", "entity": e, "fields": {"name": n}, "text": f"text {i}"}
        for i, (e, n) in enumerate([
            (1, "aa"), (1, "aa"), (2, "bb"), (2, "bb"), (3, "cc"), (3, "cc"),
        ])
    ]
    rids = [r["rid"] for r in records]
    ent = {r["rid"]: r["entity"] for r in records}
    special = {("rec-1", "rec-2"): 0.93}  # 唯一过线误连
    pairs = []
    for i, a in enumerate(rids):
        for b in rids[i + 1:]:
            key = (ent[a], ent[b]) if ent[a] <= ent[b] else (ent[b], ent[a])
            score = {
                (1, 1): 0.95, (2, 2): 0.50, (3, 3): 0.90,
                (1, 2): 0.50, (1, 3): 0.10, (2, 3): 0.10,
            }[key]
            if (a, b) in special:
                score = special[(a, b)]
            pairs.append([a, b, score])
    village = {
        "records": records,
        "shadow_pairs": [["rec-0", "rec-4"], ["rec-1", "rec-2"]],
        "provenance": {"note": "fixture"},
    }
    scores = {"model": "FIXTURE", "dim": 4, "n_records": 6, "pairs": pairs}
    vp, sp = tmp / "village.json", tmp / "scores.json"
    vp.write_text(json.dumps(village), encoding="utf-8")
    sp.write_text(json.dumps(scores), encoding="utf-8")
    return vp, sp


def run_build(village: Path, scores: Path, out: Path) -> dict:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--village", str(village),
         "--scores", str(scores), "--out", str(out)],
        capture_output=True, text=True, check=True)
    assert "wrote" in r.stdout, r.stdout
    return json.loads(out.read_text(encoding="utf-8"))


class TestOpenCases(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.village, self.scores = make_fixture(self.dir)
        self.out = self.dir / "open_cases.json"
        self.payload = run_build(self.village, self.scores, self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_theta_and_assertions(self):
        p = self.payload
        self.assertGreaterEqual(p["theta"], 0.10)
        self.assertLessEqual(p["theta"], 0.95)
        # 所有断言链分数都 ≥ θ
        for lk in p["links"]:
            self.assertGreaterEqual(lk["s"], p["theta"])
        self.assertEqual(len(p["links"]), p["stats"]["asserted"])

    def test_false_links_cross_entity(self):
        p = self.payload
        recs = p["records"]
        for fl in p["false_links"]:
            a, b = fl.split("|")
            self.assertNotEqual(recs[a]["entity"], recs[b]["entity"],
                                "误连必须跨实体")
        # fixture 设计保证既有误连又有漏连
        self.assertGreaterEqual(len(p["false_links"]), 1)
        self.assertGreaterEqual(len(p["missed_pairs"]), 1)

    def test_missed_pairs_same_entity(self):
        p = self.payload
        recs = p["records"]
        for m in p["missed_pairs"]:
            a, b = m.split("|")
            self.assertEqual(recs[a]["entity"], recs[b]["entity"],
                             "漏连必须同实体")

    def test_components_consistency(self):
        p = self.payload
        # comp_of 全覆盖且与组件成员一致
        self.assertEqual(set(p["comp_of"]), set(p["records"]))
        for c in p["components"]:
            ents = {p["records"][m]["entity"] for m in p["network"]["comp_members"][c["id"]]}
            self.assertEqual(ents, set(c["entities"]))
            self.assertEqual(c["pure"], len(ents) == 1)
        self.assertEqual(sum(1 for c in p["components"] if c["pure"]),
                         p["stats"]["pure_components"])

    def test_top3_sorted_and_scores(self):
        p = self.payload
        for rid, top in p["top3"].items():
            self.assertEqual(len(top), 3)
            scores = [s for _, s in top]
            self.assertEqual(scores, sorted(scores, reverse=True))
            for other, s in top:
                self.assertNotEqual(other, rid)

    def test_determinism(self):
        again = run_build(self.village, self.scores, self.dir / "again.json")
        a = json.dumps(self.payload, sort_keys=True)
        b = json.dumps(again, sort_keys=True)
        self.assertEqual(a, b)

    def test_entity_label_anchors(self):
        """组件标签锚点：单记录放光环内、多记录放光环上方（软墙内可上移）；
        且多记录标签两两纵向防撞（横向投影重叠时 dy ≥ 15px）。"""
        p = self.payload
        ents = p["network"]["entities"]
        self.assertTrue(any(e["size"] == 1 for e in ents))  # fixture 覆盖两种形态
        self.assertTrue(any(e["size"] > 1 for e in ents))
        for e in ents:
            self.assertIn("lx", e)
            self.assertIn("ly", e)
            if e["size"] == 1:  # 单记录：光环内（文本基线 ≈ 环心 + 4.5）
                self.assertAlmostEqual(e["ly"], e["y"] + 4.5, delta=0.11)
            else:  # 多记录：光环上方（自然位 y-R-8；顶行被软墙压到 26 时仍须在环心之上）
                self.assertLessEqual(e["ly"], e["y"] - 8)
                self.assertGreaterEqual(e["ly"], 0)
        multi = [e for e in ents if e["size"] > 1]
        for i in range(len(multi)):
            for j in range(i + 1, len(multi)):
                a, b = multi[i], multi[j]
                if abs(a["lx"] - b["lx"]) < 64:  # 标签宽度估计
                    self.assertGreaterEqual(abs(a["ly"] - b["ly"]), 15)

    def test_scores_file_unchanged_by_scores_mode(self):
        # --scores 模式不得回写溯源文件
        before = self.scores.read_text(encoding="utf-8")
        run_build(self.village, self.scores, self.dir / "once_more.json")
        self.assertEqual(self.scores.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
