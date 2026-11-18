"""W2 租户案例集：cases_core 抽取回归锚 + 租户路径锚 + 最小 fixture 全链。

锚 A（TestFebrlRebuildAnchor）：build_open_cases.py 重构为 Febrl 适配器后，
从入库分数重建的 data/open_cases.json 必须与仓库现行文件**逐字节一致**——
统计核心抽到 cases_core.py 的整个过程不允许改变任何数字或文案。

锚 B（TestVillageViaTenantAnchor）：Febrl3 案例村改走**租户路径**
（records CSV + fieldmap（带 Febrl 基线权重）+ scores + 显式影子对）重建，
结构化指标（θ/stats/links/components/metrics/play/network/baseline 全量）
必须与 open_cases.json 逐字段一致——证明"租户入口与 Febrl 入口共用同一份
统计口径"；文案字段（title/story/note/stubs/text/provenance）按设计不同
（租户序列化与叙事另一套），不在比对范围。

最小租户 fixture（TestMinimalTenant*）：两字段小数据集 + 确定性合成分数，
跑通 build_tenant_cases → build_demo 全链 self-check，锁定 page_labels /
play_fields 进页面、错误路径人话报错、影子对挖掘规则与桶上限。
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VILLAGE = ROOT / "data_open" / "village.json"
VILLAGE_SCORES = ROOT / "data_open" / "village_scores.json"
REF_OPEN_CASES = ROOT / "data" / "open_cases.json"
HAS_OPEN_CASES = VILLAGE_SCORES.exists() and REF_OPEN_CASES.exists()

# 与 build_open_cases.py 的 _FIELD_LABELS/_BASE_W 对齐（锚 B 用，使基线代理同口径）
FEBRL_FIELDS = [("name", 0.40), ("dob", 0.20), ("address", 0.20),
                ("locality", 0.10), ("ssn", 0.10)]


def run(*args: str, expect_fail: bool = False) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    if expect_fail:
        if p.returncode == 0:
            raise AssertionError(f"预期失败但成功：{args}\n{p.stdout}")
    elif p.returncode != 0:
        raise AssertionError(f"命令失败：{args}\n{p.stdout}\n{p.stderr}")
    return p


def write_village_csv(village: dict, path: Path) -> None:
    """village.json → 格式 A 记录 CSV（保持 village 记录序——锚 B 的定向基础）。"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "entity_id", "name", "dob", "address", "locality", "ssn"])
        for r in village["records"]:
            fl = r["fields"]
            w.writerow([r["rid"], r["entity"], fl.get("name", ""), fl.get("dob", ""),
                        fl.get("address", ""), fl.get("locality", ""), fl.get("ssn", "")])


def write_febrl_fieldmap(path: Path) -> None:
    labels = {"name": "Name|姓名", "dob": "Date of birth|生日", "address": "Address|地址",
              "locality": "Locality|地区", "ssn": "ID Number|证件号"}
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["field", "role", "normalize", "neg_key", "baseline_weight", "label"])
        for name, wgt in FEBRL_FIELDS:
            w.writerow([name, "embedding", "", "1", f"{wgt:.2f}", labels[name]])


@unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库")
class TestFebrlRebuildAnchor(unittest.TestCase):
    """锚 A：Febrl 适配器重构 = 逐字节一致（改 cases_core 统计逻辑先跑这里）。"""

    def test_rebuild_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix="ea-anchor-a-") as tmp:
            out = Path(tmp) / "open_cases.json"
            run("scripts/build_open_cases.py",
                "--village", str(VILLAGE), "--scores", str(VILLAGE_SCORES),
                "--out", str(out))
            self.assertEqual(out.read_bytes(), REF_OPEN_CASES.read_bytes(),
                             "重构改变了 Febrl3 实测轨输出——统计核心抽取引入漂移")


@unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库")
class TestVillageViaTenantAnchor(unittest.TestCase):
    """锚 B：案例村走租户路径，结构化指标与 open_cases.json 逐字段一致。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-anchor-b-"))
        village = json.loads(VILLAGE.read_text(encoding="utf-8"))
        write_village_csv(village, cls.tmp / "records.csv")
        write_febrl_fieldmap(cls.tmp / "fieldmap.csv")
        (cls.tmp / "shadow.json").write_text(json.dumps(village["shadow_pairs"]), encoding="utf-8")
        cls.out = cls.tmp / "tenant_cases.json"
        run("scripts/build_tenant_cases.py",
            "--records", str(cls.tmp / "records.csv"),
            "--fieldmap", str(cls.tmp / "fieldmap.csv"),
            "--scores", str(VILLAGE_SCORES),
            "--shadow-pairs", str(cls.tmp / "shadow.json"),
            "--name-field", "name",
            "--dataset-label", "Anchor B", "--truth-label", "Febrl3 truth",
            "--out", str(cls.out))
        cls.got = json.loads(cls.out.read_text(encoding="utf-8"))
        cls.ref = json.loads(REF_OPEN_CASES.read_text(encoding="utf-8"))

    def test_structural_payload_identical(self):
        """θ/断言链/组件/矩阵/网络/基线全量一致（文案豁免）。"""
        for k in ("model", "embedding_dim", "theta", "stats", "links", "false_links",
                  "missed_pairs", "components", "comp_of", "top3", "play", "network"):
            self.assertEqual(self.got[k], self.ref[k], f"字段 {k} 不一致")

    def test_records_fields_entity_identical(self):
        """记录的 fields/entity 一致；text 按设计不同（intake 序列化口径）。"""
        for rid, ref in self.ref["records"].items():
            got = self.got["records"][rid]
            self.assertEqual(got["fields"], ref["fields"], rid)
            self.assertEqual(got["entity"], ref["entity"], rid)
            self.assertTrue(got["text"].startswith("name: "), "intake 序列化口径")

    def test_baseline_block_identical(self):
        for k in ("kind", "theta", "stats", "links", "false_links", "missed_pairs",
                  "components", "comp_of", "network"):
            self.assertEqual(self.got["baseline"][k], self.ref["baseline"][k], f"baseline.{k}")

    def test_metrics_identical(self):
        self.assertEqual(self.got["metrics"], self.ref["metrics"])

    def test_arena_structure_identical(self):
        g, r = self.got["arena"], self.ref["arena"]
        for k in ("k", "theta", "absent"):
            self.assertEqual(g[k], r[k], k)
        self.assertEqual(len(g["queries"]), len(r["queries"]))
        for qg, qr in zip(g["queries"], r["queries"]):
            for k in ("query_id", "record_id", "scenario", "results"):
                self.assertEqual(qg[k], qr[k], f"{qg['query_id']}.{k}")

    def test_explain_structure_identical(self):
        g, r = self.got["explain"], self.ref["explain"]
        self.assertEqual([c["pair_id"] for c in g["pairs"]], [c["pair_id"] for c in r["pairs"]])
        for cg, cr in zip(g["pairs"], r["pairs"]):
            for k in ("pair_id", "a", "b", "real", "truth", "fields", "scores",
                      "verdicts", "theta", "model"):
                self.assertEqual(cg[k], cr[k], f"{cg['pair_id']}.{k}")

    def test_tenant_extras_present(self):
        """租户附加键：页面文案覆盖 + 试玩台字段表（Febrl 主构建不带）。"""
        self.assertIn("page_labels", self.got)
        self.assertIn("play_fields", self.got)
        self.assertEqual(self.got["play_fields"], [n for n, _ in FEBRL_FIELDS])
        for key in ("net.track.open", "play.truth.same", "footer.note.open", "badge.open"):
            self.assertIn(key, self.got["page_labels"])
        self.assertNotIn("Febrl3", json.dumps(self.got["page_labels"], ensure_ascii=False)
                         .replace("Febrl3 truth", ""))  # truth-label 参数原样透传，其余键不提 Febrl3

    def test_provenance_rules_complete(self):
        """验收 ③：θ 扫描/基线代理/missed/metrics/arena 规则全文在 provenance。"""
        pv = self.got["provenance"]
        for key in ("theta_rule", "missed_rule", "baseline_rule", "metrics_rule",
                    "arena_rule", "explain_rule", "play_rule", "shadow_rule", "truth"):
            self.assertIn(key, pv)
        self.assertIn("link-F1", pv["theta_rule"])
        self.assertIn("字面相似度", pv["baseline_rule"])


# ---- 最小租户 fixture：两字段、确定性合成分数 ----

FIX_ROWS = [
    # rid, entity, name, city —— 覆盖：精确孪生/错字/缺失/同名异实体（影子）
    ("r001", "E01", "alice smith", "springfield"),
    ("r002", "E01", "alice smith", "springfield"),
    ("r003", "E02", "bob jones", "springfield"),
    ("r004", "E02", "bob jones", "riverton"),      # 城市冲突（基线漏连候选）
    ("r005", "E03", "carol white", "madison"),
    ("r006", "E03", "carol whte", "madison"),      # 错字
    ("r007", "E04", "dave brown", "riverton"),
    ("r008", "E04", "dave brwn", "riverton"),      # 错字
    ("r009", "E05", "erin davis", ""),
    ("r010", "E05", "erin davis", "springfield"),  # 单侧缺失
    ("r011", "E06", "frank miller", "madison"),
    ("r012", "E06", "frank mille", "madison"),
    ("r013", "E07", "grace le", ""),
    ("r014", "E07", "grace lee", "springfield"),   # 缺失+错字（基线低分）
    ("r015", "E08", "henry wilson", "springfield"),   # 影子：同城异实体
    ("r016", "E09", "irene taylor", "springfield"),
    ("r017", "E10", "jack moore", "riverton"),     # 同名同城异实体（基线误连候选）
    ("r018", "E10", "jack moore", "riverton"),
    ("r019", "E11", "jack moore", "riverton"),
    ("r020", "E11", "jack moore", "riverton"),
    ("r021", "E12", "kate nelson", "madison"),
    ("r022", "E13", "kate nelsn", "madison"),
]


def _fixture_dir(path: Path) -> Path:
    with open(path / "records.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "entity_id", "name", "city"])
        w.writerows(FIX_ROWS)
    (path / "fieldmap.csv").write_text(
        "field,role,normalize,neg_key\nname,embedding,,1\ncity,embedding,,1\n",
        encoding="utf-8")
    return path


def _fixture_scores(path: Path) -> Path:
    """确定性合成分数：同实体 0.99（两对错字 0.965、E02=0.90 漏连）；
    同名异实体（jack ×）0.91（其中一对 0.96=最像的拒连）；其余 0.40+。
    → θ 扫描选中 0.965（F1 最大）：8/9 真对断言、1 漏连、误连 0，
    R1 边距 0.99-0.965=0.025 ≥0.01。"""
    ent = {r[0]: r[1] for r in FIX_ROWS}
    name = {r[0]: r[2] for r in FIX_ROWS}
    special_same = {frozenset(("r003", "r004")): 0.90,
                    frozenset(("r005", "r006")): 0.965,
                    frozenset(("r007", "r008")): 0.965}
    special_cross = {frozenset(("r017", "r019")): 0.96}
    rids = [r[0] for r in FIX_ROWS]
    pairs = []
    for i, a in enumerate(rids):
        for b in rids[i + 1:]:
            key = frozenset((a, b))
            if ent[a] == ent[b]:
                s = special_same.get(key, 0.99)
            elif name[a] == name[b]:
                s = special_cross.get(key, 0.91)
            else:
                s = round(0.40 + (i % 7) * 0.01, 4)
            pairs.append([a, b, s])
    p = path / "scores.json"
    p.write_text(json.dumps({"model": "fixture-er-model", "dim": 8, "pairs": pairs}),
                 encoding="utf-8")
    return p


class TestMinimalTenant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-tenant-min-"))
        _fixture_dir(cls.tmp)
        cls.scores = _fixture_scores(cls.tmp)
        cls.out = cls.tmp / "cases.json"
        run("scripts/build_tenant_cases.py",
            "--records", str(cls.tmp / "records.csv"),
            "--fieldmap", str(cls.tmp / "fieldmap.csv"),
            "--scores", str(cls.scores),
            "--dataset-label", "Pilot Co records",
            "--truth-label", "Pilot labels",
            "--out", str(cls.out))
        cls.payload = json.loads(cls.out.read_text(encoding="utf-8"))

    def test_theta_and_assertions(self):
        """合成分数下 θ=0.965：8/9 真对断言、1 漏连、同名异实体全部拒连。"""
        self.assertEqual(self.payload["theta"], 0.965)
        self.assertEqual(self.payload["stats"]["tp"], 8)
        self.assertEqual(self.payload["stats"]["truth_pairs"], 9)
        self.assertEqual(self.payload["stats"]["false_links"], 0)
        self.assertEqual(self.payload["stats"]["missed_pairs"], 1)

    def test_baseline_proxy_false_merge(self):
        """等权基线把同名同城异实体并成一人（误连 >0）——R6 卡的池非空。"""
        self.assertGreater(self.payload["baseline"]["stats"]["false_links"], 0)
        ids = [c["pair_id"] for c in self.payload["explain"]["pairs"]]
        self.assertIn("R1", ids)
        self.assertIn("R6", ids)

    def test_shadow_pairs_mined(self):
        """默认挖掘：共享城市值的异实体对（桶 ≤8 全部计入）。"""
        prov = self.payload["provenance"]["shadow_rule"]
        self.assertIn("自动挖掘", prov)
        self.assertIn("city", prov)

    def test_shadow_bucket_cap(self):
        """桶上限：riverton 城市桶 7 成员，--shadow-max-bucket 6 时该桶的影子对
        （同城不同名，如 r004×r007）消失；同名桶（jack，4 成员）的对不受影响。"""
        name = {r[0]: r[2] for r in FIX_ROWS}
        riv_city_pairs = {(e["a"], e["b"]) for e in self.payload["network"]["edges_before"]
                          if e["a"].startswith("r0") and e["b"].startswith("r0")
                          and {e["a"], e["b"]} <= {r[0] for r in FIX_ROWS if r[3] == "riverton"}
                          and name[e["a"]] != name[e["b"]]}
        self.assertIn(("r004", "r007"), riv_city_pairs, "默认桶上限下同城影子对应存在")
        out2 = self.tmp / "cases_cap.json"
        run("scripts/build_tenant_cases.py",
            "--records", str(self.tmp / "records.csv"),
            "--fieldmap", str(self.tmp / "fieldmap.csv"),
            "--scores", str(self.scores),
            "--shadow-max-bucket", "6",
            "--out", str(out2))
        got = json.loads(out2.read_text(encoding="utf-8"))
        edges = {(e["a"], e["b"]) for e in got["network"]["edges_before"]}
        self.assertNotIn(("r004", "r007"), edges, "桶超上限的影子对应被剔除")
        self.assertIn(("r017", "r019"), edges, "同名桶（≤上限）的对保留")

    def test_page_labels_and_play_fields(self):
        self.assertEqual(self.payload["play_fields"], ["name", "city"])
        pl = self.payload["page_labels"]
        self.assertEqual(pl["net.track.open"]["en"], "Pilot Co records · real model")
        self.assertIn("Pilot labels", pl["play.truth.same"]["zh"])
        self.assertIn("θ", pl["sb.proto.open"]["zh"])
        self.assertIn("0.965", pl["sb.proto.open"]["en"])

    def test_page_labels_home_numbers_computed(self):
        """首页数字带的 Febrl 硬编码全部改为实算注入：影子对数/误连漏连/
        徽标/阶段 chip 占位符（数字运行期 replace，θ 与计数不手写）。"""
        pl = self.payload["page_labels"]
        self.assertEqual(pl["badge.open"]["en"], "PILOT CO RECORDS · REAL MODEL SCORES")
        # 影子对数实算（fixture=51），不是 Febrl 主轨的 1,223
        self.assertIn("51 look-alike shadow pairs", pl["home.st.village.t"]["en"])
        # 微调态误连/漏连实算（fixture：0 误连 · 1 漏连），不是主轨硬编码的 0/0
        self.assertIn("0 false · 1 missed", pl["home.st.tuned.open.t"]["en"])
        # 阶段 0 chip 走占位符（N/E 由页面按当前数据 replace）
        self.assertEqual(pl["home.step0.chip.open"]["en"], "Pilot Co records · {N} rec · {E} id")
        # 轨道卡描述不再宣称"公开基准"（租户数据不是 Febrl3 公开基准）
        self.assertNotIn("public benchmark", pl["home.tr.open.t"]["en"])
        # 状态栏规模串（buildInfo）带数据集名而非 Febrl3
        self.assertEqual(pl["stat.rec.open"]["en"], "{N} rec / {E} ent · Pilot Co records")

    def test_page_labels_play_free_tab(self):
        """试玩台自由输入页签：占位符=本数据集首条记录的真实序列化（教会格式），
        sidecar 缺席提示不再说"village rid"。"""
        pl = self.payload["page_labels"]
        ph = pl["play.free.ph"]["en"]
        self.assertTrue(ph.startswith("name: "), ph)
        self.assertIn("city: ", ph)
        self.assertNotIn("DOB", ph)
        self.assertNotIn("SSN", ph)
        self.assertNotIn("village", pl["play.free.err2"]["en"].lower())

    def test_deterministic_double_run(self):
        out2 = self.tmp / "cases_again.json"
        run("scripts/build_tenant_cases.py",
            "--records", str(self.tmp / "records.csv"),
            "--fieldmap", str(self.tmp / "fieldmap.csv"),
            "--scores", str(self.scores),
            "--dataset-label", "Pilot Co records",
            "--truth-label", "Pilot labels",
            "--out", str(out2))
        self.assertEqual(out2.read_bytes(), self.out.read_bytes(), "租户构建不确定")


class TestTenantErrorPaths(unittest.TestCase):
    """失败不静默：坏输入逐条人话报错（exit 1 + stderr 定位）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-tenant-err-"))
        _fixture_dir(cls.tmp)
        cls.scores = _fixture_scores(cls.tmp)

    def _run(self, *extra: str, scores: bool = True) -> subprocess.CompletedProcess:
        argv = ["scripts/build_tenant_cases.py",
                "--records", str(self.tmp / "records.csv"),
                "--fieldmap", str(self.tmp / "fieldmap.csv")]
        if scores:
            argv += ["--scores", str(self.scores)]
        argv += ["--out", str(self.tmp / "never.json"), *extra]
        return run(*argv, expect_fail=True)

    def test_duplicate_record_id(self):
        p = self.tmp / "dup.csv"
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["record_id", "entity_id", "name", "city"])
            w.writerows(FIX_ROWS)
            w.writerow(["r001", "E99", "alice clone", "nowhere"])
        r = run("scripts/build_tenant_cases.py", "--records", str(p),
                "--fieldmap", str(self.tmp / "fieldmap.csv"),
                "--scores", str(self.scores),
                "--out", str(self.tmp / "x.json"), expect_fail=True)
        self.assertIn("r001", r.stderr)

    def test_scores_coverage_gap(self):
        sc = json.loads(self.scores.read_text(encoding="utf-8"))
        sc["pairs"] = sc["pairs"][:-3]
        p = self.tmp / "gap.json"
        p.write_text(json.dumps(sc), encoding="utf-8")
        r = run("scripts/build_tenant_cases.py",
                "--records", str(self.tmp / "records.csv"),
                "--fieldmap", str(self.tmp / "fieldmap.csv"),
                "--scores", str(p),
                "--out", str(self.tmp / "x.json"), expect_fail=True)
        self.assertIn("覆盖不全", r.stderr)

    def test_scores_foreign_rid(self):
        sc = json.loads(self.scores.read_text(encoding="utf-8"))
        sc["pairs"][0] = ["ghost-a", "ghost-b", 0.5]
        p = self.tmp / "foreign.json"
        p.write_text(json.dumps(sc), encoding="utf-8")
        r = run("scripts/build_tenant_cases.py",
                "--records", str(self.tmp / "records.csv"),
                "--fieldmap", str(self.tmp / "fieldmap.csv"),
                "--scores", str(p),
                "--out", str(self.tmp / "x.json"), expect_fail=True)
        self.assertIn("之外的 rid", r.stderr)

    def test_name_field_not_embedding(self):
        p = self.tmp / "fm_key.csv"
        p.write_text("field,role\nname,embedding\ncity,ignore\n", encoding="utf-8")
        r = run("scripts/build_tenant_cases.py",
                "--records", str(self.tmp / "records.csv"),
                "--fieldmap", str(p),
                "--scores", str(self.scores),
                "--name-field", "city",
                "--out", str(self.tmp / "x.json"), expect_fail=True)
        self.assertIn("不是 embedding 字段", r.stderr)

    def test_both_or_neither_scores_and_emb(self):
        r = self._run(scores=False)
        self.assertIn("恰好给一个", r.stderr)


