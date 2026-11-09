"""角色色板六查锁定（dataviz 口径）。

数学内嵌自 dataviz skill 的 validate_palette.py（OKLab ΔE×100 +
Machado-Oliveira-Fernandes 2009 CVD 模拟 severity 1.0），纯标准库。
改 theme.TOKENS 的角色色/亮阶文字/正文文字，本测试必须保持全绿——
"顺手调个色"而打破 CVD 分离或对比度防线在这里直接红灯。

阈值（dark 模式）：L 带 0.48–0.67；C ≥ 0.10；相邻对 CVD ΔE ≥ 8（无次级编码
豁免，按硬门槛锁）；正常视觉最差相邻对 ≥ 15；对 bg 与 panel 双面 ≥ 3:1；
文字亮阶/正文文字对 panel ≥ 4.5:1。
"""
import math
import unittest

from entity_arena.theme import CSS, TOKENS

# Machado, Oliveira & Fernandes (2009) severity-1.0（线性 RGB）。
_MACHADO = {
    "protan": [[0.152286, 1.052583, -0.204868],
               [0.114503, 0.786281, 0.099216],
               [-0.003882, -0.048116, 1.051998]],
    "deutan": [[0.367322, 0.860646, -0.227968],
               [0.280085, 0.672501, 0.047413],
               [-0.011820, 0.042940, 0.968881]],
}


def _lin(h):
    def s2lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rgb = tuple(int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return tuple(s2lin(c) for c in rgb)


def _lum(h):
    r, g, b = _lin(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _oklab(h):
    r, g, b = _lin(h)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _oklch(h):
    L, a, b = _oklab(h)
    return L, math.hypot(a, b)


def _delta_e(h1, h2, kind=None):
    def to_lab(h):
        if kind is None:
            return _oklab(h)
        r, g, b = _lin(h)
        M = _MACHADO[kind]
        rgb = tuple(max(0.0, min(1.0, M[i][0] * r + M[i][1] * g + M[i][2] * b))
                    for i in range(3))
        l = 0.4122214708 * rgb[0] + 0.5363325363 * rgb[1] + 0.0514459929 * rgb[2]
        m = 0.2119034982 * rgb[0] + 0.6806995451 * rgb[1] + 0.1073969566 * rgb[2]
        s = 0.0883024619 * rgb[0] + 0.2817188376 * rgb[1] + 0.6299787005 * rgb[2]
        l, m, s = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
        return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)
    return 100 * math.dist(to_lab(h1), to_lab(h2))


class TestRolePaletteSixChecks(unittest.TestCase):
    """三角色色 = [violet(baseline), cyan(微调), red(错误断言)] 相邻排布（网络图
    红线与青线同场、柱图 紫|青 并排），按相邻对口径校验。"""

    TRIO = ["violet", "cyan", "red"]  # 槽位顺序 = 并排顺序

    def test_lightness_band_and_chroma(self):
        for name in self.TRIO:
            L, C = _oklch(TOKENS[name])
            self.assertGreaterEqual(L, 0.48, f"{name} L={L:.3f} 低于 dark 带下沿")
            self.assertLessEqual(L, 0.67, f"{name} L={L:.3f} 高于 dark 带上沿")
            self.assertGreaterEqual(C, 0.10, f"{name} C={C:.3f} 低于彩度地板（读作灰）")

    def test_adjacent_cvd_separation(self):
        for i in range(len(self.TRIO) - 1):
            a, b = TOKENS[self.TRIO[i]], TOKENS[self.TRIO[i + 1]]
            for kind in ("protan", "deutan"):
                d = _delta_e(a, b, kind)
                self.assertGreaterEqual(
                    d, 8.0,
                    f"{self.TRIO[i]}↔{self.TRIO[i+1]} {kind} ΔE={d:.1f} < 8（无次级编码豁免）")

    def test_normal_vision_floor(self):
        worst = min(_delta_e(TOKENS[self.TRIO[i]], TOKENS[self.TRIO[i + 1]])
                    for i in range(len(self.TRIO) - 1))
        self.assertGreaterEqual(
            worst, 15.0, f"正常视觉最差相邻对 ΔE={worst:.1f} < 15（硬门槛）")

    def test_contrast_vs_both_surfaces(self):
        for name in self.TRIO:
            for surf in ("bg", "panel"):
                r = _contrast(TOKENS[name], TOKENS[surf])
                self.assertGreaterEqual(r, 3.0, f"{name} vs {surf} 对比度 {r:.2f} < 3:1")

    def test_bright_text_variants(self):
        """亮阶变体承担彩色文字（图注/轴标/命令），对 panel ≥ 4.5:1。"""
        for name in ("cyan-b", "violet-b", "red-b", "cyan-l"):
            r = _contrast(TOKENS[name], TOKENS["panel"])
            self.assertGreaterEqual(r, 4.5, f"{name} vs panel 对比度 {r:.2f} < 4.5:1")

    def test_text_tokens(self):
        for name in ("t1", "t2", "t3"):
            r = _contrast(TOKENS[name], TOKENS["panel"])
            self.assertGreaterEqual(r, 4.5, f"{name} vs panel 对比度 {r:.2f} < 4.5:1")

    def test_tokens_and_css_in_sync(self):
        """TOKENS 与 CSS 变量不得漂移：每个颜色 token 必须以同名 CSS 变量出现。"""
        for name, value in TOKENS.items():
            if len(value) == 7:  # #rrggbb 形态才进 CSS 变量
                self.assertIn(f"--{name}:{value}", CSS.replace(" ", ""),
                              f"TOKENS['{name}']={value} 未同步为 CSS 变量 --{name}")


if __name__ == "__main__":
    unittest.main()
