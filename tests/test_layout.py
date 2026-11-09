"""网络布局：确定性、坐标在画布内、光环两两不压叠（三态：原始/基线/微调）。"""

from __future__ import annotations

import math
import unittest

from entity_arena.layout import MARGIN, _halo_radius, build_network
from entity_arena.synth import build_world

SEED = 42


class TestLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = build_world(SEED)
        cls.net = build_network(cls.w, SEED)

    def test_deterministic(self):
        self.assertEqual(build_network(self.w, SEED), self.net)

    def test_canvas_declared(self):
        c = self.net["canvas"]
        self.assertEqual((c["w"], c["h"]), (1200, 780))

    def test_nodes_in_canvas(self):
        w, h = self.net["canvas"]["w"], self.net["canvas"]["h"]
        for key in ("nodes_before", "nodes_after"):
            self.assertEqual(len(self.net[key]), len(self.w.all_records()))
            for n in self.net[key]:
                self.assertTrue(-0.5 <= n["x"] <= w + 0.5, f"{key} {n['r']} x={n['x']}")
                self.assertTrue(-0.5 <= n["y"] <= h + 0.5, f"{key} {n['r']} y={n['y']}")

    def test_before_and_after_same_records(self):
        b = {n["r"] for n in self.net["nodes_before"]}
        a = {n["r"] for n in self.net["nodes_after"]}
        self.assertEqual(a, b)
        self.assertEqual(b, {r.record_id for r in self.w.all_records()})

    def test_entity_halo_no_overlap(self):
        """微调态与基线态：任意两个组件的光环不得互相压叠（影子人/双胞胎必须肉眼可分）。"""
        for name, ents in (("tuned", self.net["entities"]),
                           ("baseline", self.net["baseline"]["network"]["entities"])):
            self.assertTrue(ents)
            for i in range(len(ents)):
                for j in range(i + 1, len(ents)):
                    ea, eb = ents[i], ents[j]
                    d = math.hypot(ea["x"] - eb["x"], ea["y"] - eb["y"])
                    ra = _halo_radius(ea["size"])  # 与前端 halo 半径公式一致（单一出处）
                    rb = _halo_radius(eb["size"])
                    self.assertGreaterEqual(
                        d, ra + rb - 2,
                        f"{name} 光环压叠：{ea['e']}(r={ra:.0f}) ↔ {eb['e']}(r={rb:.0f}) 距离 {d:.1f}")

    def test_component_size_matches_members(self):
        """entities（微调组件）与 comp_members 逐组件一致；基线块同检。"""
        for ents, members in (
                (self.net["entities"], self.net["comp_members"]),
                (self.net["baseline"]["network"]["entities"],
                 self.net["baseline"]["network"]["comp_members"])):
            self.assertTrue(ents)
            self.assertEqual({e["e"] for e in ents}, set(members))
            for e in ents:
                self.assertEqual(e["size"], len(members[e["e"]]),
                                 f"{e['e']} size 与成员数不符")

    def test_three_stage_story_contract(self):
        """三态叙事契约（seed 42）：基线又误连又漏连；微调零误连、≥1 诚实漏连。"""
        bl, tu = self.net["baseline"]["stats"], self.net["stats"]
        self.assertGreaterEqual(bl["false_links"], 1)
        self.assertGreaterEqual(bl["missed_pairs"], 1)
        self.assertEqual(tu["false_links"], 0)
        self.assertGreaterEqual(tu["missed_pairs"], 1)
        # 微调态漏连必须是同实体对（组件级口径：真对落在不同组件）
        ent_of = {r.record_id: r.entity_id for r in self.w.all_records()}
        for p in self.net["missed_pairs"]:
            a, b = p.split("|")
            self.assertEqual(ent_of[a], ent_of[b], f"漏连 {p} 必须同实体")

    def test_links_over_theta_and_baseline_shape(self):
        """断言链分数 ≥ θ；基线块 shape 与 open_cases.baseline 逐字段同构。"""
        for blk in (self.net, self.net["baseline"]):
            for l in blk["links"]:
                self.assertGreaterEqual(l["s"], blk["theta"])
        for key in ("kind", "theta", "stats", "links", "false_links", "missed_pairs",
                    "components", "comp_of", "network"):
            self.assertIn(key, self.net["baseline"])
        self.assertEqual(self.net["baseline"]["kind"], "proxy")

    def test_edges_reference_valid_nodes(self):
        rids = {n["r"] for n in self.net["nodes_before"]}
        cids = {e["e"] for e in self.net["entities"]}
        for e in self.net["edges_before"]:
            self.assertIn(e["a"], rids)
            self.assertIn(e["b"], rids)
        for e in self.net["edges_after"]:
            self.assertIn(e["a"], cids)
            self.assertIn(e["b"], cids)
            self.assertNotEqual(e["a"], e["b"], "组件不得自连")
            self.assertGreaterEqual(e["evidence"], 1)

    def test_margin_kept(self):
        """组件中心保持在安全边距内（光环整体不出画布，双态同检）。"""
        w, h = self.net["canvas"]["w"], self.net["canvas"]["h"]
        for ents in (self.net["entities"],
                     self.net["baseline"]["network"]["entities"]):
            for e in ents:
                self.assertTrue(MARGIN - 1 <= e["x"] <= w - MARGIN + 1, e["e"])
                self.assertTrue(MARGIN - 1 <= e["y"] <= h - MARGIN + 1, e["e"])


if __name__ == "__main__":
    unittest.main()
