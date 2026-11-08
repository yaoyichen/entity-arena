"""网络布局：确定性、坐标在画布内、光环两两不压叠。"""

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
        """归并后状态：任意两个实体的光环不得互相压叠（影子人/双胞胎必须肉眼可分）。"""
        ents = self.net["entities"]
        self.assertEqual(len(ents), len(self.w.entities))
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                ea, eb = ents[i], ents[j]
                d = math.hypot(ea["x"] - eb["x"], ea["y"] - eb["y"])
                ra = _halo_radius(ea["size"])  # 与前端 halo 半径公式一致（单一出处）
                rb = _halo_radius(eb["size"])
                self.assertGreaterEqual(
                    d, ra + rb - 2,
                    f"光环压叠：{ea['e']}(r={ra:.0f}) ↔ {eb['e']}(r={rb:.0f}) 距离 {d:.1f}")

    def test_entity_size_matches_members(self):
        counts: dict[str, int] = {}
        for n in self.net["nodes_after"]:
            counts[n["e"]] = counts.get(n["e"], 0) + 1
        for e in self.net["entities"]:
            self.assertEqual(e["size"], counts.get(e["e"], 0), f"{e['e']} size 与成员数不符")

    def test_edges_reference_valid_nodes(self):
        rids = {n["r"] for n in self.net["nodes_before"]}
        eids = {e["e"] for e in self.net["entities"]}
        for e in self.net["edges_before"]:
            self.assertIn(e["a"], rids)
            self.assertIn(e["b"], rids)
        for e in self.net["edges_after"]:
            self.assertIn(e["a"], eids)
            self.assertIn(e["b"], eids)
            self.assertNotEqual(e["a"], e["b"], "实体不得自连")
            self.assertGreaterEqual(e["evidence"], 1)

    def test_margin_kept(self):
        """实体中心保持在安全边距内（光环整体不出画布）。"""
        w, h = self.net["canvas"]["w"], self.net["canvas"]["h"]
        for e in self.net["entities"]:
            self.assertTrue(MARGIN - 1 <= e["x"] <= w - MARGIN + 1, e["e"])
            self.assertTrue(MARGIN - 1 <= e["y"] <= h - MARGIN + 1, e["e"])


if __name__ == "__main__":
    unittest.main()