class TestEmbOnlyPath(unittest.TestCase):
    """--emb 单用（W7 控制台 cases_argv 的真实形态）：算余弦并把溯源 scores 落向量旁。

    曾是断链 bug：--emb 不带 --scores 在 sp=args.scores 处 AttributeError
    （控制台「生成效果数据」必死在子进程）；此前测试全用假子进程或全走
    --scores，没拦住——这里真跑 CLI 锁回归。"""

    def test_emb_writes_sibling_scores(self):
        with tempfile.TemporaryDirectory(prefix="ea-tenant-emb-") as d:
            tmp = _fixture_dir(Path(d))
            # 同实体=同向 one-hot（余弦 1.0）、异实体正交（0.0）；再把首个
            # ≥2 记录实体的一条记录压到 0.90——θ 扫描落 0.90、1.0 真对距
            # θ 0.10，Explain R1 的 ≥0.01 边距规则成立（纯 1/0 会卡边距）
            ents = sorted({r[1] for r in FIX_ROWS})
            idx = {e: i for i, e in enumerate(ents)}
            cnt = {e: sum(1 for r in FIX_ROWS if r[1] == e) for e in ents}
            e0 = next(e for e in ents if cnt[e] >= 2)
            star = next(r[0] for r in FIX_ROWS if r[1] == e0)
            dim = len(ents) + 1
            vec = {}
            for rid, ent, *_ in FIX_ROWS:
                v = [0.0] * dim
                v[idx[ent]] = 1.0
                if rid == star:
                    v[idx[e0]] = 0.90
                    v[-1] = (1 - 0.90 ** 2) ** 0.5
                vec[rid] = v
            vecs = tmp / "vectors.json"
            vecs.write_text(json.dumps(
                {"model": "fixture-er-model", "dim": dim, "vectors": vec}),
                encoding="utf-8")
            out = tmp / "cases.json"
            run("scripts/build_tenant_cases.py",
                "--records", str(tmp / "records.csv"),
                "--fieldmap", str(tmp / "fieldmap.csv"),
                "--emb", str(vecs),
                "--out", str(out))
            self.assertTrue(out.exists())
            sibling = tmp / "vectors.scores.json"       # 溯源自动落向量旁
            self.assertTrue(sibling.exists())
            sc = json.loads(sibling.read_text(encoding="utf-8"))
            self.assertEqual(sc["model"], "fixture-er-model")
            self.assertEqual(sc["n_records"], len(FIX_ROWS))
            self.assertEqual(len(sc["pairs"]),
                             len(FIX_ROWS) * (len(FIX_ROWS) - 1) // 2)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["model"], "fixture-er-model")
            self.assertEqual(payload["theta"], 0.9)
            self.assertEqual(payload["stats"]["false_links"], 0)


class TestFormatBTenant(unittest.TestCase):
    """格式 B（对标签）：正例连通分量为实体，真值/组件口径正确。"""

    def test_format_b_entities(self):
        with tempfile.TemporaryDirectory(prefix="ea-tenant-b-") as tmp:
            tmp = Path(tmp)
            _fixture_dir(tmp)
            # 格式 B：记录文件不带 entity_id 列（真值在对标签文件里）
            with open(tmp / "records_b.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["record_id", "name", "city"])
                w.writerows([(r[0], r[2], r[3]) for r in FIX_ROWS])
            ent = {r[0]: r[1] for r in FIX_ROWS}
            rids = [r[0] for r in FIX_ROWS]
            with open(tmp / "pairs.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["record_id_a", "record_id_b", "label"])
                for i, a in enumerate(rids):
                    for b in rids[i + 1:]:
                        w.writerow([a, b, 1 if ent[a] == ent[b] else 0])
            out = tmp / "cases_b.json"
            run("scripts/build_tenant_cases.py",
                "--records", str(tmp / "records_b.csv"),
                "--fieldmap", str(tmp / "fieldmap.csv"),
                "--scores", _fixture_scores(tmp),
                "--pairs", str(tmp / "pairs.csv"),
                "--out", str(out))
            got = json.loads(out.read_text(encoding="utf-8"))
            # 与格式 A 同一数据集：结构化结论必须一致（truth/θ/断言链）
            self.assertEqual(got["stats"]["truth_pairs"], 9)
            self.assertEqual(got["theta"], 0.965)
            self.assertEqual(got["stats"]["tp"], 8)
            self.assertEqual(got["stats"]["missed_pairs"], 1)
            # 实体号来自闭包（最小 rid），组件纯度同构
            self.assertEqual(got["stats"]["components"], 14)  # 22 记录 - 8 条断言链


@unittest.skipUnless(HAS_OPEN_CASES, "data_open/village_scores.json 未入库")
class TestTenantDemoAssembly(unittest.TestCase):
    """验收 ②：最小租户 fixture 全链 build_demo self-check（页面零改动点亮）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-tenant-demo-"))
        _fixture_dir(cls.tmp)
        cases = cls.tmp / "open_cases.json"
        run("scripts/build_tenant_cases.py",
            "--records", str(cls.tmp / "records.csv"),
            "--fieldmap", str(cls.tmp / "fieldmap.csv"),
            "--scores", _fixture_scores(cls.tmp),
            "--dataset-label", "Pilot Co records",
            "--out", str(cases))
        data = cls.tmp / "data"
        data.mkdir()
        run("scripts/make_mock_data.py", "--seed", "42", "--out", str(data))
        run("scripts/update_open_benchmark.py", "--out", str(data / "open_benchmark.json"))
        (data / "open_cases.json").write_text(cases.read_text(encoding="utf-8"),
                                              encoding="utf-8")
        cls.html_path = cls.tmp / "demo.html"
        run("scripts/build_demo.py", "--data-dir", str(data), "--out", str(cls.html_path))
        cls.html = cls.html_path.read_text(encoding="utf-8")

    def test_self_check_markers(self):
        for marker in ('"theta":0.965', '"play_fields":["name","city"]',
                       '"page_labels":{', "Pilot Co records",
                       '"badge.open":{"en":"PILOT CO RECORDS · REAL MODEL SCORES"',
                       '"home.step0.chip.open"'):
            self.assertIn(marker, self.html, marker)

    def test_zero_external_links(self):
        body = self.html.replace("http://www.w3.org/2000/svg", "")
        self.assertNotIn("http://", body)
        self.assertNotIn("https://", body)

    def test_no_leftover_placeholders(self):
        self.assertNotIn("{{", self.html)


if __name__ == "__main__":
    unittest.main()
