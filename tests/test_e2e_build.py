"""端到端：数据生成 → 页面构建。确定性重建 + 单文件零外链契约。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = ("records.jsonl", "results.json", "metrics.json", "network.json",
              "queries.json", "pairs.json", "meta.json", "entities.json")
# Febrl3 实测轨数据（平台部署修复后由 fetch 脚本写入入库溯源文件）
VILLAGE_SCORES = ROOT / "data_open" / "village_scores.json"
HAS_OPEN_CASES = VILLAGE_SCORES.exists()


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="entity-arena-e2e-")
        cls.d1 = Path(cls.tmp) / "run1"
        cls.d2 = Path(cls.tmp) / "run2"
        run("scripts/make_mock_data.py", "--seed", "42", "--out", str(cls.d1))
        run("scripts/make_mock_data.py", "--seed", "42", "--out", str(cls.d2))
        # 开源基准轨数据不是 seed 生成的（真实实测）：由已入库的平台报告确定性重建
        run("scripts/update_open_benchmark.py", "--out", str(cls.d1 / "open_benchmark.json"))
        # 网络视图 Febrl3 实测轨：由已入库的成对分数确定性重建（无需服务在线）。
        # 数据未接入前（平台部署修复中）该步跳过，页面自动只出合成轨。
        if HAS_OPEN_CASES:
            for d in (cls.d1, cls.d2):
                run("scripts/build_open_cases.py",
                    "--village", str(ROOT / "data_open" / "village.json"),
                    "--scores", str(VILLAGE_SCORES),
                    "--out", str(d / "open_cases.json"))
        cls.out = Path(cls.tmp) / "demo.html"
        run("scripts/build_demo.py", "--data-dir", str(cls.d1), "--out", str(cls.out))
        cls.html = cls.out.read_text(encoding="utf-8")

    def test_regeneration_byte_identical(self):
        """同 seed 两次生成，8 个数据文件必须逐字节一致。"""
        for name in DATA_FILES:
            self.assertEqual((self.d1 / name).read_bytes(), (self.d2 / name).read_bytes(),
                             f"{name} 不是确定性的")

    def test_all_data_files_written(self):
        for name in DATA_FILES:
            self.assertTrue((self.d1 / name).exists(), f"缺 {name}")

    @unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库（平台部署修复后接入）")
    def test_open_cases_deterministic(self):
        """Febrl3 实测轨：同分数两次重建逐字节一致（--scores 只读复算）。"""
        self.assertEqual((self.d1 / "open_cases.json").read_bytes(),
                         (self.d2 / "open_cases.json").read_bytes())

    @unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库（平台部署修复后接入）")
    def test_explain_real_cards_in_page(self):
        """Explain 实测卡必须打进页面：R1-R6 卡、基线不可部署注脚、?case= 深链。"""
        for marker in ('"pair_id":"R1"', '"pair_id":"R2"', '"pair_id":"R3"',
                       "exp.realonly", "qs.get('case')", "Febrl3"):
            self.assertIn(marker, self.html)

    def test_open_baseline_stage_in_page(self):
        """网络实测轨三态必须打进页面：基线代理数据 + 状态文案 + ?stage= 深链。"""
        for marker in ('"kind":"proxy"', "net.open.state.baseline",
                       "net.open.btn.base", "qs.get('stage')"):
            self.assertIn(marker, self.html)
        # 漏连可视化：红虚线桥 + ⊘ 断口标记 + 图例词条（误连/漏连在图上可分辨）
        for marker in ("missmark", "buildMissGroup", "net.open.lg.blink.miss"):
            self.assertIn(marker, self.html)

    def test_synth_three_stage_in_page(self):
        """合成轨三态必须打进页面：network.json 基线块（双模型断言组件）+
        轨内状态文案 + 微调态诚实漏连渲染路径。"""
        for marker in ('"baseline":{"kind":"proxy"', "net.syn.state.baseline",
                       "net.syn.state.after", "net.syn.base.note", "renderNetStats"):
            self.assertIn(marker, self.html)
        # 剧本契约：基线又误连又漏连、微调零误连 + ≥1 诚实漏连（数据层已锁定，
        # 这里锁"这些数字真的进页面"）
        self.assertIn('"false_links":4', self.html)   # 合成基线误连（seed 42）

    @unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库（平台部署修复后接入）")
    def test_dual_track_switch_in_page(self):
        """全局数据集切换器必须打进页面：header trackBar + Febrl 查询 + 双轨分派。"""
        for marker in ('id="trackBar"', "renderTrackBar", "applyTrackChrome",
                       '"query_id":"F1"', "arena.sub.open", "footer.note.open",
                       "badge.open", "sb.proto.open"):
            self.assertIn(marker, self.html)

    @unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库（平台部署修复后接入）")
    def test_playground_matrix_in_page(self):
        """试玩台离线矩阵必须打进页面（b64 全量对 + 量程下限）。"""
        for marker in ('"play":{"n":', '"order":["rec-', '"lo":', "playRevealed"):
            self.assertIn(marker, self.html)

    def test_play_free_input_in_page(self):
        """试玩台「直接输入两条」：双页签 + 在线打分（同源相对路径，零 URL 字符串）+
        双 rid 走内嵌矩阵的捷径 + ?fa=/?fb= 深链 + 失败人话提示。"""
        for marker in ('data-tab="free"', 'id="plTaA"', 'id="plTaB"', 'id="plGo"',
                       "playFreeScore", "fetch('api/score'", "play.free.mode.live",
                       "play.free.cmd", "qs.get('fa')", "qs.get('fb')",
                       "pl-verdict-chip.no", "mode: 'matrix'"):
            self.assertIn(marker, self.html)

    def test_page_markers_present(self):
        for marker in ('id="view-home"', 'id="view-arena"', 'id="view-network"', 'id="view-explain"',
                       'id="view-scoreboard"', 'id="view-play"', 'id="view-connect"',
                       'id="openBench"',
                       "switchNetTrack", "openNetSide", "const DATA = {",
                       "renderConnect", "er_score_pair", "renderPlay", "playScore"):
            self.assertIn(marker, self.html)

    def test_home_guide_view_in_page(self):
        """导览视图（默认落地页）：入口文案 + 三步叙事直达网络三态 + 双轨卡切换。"""
        for marker in ('id="view-home"', "renderHome", "home.lede", "home.step2.t.open",
                       "data-goto", "data-stage", "data-trackgo", "nav.home"):
            self.assertIn(marker, self.html)

    def test_theme_toggle_in_page(self):
        """黑白主题：默认浅色 + 先行脚本防闪 + 顶栏切换钮 + 双套调色板变量块。"""
        for marker in ('data-theme="light"', "ea_theme", 'id="themeBtn"',
                       "applyThemeBtn", "html[data-theme=\"dark\"]"):
            self.assertIn(marker, self.html)
        # 先行脚本必须出现在 <body> 之前（首帧前定主题，防闪）
        head = self.html.split("<body>")[0]
        self.assertIn("ea_theme", head)

    @unittest.skipUnless((ROOT / "data_open" / "bench_embeddings.json").exists(),
                         "data_open/bench_embeddings.json 未入库（压测后接入）")
    def test_bench_capacity_card_in_page(self):
        """connect 视图容量实测卡：压测数据 + 渲染函数必须真的进页面，
        且数据文件绝不携带 URL（零外链契约不破——压测目标是部署服务地址）。"""
        for marker in ('"bench":{', "renderBench", "conn.bench.h",
                       "rate_limit_at_threads"):
            self.assertIn(marker, self.html)
        bench = json.loads((ROOT / "data_open" / "bench_embeddings.json").read_text())
        self.assertNotIn("http", json.dumps(bench).lower())

    def test_zero_external_links(self):
        """离线契约：成品页不得引用任何外部资源。

        唯一例外：SVG 命名空间常量 http://www.w3.org/2000/svg ——
        它是 W3C 标识符（createElementNS 用），浏览器不会发起请求。
        """
        body = self.html.replace("http://www.w3.org/2000/svg", "")
        self.assertNotIn("http://", body)
        self.assertNotIn("https://", body)
        self.assertNotIn("<link", body.lower())
        self.assertNotIn("<img", body.lower())

    def test_no_unreplaced_placeholders(self):
        self.assertNotIn("{{", self.html)

    def test_script_blocks_wellformed(self):
        """每个 script 块独立看都应是平衡的 JS（粗检：花括号/圆括号配平）。"""
        blocks = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertGreaterEqual(len(blocks), 1)
        for i, b in enumerate(blocks):
            self.assertEqual(b.count("{"), b.count("}"), f"script#{i} 花括号不配平")
            self.assertEqual(b.count("("), b.count(")"), f"script#{i} 圆括号不配平")
            self.assertNotIn("<body>", b)

    def test_build_is_single_file_reasonable_size(self):
        kb = self.out.stat().st_size / 1024
        self.assertGreater(kb, 50, "页面异常地小 —— 数据可能没打进去")
        # 上限 650：试玩台嵌入 25,878 对量化真实分数矩阵（b64 ~67KB）
        self.assertLess(kb, 650, "页面异常地大")


if __name__ == "__main__":
    unittest.main()
