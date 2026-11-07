"""模拟评分与指标：字段相似度、剧本走向（Q1–Q6）、指标口径。"""

from __future__ import annotations

import random
import unittest

from entity_arena.simulate import (
    K,
    THRESHOLD,
    baseline_score,
    compute_metrics,
    field_sims,
    field_state,
    retrieve_all,
    tuned_score,
)
from entity_arena.synth import build_world

SEED = 42


def rec(**kw):
    """造一条最小 Record（字段相似度单测用）。"""
    from entity_arena.synth import Record

    base = dict(record_id="RX", entity_id="EX", name="X", name_script="han",
                id_number=None, dob=None, pob=None, phone=None,
                source="household_registry", noise_tags=[])
    base.update(kw)
    return Record(**base)


class TestFieldSims(unittest.TestCase):
    def test_name_exact(self):
        a = rec(name="张伟"); b = rec(name="张伟")
        self.assertEqual(field_sims(a, b)["name"], 1.0)

    def test_name_cross_script_blind_spot(self):
        """跨文字名字 = baseline 盲区：低但非零。"""
        a = rec(name="张伟", name_script="han"); b = rec(name="Zhang Wei", name_script="latin")
        self.assertAlmostEqual(field_sims(a, b)["name"], 0.12)

    def test_dob_transposition(self):
        """02-30 vs 02-03：日数字换位给 0.6（冲突容忍的入口）。"""
        a = rec(dob="1978-02-30"); b = rec(dob="1978-02-03")
        self.assertAlmostEqual(field_sims(a, b)["dob"], 0.6)

    def test_dob_year_shift(self):
        a = rec(dob="1979-02-03"); b = rec(dob="1978-02-03")
        self.assertAlmostEqual(field_sims(a, b)["dob"], 0.2)

    def test_dob_missing_is_none(self):
        self.assertIsNone(field_sims(rec(dob=None), rec(dob="1980-01-01"))["dob"])

    def test_id_typo_vs_wrong(self):
        """差 ≤2 位 = OCR 级 typo（0.5）；≥3 位 = 陌生证件（0.1）。"""
        same18 = "110105198307140037"
        typo2 = same18[:15] + "98" + same18[17:]  # 差 2 位，仍在 typo 带内
        self.assertAlmostEqual(field_sims(rec(id_number=same18), rec(id_number=typo2))["id_number"], 0.5)
        other = "999" + same18[3:]  # 差 3 位（不同区域的陌生证件）
        self.assertAlmostEqual(field_sims(rec(id_number=same18), rec(id_number=other))["id_number"], 0.1)

    def test_pob_cross_language_same_city(self):
        """中文籍贯 vs 英文城市名 → 同城键 0.7。"""
        a = rec(pob="上海市静安区"); b = rec(pob="Shanghai")
        self.assertAlmostEqual(field_sims(a, b)["pob"], 0.7)
        self.assertAlmostEqual(field_sims(rec(pob="北京"), rec(pob="成都市"))["pob"], 0.0)

    def test_phone_space_insensitive(self):
        a = rec(phone="+86 139 1122 3344"); b = rec(phone="+8613911223344")
        self.assertEqual(field_sims(a, b)["phone"], 1.0)

    def test_field_state_mapping(self):
        self.assertEqual(field_state(None), "missing")
        self.assertEqual(field_state(1.0), "exact")
        self.assertEqual(field_state(0.6), "fuzzy")
        self.assertEqual(field_state(0.0), "conflict")


class TestScoring(unittest.TestCase):
    def test_baseline_all_missing_is_zero(self):
        self.assertEqual(baseline_score({"name": None, "id_number": None, "dob": None,
                                         "pob": None, "phone": None}), 0.0)

    def test_tuned_alias_ceiling_without_corroboration(self):
        """曾用名型：无佐证时 oracle 封顶 0.50 —— 微调也接不上（诚实天花板）。"""
        sims = {"name": 0.0, "id_number": None, "dob": 1.0, "pob": 0.0, "phone": None}
        rng = random.Random(0)
        s = tuned_score(sims, same_entity=True, lookalike=False, rng=rng)
        self.assertLess(s, 0.55)

    def test_tuned_lookalike_suppressed(self):
        sims = {"name": 1.0, "id_number": 0.0, "dob": 0.0, "pob": 0.0, "phone": None}
        rng = random.Random(0)
        s = tuned_score(sims, same_entity=False, lookalike=True, rng=rng)
        self.assertLess(s, 0.55)


class TestRetrieveStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = build_world(SEED)
        cls.res = retrieve_all(cls.w, SEED)

    def test_top5_sorted_no_self(self):
        rids = {r.record_id for r in self.w.all_records()}
        for model in ("baseline", "tuned"):
            self.assertEqual(set(self.res[model]), rids)
            for q, hits in self.res[model].items():
                self.assertLessEqual(len(hits), K)
                scores = [h["s"] for h in hits]
                self.assertEqual(scores, sorted(scores, reverse=True), f"{model}/{q} 未按分排序")
                for h in hits:
                    self.assertNotEqual(h["r"], q)
                    self.assertTrue(0.0 <= h["s"] <= 1.0)


