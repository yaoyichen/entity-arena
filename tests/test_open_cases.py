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
        {"rid": f"rec-{i}", "entity": e, "fields": f, "text": f"text {i}"}
        for i, (e, f) in enumerate([
            # E1 两条：姓名全同，但生日一侧缺失、地址错字 —— Explain 极值规则应挑它做 R1
            (1, {"name": "aa", "dob": "1980-01-01", "address": "1 main st"}),
            (1, {"name": "aa", "address": "1 mani st"}),
            # E2 两条：与 E1 同名同生日的"影子人"（基线字面相似度的纠缠源）
            (2, {"name": "aa", "dob": "1980-01-01"}), (2, {"name": "aa", "dob": "1980-01-01"}),
            # E3 两条：全同的干净对（分数更低 0.90 但零缺失）—— 不得抢走 R1
            (3, {"name": "cc"}), (3, {"name": "cc"}),
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

    def test_explain_cards_structure(self):
        """Explain 实测卡：真实分数 + 逐字段四态；θ/模型与全 payload 一致。
        六卡按极值规则生成，池空缺席（fixture 基线 θ=1.0 零误连 → R6 缺席）。"""
        exp = self.payload["explain"]
        ids = [c["pair_id"] for c in exp["pairs"]]
        self.assertEqual(ids, ["R1", "R2", "R3", "R4", "R5"])  # R6 池空缺席
        for c in exp["pairs"]:
            self.assertTrue(c["real"])
            self.assertEqual(c["theta"], round(self.payload["theta"], 4))
            self.assertEqual(c["model"], self.payload["model"])
            self.assertIn("en", c["title"]); self.assertIn("zh", c["title"])
            self.assertIn("en", c["note"]); self.assertIn("zh", c["note"])
            self.assertTrue(c["fields"])
            for f in c["fields"]:
                self.assertIn(f["state"], ("exact", "fuzzy", "conflict", "missing"))
                self.assertIn("en", f["label"]); self.assertIn("zh", f["label"])
                if f["state"] == "exact":
                    self.assertEqual(f["va"], f["vb"]); self.assertEqual(f["sim"], 1.0)
                elif f["state"] == "missing":
                    self.assertIsNone(f["sim"]); self.assertIn("—", (f["va"], f["vb"]))
                elif f["state"] == "fuzzy":
                    self.assertGreaterEqual(f["sim"], 0.7)
                else:
                    self.assertLess(f["sim"], 0.7)
            self.assertIn(c["verdicts"]["tuned"], ("match", "nonmatch"))
            self.assertNotIn("baseline", c["scores"])  # 实测卡只有微调一行

    def test_explain_r1_extreme_pick(self):
        """R1 = 正确断言的真对中"最乱"者：缺失字段数优先（E1 缺生日、地址错字，
        即便 E3 分数更接近 θ 也不得抢走）；分数必须过线。"""
        r1 = self.payload["explain"]["pairs"][0]
        self.assertEqual((r1["a"], r1["b"]), ("rec-0", "rec-1"))
        self.assertEqual(r1["truth"], "match")
        self.assertGreaterEqual(r1["scores"]["tuned"], self.payload["theta"])
        self.assertEqual(r1["verdicts"]["tuned"], "match")
        states = {f["key"]: f["state"] for f in r1["fields"]}
        self.assertEqual(states["dob"], "missing")
        self.assertEqual(states["address"], "fuzzy")
        # 档案 stub 恰好覆盖两卡涉及的记录（并集）
        stubs = self.payload["explain"]["records"]
        used = {rid for c in self.payload["explain"]["pairs"] for rid in (c["a"], c["b"])}
        self.assertEqual(set(stubs), used)
        for rid in used:
            self.assertEqual(stubs[rid]["source"], "febrl3")

    def test_explain_r2_strongest_refusal(self):
        """R2 = 被拒异实体对中分数最高者：真值不同人、分数不过线、判定拒连。"""
        r2 = self.payload["explain"]["pairs"][1]
        self.assertEqual(r2["truth"], "nonmatch")
        self.assertLess(r2["scores"]["tuned"], self.payload["theta"])
        self.assertEqual(r2["verdicts"]["tuned"], "nonmatch")
        # fixture 里被拒的最高分是 0.50（0.93 那对是误连、不在被拒池）
        self.assertAlmostEqual(r2["scores"]["tuned"], 0.50, places=6)

    def test_explain_r3_r4_extreme_picks(self):
        """R3 = 贴线惊险连对（距 θ ≥ 0.01 的最低分真连）；R4 = exact 字段
        最多仍拒连。fixture 上 R3 池只剩 E1 对（E3 对 0.90 贴线 0.00 被边距
        排除），R4 是同名同生日的影子对（name/dob 双 exact）。"""
        pairs = {c["pair_id"]: c for c in self.payload["explain"]["pairs"]}
        r3 = pairs["R3"]
        self.assertEqual(r3["truth"], "match")
        self.assertEqual(r3["verdicts"]["tuned"], "match")
        self.assertGreaterEqual(r3["scores"]["tuned"] - self.payload["theta"], 0.01)
        r4 = pairs["R4"]
        self.assertEqual(r4["truth"], "nonmatch")
        self.assertEqual(r4["verdicts"]["tuned"], "nonmatch")
        exact = [f["key"] for f in r4["fields"] if f["state"] == "exact"]
        self.assertGreaterEqual(len(exact), 2, "R4 必须是 exact 最多仍被拒的对")

    # ---- Arena 对决台（payload["arena"]） ----

    def test_arena_payload(self):
        """Arena 六类查询：顺序编号、类别/缺席声明、双模型 top-5 降序，
        微调列与全矩阵独立重算逐对一致（tie-break：-分数、rid 升序）。"""
        a, p = self.payload["arena"], self.payload
        known = {"big_entity", "missing", "lookalike", "false_link",
                 "missed_link", "typo_storm"}
        self.assertEqual(a["k"], 5)
        self.assertEqual(a["theta"]["baseline"], p["baseline"]["theta"])
        self.assertEqual(a["theta"]["tuned"], round(p["theta"], 5))
        self.assertTrue(set(a["absent"]) <= known)
        # fixture：基线 θ=1.0 零误断言 → false_link 池空缺席
        self.assertIn("false_link", a["absent"])

        qs = a["queries"]
        self.assertTrue(3 <= len(qs) <= 6, "类别池撞车顺延，但不得少于 3 条")
        self.assertEqual([q["query_id"] for q in qs],
                         [f"F{i}" for i in range(1, len(qs) + 1)])
        self.assertEqual(len({q["record_id"] for q in qs}), len(qs),
                         "一条记录不得充当两条查询")
        self.assertTrue({q["scenario"] for q in qs} <= known)

        # 全矩阵独立重算（微调列 = 真实分数，不依赖脚本内部实现）
        full: dict[tuple[str, str], float] = {}
        for x, y, s in json.loads(self.scores.read_text(encoding="utf-8"))["pairs"]:
            full[(x, y)] = full[(y, x)] = s
        recs = p["records"]
        k = min(5, len(recs) - 1)
        for q in qs:
            for key in ("title", "story"):
                self.assertIn("en", q[key]); self.assertIn("zh", q[key])
            for model in ("baseline", "tuned"):
                hits = q["results"][model]
                self.assertEqual(len(hits), k)
                ss = [h["s"] for h in hits]
                self.assertEqual(ss, sorted(ss, reverse=True), "top-k 必须降序")
                for h in hits:
                    self.assertNotEqual(h["r"], q["record_id"])
                    self.assertIn(h["r"], recs)
                    self.assertGreaterEqual(h["s"], 0.0)
                    self.assertLessEqual(h["s"], 1.0)
            others = sorted((r for r in recs if r != q["record_id"]),
                            key=lambda r: (-full[(q["record_id"], r)], r))[:k]
            self.assertEqual([h["r"] for h in q["results"]["tuned"]], others,
                             "微调列必须等于全矩阵重排（含 tie-break）")
            for h, r in zip(q["results"]["tuned"], others):
                self.assertEqual(h["s"], round(full[(q["record_id"], r)], 4))

    def test_arena_extreme_picks(self):
        """类别挑选对叙事盲（规则可复算）：missing 类必须落在按 5 字段
        全 schema 缺失最多的记录上（fixture：E3 的记录只余 name，缺 4 个
        居首；rec-4/rec-5 并列时 rid 升序取 rec-4）。"""
        a = self.payload["arena"]
        miss = [q for q in a["queries"] if q["scenario"] == "missing"]
        self.assertTrue(miss, "fixture 的 missing 池非空")
        self.assertEqual(miss[0]["record_id"], "rec-4")

    # ---- 指标板（payload["metrics"]） ----

    def test_open_metrics(self):
        """指标口径：方向性计数、空池诚实置 None、分桶求和 = 总查询数、
        link_recall ≤ recall@5（断言是检索的子集）。"""
        m, p = self.payload["metrics"], self.payload
        names = ("recall@5", "link_recall", "link_precision", "false_link_rate")
        self.assertEqual(m["k"], 5)
        self.assertEqual(m["n_queries"], len(p["records"]))
        self.assertEqual(m["threshold"]["baseline"], p["baseline"]["theta"])
        self.assertEqual(m["threshold"]["tuned"], round(p["theta"], 5))
        self.assertEqual(sum(b["n"] for b in m["by_bucket"]), m["n_queries"])
        for b in m["by_bucket"]:
            self.assertIn(b["scenario"], m["bucket_labels"])
            lbl = m["bucket_labels"][b["scenario"]]
            self.assertIn("en", lbl); self.assertIn("zh", lbl)
        for grp in [m["overall"], *(b for b in m["by_bucket"])]:
            for model in ("baseline", "tuned"):
                for name in names:
                    v = grp[model][name]
                    if v is not None:  # 空池（如单记录实体）诚实置空，页面渲染 —
                        self.assertGreaterEqual(v, 0.0)
                        self.assertLessEqual(v, 1.0)
                lr, r5 = grp[model]["link_recall"], grp[model]["recall@5"]
                if lr is not None and r5 is not None:
                    self.assertLessEqual(lr, r5)
        # fixture 可复算：影子查询 2 条，微调把 0.93 的影子对断言过线 → flr=1/2
        self.assertAlmostEqual(m["overall"]["tuned"]["false_link_rate"], 0.5)
        self.assertEqual(m["overall"]["baseline"]["false_link_rate"], 0.0)

    def test_provenance_arena_and_metrics_rules(self):
        """新板块的挑选/口径规则必须全文进溯源（对叙事盲的承诺可审计）。"""
        self.assertIn("arena_rule", self.payload["provenance"])
        self.assertIn("metrics_rule", self.payload["provenance"])
        self.assertIn("θ", self.payload["provenance"]["metrics_rule"])

    # ---- 基线（字面相似度代理） ----

    def test_baseline_payload_structure(self):
        """基线代理与微调轨道同构：断言链/误连/漏连/组件全套，可被前端复用。"""
        b = self.payload["baseline"]
        self.assertEqual(b["kind"], "proxy")  # 诚实标注：不是真实基线模型
        self.assertGreater(b["theta"], 0.0)
        self.assertLessEqual(b["theta"], 1.0)
        self.assertEqual(len(b["links"]), b["stats"]["asserted"])
        for lk in b["links"]:
            self.assertGreaterEqual(lk["s"], b["theta"])
        recs = self.payload["records"]
        for fl in b["false_links"]:
            a, c = fl.split("|")
            self.assertNotEqual(recs[a]["entity"], recs[c]["entity"], "误连必须跨实体")
        for m in b["missed_pairs"]:
            a, c = m.split("|")
            self.assertEqual(recs[a]["entity"], recs[c]["entity"], "漏连必须同实体")
        self.assertEqual(set(b["comp_of"]), set(recs))
        self.assertEqual(len(b["components"]), b["stats"]["components"])
        self.assertEqual(sum(1 for c in b["components"] if c["pure"]),
                         b["stats"]["pure_components"])
        for c in b["components"]:
            ents = {recs[m]["entity"] for m in b["network"]["comp_members"][c["id"]]}
            self.assertEqual(ents, set(c["entities"]))
            self.assertEqual(c["pure"], len(ents) == 1)

    def test_baseline_fixture_precision_collapse(self):
        """影子人倒挂在 fixture 上的缩影：E1↔E2 同名同生日的跨实体对字面分
        （0.875）压过 E1 内部真对（生日缺失 + 地址错字，<0.875），θ 扫描
        在"误连换漏连"之间无解，只好退到 1.0 —— 只连逐字节全同的记录，
        漏掉真对 rec-0|rec-1。"""
        b = self.payload["baseline"]
        self.assertEqual(b["theta"], 1.0)
        self.assertEqual(b["false_links"], [])       # 宁可全不连，也不吸收影子人
        self.assertEqual(b["missed_pairs"], ["rec-0|rec-1"])
        self.assertEqual(b["stats"]["tp"], 2)
        self.assertEqual(b["stats"]["truth_pairs"], 3)

    def test_provenance_discloses_baseline_proxy(self):
        """溯源必须声明基线是代理（诚实标注），不能冒充真实基线模型。"""
        rule = self.payload["provenance"]["baseline_rule"]
        self.assertIn("代理", rule)
        self.assertIn("Levenshtein", rule)


if __name__ == "__main__":
    unittest.main()
