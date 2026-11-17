"""W4 租户 MCP 交付打包：--mcp-dir 七件套 + er_mcp_server 租户口径（ER_META）
+ 三方一致性验收（MCP 离线 == 页面试玩台 == er-gateway 在线）。

不碰真网络：MCP 走 stdio 子进程（bundle 拷贝，ER_META/ER_VILLAGE/ER_SCORES
指进临时目录），gateway 走 embed_fn 注入 stub（test_play_live_server 模式）。
Febrl 默认路径（无 ER_META）的行为不变由 tests/test_mcp_server.py 锁定。
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import test_tenant_cases as tt  # noqa: E402  复用 fixture（单一事实来源）
from test_mcp_server import McpClient  # noqa: E402

from entity_arena.gateway import Gateway, Tenant  # noqa: E402
from entity_arena.intake import load_fieldmap  # noqa: E402


class FakeEmbed:
    """stub：按文本注册向量（成对工艺向量——每对测试对独占 rid，互不干扰）。"""

    def __init__(self):
        self.vecs: dict[str, list[float]] = {}

    @staticmethod
    def pair(cos: float) -> list[list[float]]:
        return [[1.0, 0.0], [cos, math.sqrt(max(0.0, 1.0 - cos * cos))]]

    def __call__(self, endpoint, model, token, texts):
        out = []
        for t in texts:
            if t not in self.vecs:
                raise AssertionError(f"FakeEmbed 未注册文本：{t!r}")
            out.append(self.vecs[t])
        return out


class DeliveryBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-mcp-delivery-"))
        tt._fixture_dir(cls.tmp)
        cls.scores = tt._fixture_scores(cls.tmp)
        cls.mcp_dir = cls.tmp / "mcp_bundle"
        cls.out = cls.tmp / "cases.json"
        tt.run("scripts/build_tenant_cases.py",
               "--records", str(cls.tmp / "records.csv"),
               "--fieldmap", str(cls.tmp / "fieldmap.csv"),
               "--scores", str(cls.scores),
               "--dataset-label", "Pilot Co records",
               "--truth-label", "Pilot labels",
               "--out", str(cls.out),
               "--mcp-dir", str(cls.mcp_dir))
        cls.payload = json.loads(cls.out.read_text(encoding="utf-8"))
        cls.matrix = {(a, b): s for a, b, s in
                      json.loads((cls.mcp_dir / "scores.json").read_text(
                          encoding="utf-8"))["pairs"]}

    @classmethod
    def mcp(cls, **extra):
        env = {"ER_VILLAGE": str(cls.mcp_dir / "records.json"),
               "ER_SCORES": str(cls.mcp_dir / "scores.json"),
               "ER_META": str(cls.mcp_dir / "er_meta.json"), **extra}
        return McpClient(server=str(cls.mcp_dir / "er_mcp_server.py"),
                         env_extra=env)


class TestMcpBundle(DeliveryBase):
    """七件套齐 + 内容与 cases 产物同源（θ/模型/字段表不漂移）。"""

    FILES = ["er_mcp_server.py", "embed_client.py", "records.json", "scores.json",
             "er_meta.json", "er_mcp.env", "START.md"]

    def test_files_present_and_copies_verbatim(self):
        for name in self.FILES:
            self.assertTrue((self.mcp_dir / name).exists(), f"缺 {name}")
        self.assertEqual((self.mcp_dir / "er_mcp_server.py").read_text(encoding="utf-8"),
                         (ROOT / "scripts" / "er_mcp_server.py").read_text(encoding="utf-8"),
                         "交付包服务器与仓库版本不同步（重跑构建即同步）")
        self.assertEqual((self.mcp_dir / "embed_client.py").read_text(encoding="utf-8"),
                         (ROOT / "src" / "entity_arena" / "embed_client.py")
                         .read_text(encoding="utf-8"))

    def test_records_village_shape(self):
        data = json.loads((self.mcp_dir / "records.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["records"]), 22)
        r0 = data["records"][0]
        for k in ("rid", "entity", "fields", "text"):
            self.assertIn(k, r0)
        self.assertGreater(len(data["shadow_pairs"]), 0)

    def test_scores_canonical(self):
        sc = json.loads((self.mcp_dir / "scores.json").read_text(encoding="utf-8"))
        self.assertEqual(sc["model"], "fixture-er-model")
        self.assertEqual(sc["n_records"], 22)
        self.assertEqual(len(sc["pairs"]), 22 * 21 // 2)
        self.assertAlmostEqual(self.matrix[("r001", "r002")], 0.99, places=6)

    def test_er_meta_same_source_as_cases(self):
        meta = json.loads((self.mcp_dir / "er_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["theta"], self.payload["theta"])  # θ 只认 cases 产物
        self.assertEqual(meta["model"], self.payload["model"])
        self.assertEqual(meta["label"], "Pilot Co records")
        self.assertEqual(meta["truth_label"], "Pilot labels")
        self.assertEqual([f[0] for f in meta["fields"]], ["name", "city"])
        self.assertIn("build_tenant_cases", meta["theta_src"])

    def test_env_and_start_no_token_leak(self):
        env = (self.mcp_dir / "er_mcp.env").read_text(encoding="utf-8")
        for k in ("ER_VILLAGE", "ER_SCORES", "ER_META"):
            self.assertIn(k, env)
        self.assertIn("# export EMB_TOKEN", env)  # 令牌只以注释指引出现
        start = (self.mcp_dir / "START.md").read_text(encoding="utf-8")
        self.assertIn("claude mcp add entity-er", start)
        self.assertIn(str(self.mcp_dir.resolve()), start)
        # EMB_TOKEN 只许以占位符/空值出现——真实令牌绝不进交付物
        import re
        leaks = [m.group(1) for m in re.finditer(r"EMB_TOKEN=(\S*)", start)
                 if m.group(1).strip("`") not in ("", "<平台令牌>")]
        self.assertEqual(leaks, [], "START.md 里出现了疑似真实令牌")

    def test_page_labels_conn_keys(self):
        pl = self.payload["page_labels"]
        b = str(self.mcp_dir.resolve())
        self.assertIn(b, pl["conn.install.cmd"]["en"])  # 注册命令指向交付包
        self.assertIn("ER_META", pl["conn.install.cfg"]["en"])
        self.assertIn("{TH}", pl["conn.proto"]["en"])   # θ 仍由页面运行期注入
        self.assertIn("Pilot labels", pl["conn.proto"]["en"])
        self.assertIn("231", pl["conn.mode.off.t"]["en"])  # 全量对数实算
        self.assertIn("dataset rid pairs", pl["home.v.connect"]["en"])
        self.assertNotIn("village", pl["conn.sub"]["en"].lower())
        # 容量实测卡（平台共享 bench）：去案例村/仓库脚本措辞，日期仍运行期注入
        self.assertIn("{D}", pl["conn.bench.sub"]["en"])
        self.assertNotIn("village", pl["conn.bench.sub"]["en"].lower())
        self.assertNotIn("228", pl["conn.bench.note"]["en"])
        self.assertNotIn("scripts/", pl["conn.bench.note"]["en"])
        ex2 = pl["conn.ex2"]["en"]
        self.assertRegex(ex2, r"Score r\d+ vs r\d+")


class TestTenantMcpServer(DeliveryBase):
    """bundle 拷贝 + ER_META：标签/字段表/θ 全按租户口径，零 Febrl 残留。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.mcp()
        cls.client.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "unittest"}})
        cls.client.notify("notifications/initialized")

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_about_tenant_labeling(self):
        text, err = self.client.tool_text("er_about")
        self.assertFalse(err)
        self.assertIn("Pilot Co records", text)
        self.assertIn("fixture-er-model", text)
        self.assertIn("22 records", text)
        self.assertIn("0.9650", text)  # θ 与 cases 产物同源
        self.assertNotIn("Febrl3", text)
        self.assertIn("dataset", text)
        self.assertIn("START.md", text)  # 文档指引指向交付包，不是仓库 docs/

    def test_score_pair_offline_matches_matrix(self):
        text, err = self.client.tool_text("er_score_pair", {"a": "r001", "b": "r002"})
        self.assertFalse(err)
        self.assertIn("cosine similarity = 0.9900", text)
        self.assertIn("decision line θ    = 0.9650", text)
        self.assertIn("VERDICT: LINKED", text)
        self.assertIn("Pilot labels: SAME identity — assertion CORRECT", text)
        self.assertIn("pre-sampled real matrix", text)
        self.assertNotIn("Febrl3", text)

    def test_score_pair_refused_cross_pair(self):
        text, err = self.client.tool_text("er_score_pair", {"a": "r017", "b": "r019"})
        self.assertFalse(err)
        self.assertIn("cosine similarity = 0.9600", text)
        self.assertIn("VERDICT: REFUSED", text)
        self.assertIn("Pilot labels: DIFFERENT identities", text)

    def test_field_diff_uses_tenant_fields(self):
        text, _ = self.client.tool_text("er_score_pair", {"a": "r005", "b": "r006"})
        self.assertIn("name", text)  # 字段表是 name/city，不是 Febrl 五字段
        self.assertIn("city", text)
        self.assertNotIn("ssn", text)
        self.assertIn("←", text)

    def test_find_records_tenant_fields(self):
        text, err = self.client.tool_text("er_find_records", {"q": "carol"})
        self.assertFalse(err)
        self.assertIn("r005", text)
        self.assertIn("name: carol white", text)
        self.assertNotIn("ssn", text)

    def test_free_text_error_mentions_dataset(self):
        text, err = self.client.tool_text("er_score_pair",
                                          {"a": "name: x", "b": "name: y"})
        self.assertTrue(err)
        self.assertIn("EMB_TOKEN", text)
        self.assertIn("dataset rids", text)  # 不是 "village rids"
        self.assertNotIn("village", text)

    def test_tools_list_tenant_descriptions(self):
        r = self.client.request("tools/list")
        descs = {t["name"]: t["description"] for t in r["result"]["tools"]}
        self.assertIn("Pilot Co records", descs["er_find_records"])
        self.assertIn("22 records / 13 identities", descs["er_find_records"])
        self.assertIn("name/city", descs["er_find_records"])
        self.assertIn("Pilot labels", descs["er_score_pair"])
        self.assertNotIn("Febrl3", json.dumps(r["result"], ensure_ascii=False))