class TestScriptedNarratives(unittest.TestCase):
    """六个剧本查询的确定走向 —— Arena 文案与数据必须互相印证。"""

    @classmethod
    def setUpClass(cls):
        cls.w = build_world(SEED)
        cls.res = retrieve_all(cls.w, SEED)

    def hit(self, model, q, rid):
        for h in self.res[model][q]:
            if h["r"] == rid:
                return h["s"]
        return None

    def test_q1_bare_hotel_slip(self):
        """R103：baseline 漏掉户籍 R101；tuned 全量捞回，R101 是未过线候选。"""
        s = self.hit("baseline", "R103", "R101")
        self.assertIsNone(s, "baseline 不应召回 R101")
        s = self.hit("tuned", "R103", "R101")
        self.assertIsNotNone(s)
        self.assertLess(s, THRESHOLD, "R101 应是候选（未断言），不是已连")
        for m in ("R102", "R104"):
            self.assertIsNotNone(self.hit("tuned", "R103", m))

    def test_q2_transliteration(self):
        """R402 → R401：tuned #1 高分；baseline 0.34 不过线。"""
        self.assertEqual(self.res["tuned"]["R402"][0]["r"], "R401")
        self.assertGreaterEqual(self.res["tuned"]["R402"][0]["s"], 0.85)
        self.assertAlmostEqual(self.hit("baseline", "R402", "R401"), 0.34, places=2)

    def test_q3_conflicting_dob(self):
        """R602 → R601：baseline 0.50 差一点；tuned ≥0.77 连上。"""
        b = self.hit("baseline", "R602", "R601")
        t = self.hit("tuned", "R602", "R601")
        self.assertLess(b, THRESHOLD)
        self.assertAlmostEqual(b, 0.50, places=1)
        self.assertGreaterEqual(t, 0.77)

    def test_q4_same_name_stranger(self):
        """R201 ↔ R101：baseline 0.66 误连；tuned 0.31 分开。"""
        b = self.hit("baseline", "R201", "R101")
        t = self.hit("tuned", "R201", "R101")
        self.assertIsNotNone(b); self.assertIsNotNone(t)
        self.assertGreaterEqual(b, THRESHOLD)
        self.assertAlmostEqual(b, 0.66, places=2)
        self.assertLess(t, THRESHOLD)
        self.assertAlmostEqual(t, 0.31, places=2)

    def test_q5_twins(self):
        """R301 ↔ R303：baseline 0.74 误连；tuned 0.49 惊险分开。"""
        b = self.hit("baseline", "R301", "R303")
        t = self.hit("tuned", "R301", "R303")
        self.assertIsNotNone(b); self.assertIsNotNone(t)
        self.assertGreaterEqual(b, THRESHOLD)
        self.assertAlmostEqual(b, 0.74, places=2)
        self.assertLess(t, THRESHOLD)
        self.assertAlmostEqual(t, 0.49, places=2)

    def test_q6_honest_alias_miss(self):
        """R501 ↔ R502：双双不过线，tuned 更近但依然不敢连。"""
        b = self.hit("baseline", "R501", "R502")
        t = self.hit("tuned", "R501", "R502")
        self.assertLess(b, THRESHOLD)
        self.assertLess(t, THRESHOLD)
        self.assertGreater(t, b)


class TestMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = build_world(SEED)
        cls.m = compute_metrics(cls.w, retrieve_all(cls.w, SEED))

    def test_protocol_constants(self):
        self.assertEqual(self.m["k"], 5)
        self.assertEqual(self.m["threshold"], 0.55)

    def test_tuned_perfect_on_safe_metrics(self):
        o = self.m["overall"]["tuned"]
        self.assertEqual(o["recall@5"], 1.0)
        self.assertEqual(o["link_precision"], 1.0)
        self.assertEqual(o["false_link_rate"], 0.0)

    def test_baseline_makes_false_links(self):
        o = self.m["overall"]["baseline"]
        self.assertGreater(o["false_link_rate"], 0.0, "baseline 必须有误连（演示冲突点）")
        self.assertLess(o["recall@5"], 1.0)

    def test_tuned_beats_baseline_where_it_should(self):
        b, t = self.m["overall"]["baseline"], self.m["overall"]["tuned"]
        self.assertGreater(t["link_recall"], b["link_recall"])
        self.assertLess(t["false_link_rate"], b["false_link_rate"])

    def test_all_metrics_in_unit_range(self):
        for model in ("baseline", "tuned"):
            for k, v in self.m["overall"][model].items():
                self.assertTrue(0.0 <= v <= 1.0, f"{model}.{k}={v}")
        for row in self.m["by_scenario"]:
            for model in ("baseline", "tuned"):
                for k, v in row[model].items():
                    if v is not None:
                        self.assertTrue(0.0 <= v <= 1.0, f"{row['scenario']}.{model}.{k}={v}")


if __name__ == "__main__":
    unittest.main()
