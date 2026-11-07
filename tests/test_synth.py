"""合成数据层：确定性、唯一性、实体一致性、场景覆盖。"""

from __future__ import annotations

import unittest

from entity_arena.synth import (
    SCENARIO_META,
    SOURCES,
    build_world,
)

SEED = 42


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_world(self):
        """同 seed 两次构建必须逐字段一致（演示可复现的根基）。"""
        w1 = build_world(SEED)
        w2 = build_world(SEED)
        d1 = [r.to_dict() for r in w1.all_records()]
        d2 = [r.to_dict() for r in w2.all_records()]
        self.assertEqual(d1, d2)
        r1 = [(x.a, x.b, x.rel) for x in w1.relations]
        r2 = [(x.a, x.b, x.rel) for x in w2.relations]
        self.assertEqual(r1, r2)

    def test_different_seed_different_fillers(self):
        """换 seed 填充人物必须变化（不是只认一个死数据）。"""
        a = [r.name for r in build_world(SEED).all_records() if r.record_id.startswith("RF")]
        b = [r.name for r in build_world(SEED + 1).all_records() if r.record_id.startswith("RF")]
        self.assertNotEqual(a, b)


class TestIntegrity(unittest.TestCase):
    def setUp(self):
        self.w = build_world(SEED)

    def test_record_ids_unique(self):
        rids = [r.record_id for r in self.w.all_records()]
        self.assertEqual(len(rids), len(set(rids)))

    def test_core_and_filler_never_collide(self):
        """核心 R1xx–R6xx 与填充 RFxx 前缀隔离。"""
        for r in self.w.all_records():
            self.assertRegex(r.record_id, r"^R(F\d{2}\d|[1-6]\d{2})$")

    def test_entity_membership_consistent(self):
        emap = self.w.entity_map()
        for r in self.w.all_records():
            self.assertIn(r.entity_id, emap)
            self.assertIn(r, emap[r.entity_id].records)
        for e in self.w.entities:
            self.assertGreaterEqual(len(e.records), 2, "每个实体至少 2 条记录（否则无归并可言）")

    def test_lookalikes_valid_and_symmetric(self):
        emap = self.w.entity_map()
        for e in self.w.entities:
            for lk in e.lookalikes:
                self.assertIn(lk, emap, "相似实体必须真实存在")
                self.assertNotEqual(lk, e.entity_id)
                self.assertIn(e.entity_id, emap[lk].lookalikes, "相似关系应对称")

    def test_sources_valid(self):
        for r in self.w.all_records():
            self.assertIn(r.source, SOURCES)

    def test_id_number_shape(self):
        """证件号存在时必须 18 位（区域 6 + 生日 8 + 顺序 3 + 校验 1）。"""
        for r in self.w.all_records():
            if r.id_number is not None:
                self.assertEqual(len(r.id_number), 18, r.record_id)


class TestScenarioCoverage(unittest.TestCase):
    """Jeff 邮件的用例面：每个指标分组场景在数据里都有代表。"""

    def test_all_metric_scenarios_present(self):
        w = build_world(SEED)
        tags: set[str] = set()
        for r in w.all_records():
            tags.update(r.noise_tags)
        for needed in ("transliteration", "name_variant", "missing_partial", "conflicting"):
            self.assertIn(needed, tags)
        self.assertTrue(any(e.lookalikes for e in w.entities), "必须存在相似实体对（similar_entity 场景）")

    def test_scenario_meta_covers_tags(self):
        w = build_world(SEED)
        seen = {"similar_entity"}  # 该场景由 lookalikes 派生，不在 noise_tags
        for r in w.all_records():
            if any(t in ("transliteration", "trad_variant") for t in r.noise_tags):
                seen.add("transliteration")
            if "name_variant" in r.noise_tags or "typo" in r.noise_tags:
                seen.add("name_variant")
            if "missing_partial" in r.noise_tags:
                seen.add("missing_partial")
            if "conflicting" in r.noise_tags:
                seen.add("conflicting")
            if "cross_source" in r.noise_tags:
                seen.add("cross_source")
        for sc in seen:
            self.assertIn(sc, SCENARIO_META)


if __name__ == "__main__":
    unittest.main()
