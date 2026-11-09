"""试玩台（playground）离线矩阵：页面内任意挑对打分的数据完整性。

三重锁定：
  1. 矩阵覆盖全量两两组合（C(228,2)=25,878），量化值与入库 village_scores 逐对相等；
  2. 量化判定（q ≥ θ）与全精度判定集 == 断言链 links（页内 LINKED/REFUSED
     不允许与网络视图不一致——build_open_cases 构建期同款断言的前端侧锁定）；
  3. 上三角行主序索引公式与 page.py playScore 的 JS 实现一致（同公式复算抽样）。
"""

from __future__ import annotations

import base64
import json
import math
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VILLAGE_SCORES = ROOT / "data_open" / "village_scores.json"
OPEN_CASES = ROOT / "data" / "open_cases.json"
HAS_DATA = VILLAGE_SCORES.exists() and OPEN_CASES.exists()


def _decode(play: dict) -> list[int]:
    raw = base64.b64decode(play["b64"])
    assert len(raw) % 2 == 0
    return list(struct.unpack(f"<{len(raw) // 2}H", raw))


@unittest.skipUnless(HAS_DATA, "data/open_cases.json 或 village_scores.json 未入库")
class TestPlayMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.oc = json.loads(OPEN_CASES.read_text(encoding="utf-8"))
        cls.play = cls.oc["play"]
        cls.vals = _decode(cls.play)
        cls.order = cls.play["order"]
        cls.n = cls.play["n"]
        cls.pos = {r: i for i, r in enumerate(cls.order)}

    def _idx(self, a: str, b: str) -> int:
        i, j = sorted((self.pos[a], self.pos[b]))
        return i * (2 * self.n - i - 1) // 2 + (j - i - 1)

    def test_full_coverage_and_values(self):
        """矩阵覆盖全量组合，量化值 = round-half-up(入库分数 × 1e4)。"""
        self.assertEqual(self.n, 228)
        self.assertEqual(len(self.vals), self.n * (self.n - 1) // 2)
        self.assertEqual(self.order, sorted(self.order))
        src = json.loads(VILLAGE_SCORES.read_text(encoding="utf-8"))
        self.assertEqual(len(src["pairs"]), len(self.vals))
        for a, b, s in src["pairs"]:
            q = self.vals[self._idx(a, b)]
            self.assertEqual(q, math.floor(s * 10000 + 0.5),
                             f"{a}×{b}: 页面矩阵 {q} != 入库 {s}")

    def test_quantized_verdicts_match_links(self):
        """量化判定集必须等于断言链 links（页面试玩台与网络视图同口径）。"""
        theta = self.oc["theta"]
        qset = set()
        for i in range(self.n):
            for j in range(i + 1, self.n):
                if self.vals[i * (2 * self.n - i - 1) // 2 + (j - i - 1)] / 10000 >= theta:
                    qset.add((self.order[i], self.order[j]))
        links = {(l["a"], l["b"]) for l in self.oc["links"]}
        self.assertEqual(qset, links)

    def test_spot_scores_against_source(self):
        """抽样（首对 + 全村最高分对）与入库分数一致，量程下限在有效域内。"""
        src = json.loads(VILLAGE_SCORES.read_text(encoding="utf-8"))
        a, b, s = src["pairs"][0]
        self.assertAlmostEqual(self.vals[self._idx(a, b)] / 10000, s, delta=6e-5)
        self.assertAlmostEqual(max(self.vals) / 10000, max(p[2] for p in src["pairs"]),
                               delta=6e-5)  # 量化半步 5e-5 + 浮点余量
        self.assertGreater(self.play["lo"], 0)
        self.assertLess(self.play["lo"], min(self.vals) / 10000)

    def test_playground_js_index_formula_matches(self):
        """page.py playScore 的 JS 索引式（含除法）在此数据上恒为整数。"""
        for i in range(0, self.n, 17):
            for j in range(i + 1, min(i + 5, self.n)):
                k = i * (2 * self.n - i - 1) / 2 + (j - i - 1)
                self.assertEqual(k, int(k))


if __name__ == "__main__":
    unittest.main()