class TestThreeWayConsistency(DeliveryBase):
    """验收：er_score_pair 离线矩阵判定 == 页面试玩台判定 == er-gateway 在线判定。

    页面侧判定 = cases 产物 links（试玩台矩阵即由同一份分数量化而来，
    量化判定一致由构建期断言保证）；gateway 侧 embed_fn 注入成对工艺向量
    （每对测试对独占 rid，向量余弦=入库分数——在线=入库的 stub 化身）。
    """

    PAIRS = [("r001", "r002", 0.99),   # 精确孪生 → link
             ("r005", "r006", 0.965),  # 错字对 s=θ → link（边界）
             ("r001", "r003", None),   # 异实体 → refuse（分数取矩阵）
             ("r017", "r019", 0.96)]   # 同名异实体最像的拒连

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.mcp()
        cls.client.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "unittest"}})
        cls.client.notify("notifications/initialized")
        cls.text_of = {r["rid"]: r["text"] for r in json.loads(
            (cls.mcp_dir / "records.json").read_text(encoding="utf-8"))["records"]}
        # 页面试玩台判定：links 集合（构建期已断言量化判定与全精度一致）
        cls.page_links = {frozenset((p["a"], p["b"])) for p in cls.payload["links"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def _gw(self, fake: FakeEmbed) -> Gateway:
        t = Tenant(name="pilot", label="Pilot Co records", endpoint="http://stub",
                   model="fixture-er-model", theta=self.payload["theta"],
                   fields=load_fieldmap(self.tmp / "fieldmap.csv"),
                   cases_path=self.out, page=None, api_key=None)
        return Gateway(tenants={"pilot": t}, token="stub-token",
                       default_tenant="pilot", embed_fn=fake)

    def test_verdicts_agree(self):
        fake = FakeEmbed()
        fields = {r["rid"]: r["fields"] for r in json.loads(
            (self.mcp_dir / "records.json").read_text(encoding="utf-8"))["records"]}
        for a, b, _s in self.PAIRS:
            with self.subTest(pair=f"{a}×{b}"):
                score = self.matrix[(a, b)]
                # MCP 离线
                mcp_text, err = self.client.tool_text("er_score_pair", {"a": a, "b": b})
                self.assertFalse(err, mcp_text)
                self.assertIn(f"cosine similarity = {score:.4f}", mcp_text)
                mcp_verdict = "link" if "VERDICT: LINKED" in mcp_text else "refuse"
                # 页面试玩台
                page_verdict = ("link" if frozenset((a, b)) in self.page_links
                                else "refuse")
                self.assertEqual(mcp_verdict, page_verdict,
                                 "MCP 离线判定与页面试玩台判定不一致")
                # er-gateway 在线（stub 向量=入库分数）。精确孪生对（序列化逐字节
                # 相同）任何 embedder 都只给 1.0，不在"在线=入库"可证范围
                # （与 W3 test_er_gateway 同一豁免）——只对文本可区分对做网关断言。
                if self.text_of[a] != self.text_of[b]:
                    va, vb = FakeEmbed.pair(score)
                    fake.vecs[self.text_of[a]] = va
                    fake.vecs[self.text_of[b]] = vb
                    gw = self._gw(fake)
                    res = gw.score("pilot", fields[a], fields[b])
                    self.assertEqual(res["mode"], "structured")
                    self.assertAlmostEqual(res["score"], score, places=6,
                                           msg="gateway 在线分数应等于入库矩阵值")
                    self.assertEqual(res["verdict"], mcp_verdict,
                                     "gateway 与 MCP 判定不一致")
                    self.assertEqual(res["verdict"], page_verdict,
                                     "gateway 与页面判定不一致")

    def test_theta_edge_is_linked_and_unstable(self):
        """s=θ 的边界对（r005×r006=0.965）：判定 link、stable=false（|margin|<0.01）
        ——三方同口径的边界行为。"""
        text, _ = self.client.tool_text("er_score_pair", {"a": "r005", "b": "r006"})
        self.assertIn("VERDICT: LINKED", text)
        self.assertIn("(+0.0000 above θ)", text)
        fake = FakeEmbed()
        va, vb = FakeEmbed.pair(0.965)
        fake.vecs[self.text_of["r005"]] = va
        fake.vecs[self.text_of["r006"]] = vb
        gw = self._gw(fake)
        fields = {r["rid"]: r["fields"] for r in json.loads(
            (self.mcp_dir / "records.json").read_text(encoding="utf-8"))["records"]}
        res = gw.score("pilot", fields["r005"], fields["r006"])
        self.assertEqual(res["verdict"], "link")
        self.assertFalse(res["stable"])
        self.assertEqual(res["margin"], 0.0)


if __name__ == "__main__":
    unittest.main()
