"""角色色板六查锁定（dataviz 口径）——双主题：light（默认）/ dark 各自全查。

数学内嵌自 dataviz skill 的 validate_palette.py（OKLab ΔE×100 +
Machado-Oliveira-Fernandes 2009 CVD 模拟 severity 1.0），纯标准库。
改 theme.LIGHT / theme.DARK 的角色色/文字阶/表面色，本测试必须保持全绿——
"顺手调个色"而打破 CVD 分离或对比度防线在这里直接红灯。

阈值（两主题同一套硬门槛，仅亮度带按主题分档）：
- 亮度带：dark 0.48–0.67（重要的更亮）；light 0.50–0.62（重要的更暗）；
- 彩度 C ≥ 0.10（低于读作灰）；
- 相邻对 CVD ΔE ≥ 8（无次级编码豁免，按硬门槛锁）；
- 正常视觉最差相邻对 ≥ 15；
- 角色色对 bg 与 panel 双面 ≥ 3:1；
- 文字色阶（*-b / cyan-l / t1–t3）对 panel ≥ 4.5:1。
"""
import math
import unittest

from entity_arena.theme import CSS, PALETTES

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


# 各主题的亮度带（dark：重要的更亮；light：重要的更暗）
BANDS = {"light": (0.50, 0.62), "dark": (0.48, 0.67)}


class TestRolePaletteSixChecks(unittest.TestCase):
    """三角色色 = [violet(baseline), cyan(微调), red(错误断言)] 相邻排布（网络图
    红线与青线同场、柱图 紫|青 并排），按相邻对口径校验；light/dark 逐主题全查。"""

    TRIO = ["violet", "cyan", "red"]  # 槽位顺序 = 并排顺序

    def test_lightness_band_and_chroma(self):
        for theme, pal in PALETTES.items():
            lo, hi = BANDS[theme]
            for name in self.TRIO:
                L, C = _oklch(pal[name])
                with self.subTest(theme=theme, name=name):
                    self.assertGreaterEqual(L, lo, f"{name} L={L:.3f} 低于 {theme} 带下沿")
                    self.assertLessEqual(L, hi, f"{name} L={L:.3f} 高于 {theme} 带上沿")
                    self.assertGreaterEqual(C, 0.10, f"{name} C={C:.3f} 低于彩度地板（读作灰）")

    def test_adjacent_cvd_separation(self):
        for theme, pal in PALETTES.items():
            for i in range(len(self.TRIO) - 1):
                a, b = pal[self.TRIO[i]], pal[self.TRIO[i + 1]]
                for kind in ("protan", "deutan"):
                    d = _delta_e(a, b, kind)
                    with self.subTest(theme=theme, pair=f"{self.TRIO[i]}↔{self.TRIO[i+1]}", kind=kind):
                        self.assertGreaterEqual(
                            d, 8.0, f"CVD ΔE={d:.1f} < 8（无次级编码豁免）")

    def test_normal_vision_floor(self):
        for theme, pal in PALETTES.items():
            worst = min(_delta_e(pal[self.TRIO[i]], pal[self.TRIO[i + 1]])
                        for i in range(len(self.TRIO) - 1))
            with self.subTest(theme=theme):
                self.assertGreaterEqual(
                    worst, 15.0, f"正常视觉最差相邻对 ΔE={worst:.1f} < 15（硬门槛）")

    def test_contrast_vs_both_surfaces(self):
        for theme, pal in PALETTES.items():
            for name in self.TRIO:
                for surf in ("bg", "panel"):
                    r = _contrast(pal[name], pal[surf])
                    with self.subTest(theme=theme, name=name, surf=surf):
                        self.assertGreaterEqual(r, 3.0, f"对比度 {r:.2f} < 3:1")

    def test_colored_text_variants(self):
        """彩色文字阶（图注/轴标/命令），对各自 panel ≥ 4.5:1。"""
        for theme, pal in PALETTES.items():
            for name in ("cyan-b", "violet-b", "red-b", "cyan-l"):
                r = _contrast(pal[name], pal["panel"])
                with self.subTest(theme=theme, name=name):
                    self.assertGreaterEqual(r, 4.5, f"vs panel 对比度 {r:.2f} < 4.5:1")

    def test_text_tokens(self):
        for theme, pal in PALETTES.items():
            for name in ("t1", "t2", "t3"):
                r = _contrast(pal[name], pal["panel"])
                with self.subTest(theme=theme, name=name):
                    self.assertGreaterEqual(r, 4.5, f"vs panel 对比度 {r:.2f} < 4.5:1")

    def test_signature_gradient_is_chrome_only(self):
        """平台家族签名渐变（对齐 INF 控制台）：双主题 token 在场，且绝不染指数据编码。"""
        css = CSS.replace(" ", "")
        self.assertIn("--sig-a:#22d3ee", css)   # dark 档（控制台同款）
        self.assertIn("--sig-b:#a78bfa", css)
        self.assertIn("--sig-a:#0895b5", css)   # light 档（压深保可读）
        self.assertIn("--sig-b:#7a6bf0", css)
        self.assertIn("linear-gradient(135deg,var(--sig-a),var(--sig-b))", css)
        # 数据标记（柱/点）只允许实色角色色，渐变不得渗入
        for fill_rule in (".mb-t{fill:", ".mb-b{fill:", ".ob-t{fill:", ".ob-b{fill:"):
                    i = css.find(fill_rule)
                    self.assertNotEqual(i, -1, f"{fill_rule} 规则失踪")
                    self.assertNotIn("sig", css[i:i + 60],
                                     f"{fill_rule} 沾上签名渐变——数据编码必须实色")

    def test_tokens_and_css_in_sync(self):
        """两套调色板与 CSS 变量不得漂移：每个 hex 形态 token 必须以同名 CSS 变量出现。"""
        for theme, pal in PALETTES.items():
            for name, value in pal.items():
                if len(value) == 7:  # #rrggbb 形态才进 CSS 变量断言
                    with self.subTest(theme=theme, name=name):
                        self.assertIn(f"--{name}:{value}", CSS.replace(" ", ""),
                                      f"{theme}['{name}']={value} 未同步为 CSS 变量 --{name}")

    def test_theme_switch_mechanism_present(self):
        """切换机制齐备（CSS 侧）：:root 携带浅色 + dark 整块覆盖。"""
        self.assertIn(':root{', CSS.replace(" ", ""))
        self.assertIn('html[data-theme="dark"]{', CSS.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
