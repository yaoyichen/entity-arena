"""客户数据入口（W1）：intake 校验 / 序列化 / 切分 / 训练包契约。

关键回归锚（TestFebrlAnchor）：Febrl3 经 make_client_dataset.py（泛化路径）与
make_open_dataset.py（Febrl 专用路径）产出 的 qrels 必须逐字节一致——
实体切分算法两处同款是数据合同"零泄漏切分"的单一事实来源。
硬负例规则是泛化版（允许与 Febrl 复合键规则不同），但必须满足不变量：
异实体 + 共享 ≥1 个 neg_key 字段的规范化非空值（测试显式断言）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.intake import (  # noqa: E402
    IntakeError, apply_normalize, entity_split, load_fieldmap, load_records,
    run_intake, serialize_record,
)

FEBRL_CSV = ROOT / "data_open" / "febrl" / "febrl3.csv"
ENT_RE = re.compile(r"^rec-(\d+)-")
PKG_FILES = ("queries.jsonl", "corpus.jsonl", "qrels/train.tsv",
             "qrels/test.tsv", "neg/neg.jsonl")

RECORDS_A = """record_id,entity_id,name,city,dob,ssn
r1,e1,alice wang,shanghai,19800101,111
r2,e1,ALICE Wang,,,111
r3,e2,bob li,beijing,19750505,222
r4,e2,bob lee,beijing,,222
r5,e3,carla,chengdu,19901231,333
r6,e4,dave xu,shanghai,19800101,444
r7,e5,erin zhou,hangzhou,19850303,555
r8,e5,ERIN Zhou,,19850303,
r9,e6,frank wu,nanjing,19700101,666
r10,e6,frank woo,nanjing,,666
r11,e7,grace sun,suzhou,19880707,777
r12,e7,grace soon,,19880707,777
r13,e8,henry qin,tianjin,19650909,888
r14,e8,henry chin,tianjin,,888
"""
FIELDMAP_A = """field,role,normalize
name,embedding,lower
city,embedding,
dob,embedding,date8
ssn,unique-key,
"""
PAIRS_B = """record_id_a,record_id_b,label
r1,r2,1
r3,r4,1
r2,r5,0
r4,r5,1
"""


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def msgs(err: IntakeError) -> str:
    return "\n".join(err.errors)


class TestFieldmap(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="intake-fm-"))

    def test_bad_role(self):
        p = write(self.tmp / "fm.csv", "field,role\nname,embed\n")
        with self.assertRaises(IntakeError) as cm:
            load_fieldmap(p)
        self.assertIn("role", msgs(cm.exception))

    def test_bad_normalize(self):
        p = write(self.tmp / "fm.csv", "field,role,normalize\nname,embedding,ROT13\n")
        with self.assertRaises(IntakeError) as cm:
            load_fieldmap(p)
        self.assertIn("normalize", msgs(cm.exception))

    def test_dup_and_missing_embedding(self):
        p = write(self.tmp / "fm.csv", "field,role\nname,ignore\nname,ignore\n")
        with self.assertRaises(IntakeError) as cm:
            load_fieldmap(p)
        self.assertIn("重复声明", msgs(cm.exception))
        p2 = write(self.tmp / "fm2.csv", "field,role\nname,ignore\n")
        with self.assertRaises(IntakeError) as cm:
            load_fieldmap(p2)
        self.assertIn("没有任何 embedding 字段", msgs(cm.exception))

    def test_header_and_json_variant(self):
        p = write(self.tmp / "fm.csv", "field,name\n")
        with self.assertRaises(IntakeError) as cm:
            load_fieldmap(p)
        self.assertIn("表头缺 role 列", msgs(cm.exception))
        pj = write(self.tmp / "fm.json", json.dumps(
            [{"field": "name", "role": "embedding", "normalize": "lower"},
             {"field": "ssn", "role": "unique_key", "neg_key": False}]))
        fm = load_fieldmap(pj)
        self.assertEqual([f.name for f in fm], ["name", "ssn"])
        self.assertEqual(fm[1].role, "unique-key")  # 下划线别名归一


class TestNormalizeSerialize(unittest.TestCase):
    def test_apply_normalize(self):
        self.assertEqual(apply_normalize(" 19790212 ", "date8"), "1979-02-12")
        self.assertEqual(apply_normalize("1979-2-12", "date8"), "1979-2-12")  # 非 8 位原样
        self.assertEqual(apply_normalize(" MacDo ", "lower"), "macdo")
        self.assertEqual(apply_normalize("", "lower"), "")

    def test_serialize_skips_empty_and_non_embedding(self):
        fm = load_fieldmap(write(Path(tempfile.mkdtemp()) / "fm.csv", FIELDMAP_A))
        rec = {"name": " Alice Wang ", "city": "", "dob": "19800101", "ssn": "111"}
        self.assertEqual(serialize_record(rec, fm),
                         "name: alice wang | dob: 1980-01-01")
        self.assertEqual(serialize_record({"name": "", "city": "", "dob": "", "ssn": "1"}, fm), "")


class TestValidationFormatA(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="intake-va-"))
        self.fm = load_fieldmap(write(self.tmp / "fm.csv", FIELDMAP_A))

    def _run(self, records_text: str, fieldmap_text: str = FIELDMAP_A):
        rp = write(self.tmp / "rec.csv", records_text)
        fp = write(self.tmp / "fm.csv", fieldmap_text)
        return run_intake(rp, fp, out=self.tmp / "pkg", test_frac=0.5)

    def test_missing_entity_id_column(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id,name\nr1,alice\n")
        self.assertIn("entity_id", msgs(cm.exception))
        self.assertIn("--pairs", msgs(cm.exception))  # 给出格式 B 出路

    def test_empty_entity_id_value(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id,entity_id,name,city,dob\nr2,,alice,,,")
        self.assertIn("第 2 行", msgs(cm.exception))

    def test_duplicate_record_id(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id,entity_id,name,city,dob\nr1,e1,a,b,19800101\nr1,e1,a,b,19800101\n")
        self.assertIn("重复", msgs(cm.exception))
        self.assertIn("第 2 行", msgs(cm.exception))
        self.assertIn("第 3 行", msgs(cm.exception))

    def test_single_entity_guard(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id,entity_id,name,city,dob\nr1,e1,a,b,19800101\nr2,e1,a,b,19800101\n")
        self.assertIn("单实体", msgs(cm.exception))

    def test_all_embedding_empty_record(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id,entity_id,name,city,dob\n"
                      "r1,e1,a,b,19800101\nr2,e2,,,,\n")  # r2 全空 → 文本为空
        self.assertIn("embedding 字段均为空", msgs(cm.exception))
        self.assertIn("r2", msgs(cm.exception))

    def test_fieldmap_unknown_field(self):
        with self.assertRaises(IntakeError) as cm:
            self._run(RECORDS_A, "field,role\nnmae,embedding\ncity,embedding\ndob,embedding\n")
        self.assertIn("不存在的字段", msgs(cm.exception))

    def test_record_id_as_embedding_rejected(self):
        with self.assertRaises(IntakeError) as cm:
            self._run(RECORDS_A, "field,role\nrecord_id,embedding\nname,embedding\n")
        self.assertIn("基础设施列", msgs(cm.exception))

    def test_empty_records_file(self):
        with self.assertRaises(IntakeError):
            self._run("record_id,entity_id,name,city,dob\n")

    def test_undeclared_columns_warned_not_raised(self):
        r = self._run(RECORDS_A)  # ssn 声明为 unique-key → 不算未声明；city 已声明
        self.assertIsInstance(r["warnings"], list)


class TestValidationFormatB(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="intake-vb-"))
        write(self.tmp / "rec.csv", RECORDS_A)
        write(self.tmp / "fm.csv", FIELDMAP_A)

    def _run(self, pairs_text: str):
        pp = write(self.tmp / "pairs.csv", pairs_text)
        return run_intake(self.tmp / "rec.csv", self.tmp / "fm.csv", pp,
                          out=self.tmp / "pkg")

    def test_dangling_reference(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id_a,record_id_b,label\nr1,rX,1\n")
        self.assertIn("rX", msgs(cm.exception))
        self.assertIn("第 2 行", msgs(cm.exception))

    def test_bad_label_and_self_pair(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id_a,record_id_b,label\nr1,r2,YES\n")
        self.assertIn("label", msgs(cm.exception))
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id_a,record_id_b,label\nr1,r1,1\n")
        self.assertIn("自身配对", msgs(cm.exception))

    def test_conflicting_duplicate_labels(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id_a,record_id_b,label\nr1,r2,1\nr2,r1,0\n")
        self.assertIn("冲突", msgs(cm.exception))

    def test_identical_duplicates_deduped(self):
        r = self._run("record_id_a,record_id_b,label\nr1,r2,1\nr2,r1,1\nr3,r4,1\nr4,r5,1\n")
        self.assertEqual(r["positive_pairs"], 3)  # (r1,r2) 去重后计一次

    def test_no_positives(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("record_id_a,record_id_b,label\nr1,r2,0\n")
        self.assertIn("正例对", msgs(cm.exception))

    def test_bad_header(self):
        with self.assertRaises(IntakeError) as cm:
            self._run("a,b,l\nr1,r2,1\n")
        self.assertIn("record_id_a", msgs(cm.exception))


class TestFormatAGold(unittest.TestCase):
    """小金标：序列化、qrels 全集、硬负例规则、兜底确定性、双跑逐字节一致。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="intake-gold-"))
        write(cls.tmp / "rec.csv", RECORDS_A)
        write(cls.tmp / "fm.csv", FIELDMAP_A)
        cls.report = run_intake(cls.tmp / "rec.csv", cls.tmp / "fm.csv",
                                out=cls.tmp / "pkg", test_frac=0.5)
        cls.ent_of = {f"r{i}": e for i, e in enumerate(
            ["e1", "e1", "e2", "e2", "e3", "e4", "e5", "e5",
             "e6", "e6", "e7", "e7", "e8", "e8"], start=1)}

    def _lines(self, rel: str):
        return (self.tmp / "pkg" / rel).read_text(encoding="utf-8").splitlines()

    def test_texts(self):
        texts = {json.loads(l)["_id"]: json.loads(l)["text"]
                 for l in self._lines("queries.jsonl")}
        self.assertEqual(texts["r1"], "name: alice wang | city: shanghai | dob: 1980-01-01")
        self.assertEqual(texts["r2"], "name: alice wang")  # 空 city/dob 跳过
        self.assertEqual(texts["r5"], "name: carla | city: chengdu | dob: 1990-12-31")
        self.assertNotIn("ssn", texts["r1"])  # unique-key 不进嵌入文本

    def test_qrels_full_coverage_and_partition(self):
        train = {tuple(l.split("\t")[:2]) for l in self._lines("qrels/train.tsv")}
        test = {tuple(l.split("\t")[:2]) for l in self._lines("qrels/test.tsv")}
        expected = {(a, b) for a, b in
                    [("r1", "r2"), ("r3", "r4"), ("r7", "r8"), ("r9", "r10"),
                     ("r11", "r12"), ("r13", "r14")]} |                    {(b, a) for a, b in
                    [("r1", "r2"), ("r3", "r4"), ("r7", "r8"), ("r9", "r10"),
                     ("r11", "r12"), ("r13", "r14")]}
        self.assertEqual(train | test, expected)  # e3/e4 单记录实体无 qrel（口径如此）
        self.assertFalse(train & test)
        # 实体不横跨：同一实体的对必在同侧
        for a, b in train | test:
            self.assertEqual(self.ent_of[a], self.ent_of[b])

    def test_neg_rule_and_fallback(self):
        test_ents, train_ents, _ = entity_split(
            ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8"], 42, 0.5)
        negs = {json.loads(l)["qid"]: json.loads(l)["neg"] for l in self._lines("neg/neg.jsonl")}
        for qid, ns in negs.items():
            self.assertTrue(ns, "每条至少 1 个负例")
            for n in ns:
                self.assertNotEqual(self.ent_of[n], self.ent_of[qid], "负例必须异实体")
        if "e1" in train_ents:
            self.assertEqual(negs.get("r1"), ["r6"], "r1 与 r6 同城（shanghai）异实体")
        else:
            self.assertNotIn("r1", negs)
        if "e4" in train_ents:
            self.assertEqual(negs.get("r6"), ["r1"])
        else:
            self.assertNotIn("r6", negs)
        members = {"e1": ["r1", "r2"], "e2": ["r3", "r4"], "e3": ["r5"],
                   "e4": ["r6"], "e5": ["r7", "r8"], "e6": ["r9", "r10"],
                   "e7": ["r11", "r12"], "e8": ["r13", "r14"]}
        self.assertEqual(set(negs), {r for e in train_ents for r in members[e]},
                         "负例覆盖且仅覆盖 train 侧记录")

    def test_deterministic(self):
        run_intake(self.tmp / "rec.csv", self.tmp / "fm.csv", out=self.tmp / "pkg2", test_frac=0.5)
        for f in PKG_FILES:
            self.assertEqual((self.tmp / "pkg" / f).read_bytes(),
                             (self.tmp / "pkg2" / f).read_bytes(), f"{f} 不确定")
        self.assertEqual((self.tmp / "pkg" / "intake_report.json").read_bytes(),
                         (self.tmp / "pkg2" / "intake_report.json").read_bytes(),
                         "report 含墙上时钟之类的非确定内容")

    def test_report_contents(self):
        r = self.report
        self.assertEqual(r["label_format"], "A")
        self.assertEqual(r["records"], 14)
        self.assertEqual(r["embedding_fields"], ["name", "city", "dob"])
        self.assertEqual(r["unique_key_fields"], ["ssn"])
        self.assertIn("field: value", r["serialization"])  # 口径全文进 report（W2 复用）
        self.assertEqual(r["split"]["seed"], 42)

    def test_zip_deterministic_and_excludes_report(self):
        z1 = run_intake(self.tmp / "rec.csv", self.tmp / "fm.csv",
                        out=self.tmp / "pkgz1", test_frac=0.5, make_zip=True)["zip"]
        z2 = run_intake(self.tmp / "rec.csv", self.tmp / "fm.csv",
                        out=self.tmp / "pkgz2", test_frac=0.5, make_zip=True)["zip"]
        self.assertEqual(Path(z1).read_bytes(), Path(z2).read_bytes(), "zip 必须确定性")
        with zipfile.ZipFile(z1) as z:
            names = sorted(z.namelist())
        self.assertEqual(names, ["corpus.jsonl", "neg/neg.jsonl", "qrels/test.tsv",
                                 "qrels/train.tsv", "queries.jsonl"])
        self.assertNotIn("intake_report.json", "\n".join(names))


class TestFormatBGold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="intake-goldb-"))
        write(cls.tmp / "rec.csv", RECORDS_A)
        write(cls.tmp / "fm.csv", FIELDMAP_A)
        write(cls.tmp / "pairs.csv", PAIRS_B)
        cls.report = run_intake(cls.tmp / "rec.csv", cls.tmp / "fm.csv",
                                cls.tmp / "pairs.csv", out=cls.tmp / "pkg")

    def _lines(self, rel: str):
        return (self.tmp / "pkg" / rel).read_text(encoding="utf-8").splitlines()

    def test_qrels_from_positives_both_directions(self):
        train = {tuple(l.split("\t")[:2]) for l in self._lines("qrels/train.tsv")}
        test = {tuple(l.split("\t")[:2]) for l in self._lines("qrels/test.tsv")}
        allpairs = train | test
        expected = {("r1", "r2"), ("r2", "r1"), ("r3", "r4"), ("r4", "r3"),
                    ("r4", "r5"), ("r5", "r4")}
        self.assertEqual(allpairs, expected)  # label=1 三对 × 双向；r2-r5 的 0 不进 qrels
        self.assertFalse(train & test)
        self.assertTrue(train and test)

    def test_gold_negatives_used(self):
        negs = {json.loads(l)["qid"]: json.loads(l)["neg"] for l in self._lines("neg/neg.jsonl")}
        train_q = {l.split("\t")[0] for l in self._lines("qrels/train.tsv")}
        self.assertEqual(set(negs), train_q)
        pos_partners = {"r1": {"r2"}, "r2": {"r1"}, "r3": {"r4"}, "r4": {"r3", "r5"},
                        "r5": {"r4"}}
        for qid, ns in negs.items():
            self.assertTrue(ns)
            for n in ns:
                self.assertNotIn(n, pos_partners[qid], "负例不得是正例伙伴")
        if "r2" in negs:
            self.assertEqual(negs["r2"], ["r5"], "label=0 金标负例直接生效")
        if "r5" in negs:
            self.assertEqual(negs["r5"], ["r2"])

    def test_report_and_deterministic(self):
        self.assertEqual(self.report["label_format"], "B")
        self.assertEqual(self.report["positive_pairs"], 3)
        self.assertEqual(self.report["zero_pairs"], 1)
        run_intake(self.tmp / "rec.csv", self.tmp / "fm.csv", self.tmp / "pairs.csv",
                   out=self.tmp / "pkg2")
        for f in PKG_FILES:
            self.assertEqual((self.tmp / "pkg" / f).read_bytes(),
                             (self.tmp / "pkg2" / f).read_bytes(), f"{f} 不确定")


class TestCrossFormat(unittest.TestCase):
    """CSV 与 JSONL 记录文件同数据 → 同包（跨格式一致性）。"""

    def test_jsonl_equals_csv(self):
        tmp = Path(tempfile.mkdtemp(prefix="intake-x-"))
        write(tmp / "rec.csv", RECORDS_A)
        lines = []
        for line in RECORDS_A.splitlines()[1:]:
            rid, eid, name, city, dob, ssn = line.split(",")
            lines.append(json.dumps({"record_id": rid, "entity_id": eid, "name": name,
                                     "city": city, "dob": dob, "ssn": ssn}))
        write(tmp / "rec.jsonl", "\n".join(lines) + "\n")
        write(tmp / "fm.csv", FIELDMAP_A)
        run_intake(tmp / "rec.csv", tmp / "fm.csv", out=tmp / "pkg_csv", test_frac=0.5)
        run_intake(tmp / "rec.jsonl", tmp / "fm.csv", out=tmp / "pkg_jsonl", test_frac=0.5)
        for f in PKG_FILES:
            self.assertEqual((tmp / "pkg_csv" / f).read_bytes(),
                             (tmp / "pkg_jsonl" / f).read_bytes(), f"{f} 跨格式不一致")


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="intake-cli-"))

    def _run(self, *extra: str, records: str = RECORDS_A):
        write(self.tmp / "rec.csv", records)
        write(self.tmp / "fm.csv", FIELDMAP_A)
        return subprocess.run(
            [sys.executable, "scripts/make_client_dataset.py",
             "--records", str(self.tmp / "rec.csv"),
             "--fieldmap", str(self.tmp / "fm.csv"),
             "--out", str(self.tmp / "pkg"), "--test-frac", "0.5", *extra],
            cwd=ROOT, capture_output=True, text=True)

    def test_ok_and_zip(self):
        r = self._run("--zip")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("package written", r.stdout)
        self.assertIn("下一步", r.stdout)  # 引导 upload_dataset
        zp = self.tmp / "pkg.zip"
        self.assertTrue(zp.exists())
        with zipfile.ZipFile(zp) as z:
            self.assertTrue(any(n.endswith("queries.jsonl") for n in z.namelist()))

    def test_validation_failure_exit_and_message(self):
        bad = RECORDS_A.replace("r2,e1,ALICE Wang,,,111\n", "r1,e1,dup,,,\n")
        r = self._run(records=bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("校验失败", r.stderr)
        self.assertIn("第", r.stderr)  # 行号定位

    def test_format_b_via_cli(self):
        write(self.tmp / "pairs.csv", PAIRS_B)
        r = self._run("--pairs", str(self.tmp / "pairs.csv"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("B（逐对标签）", r.stdout)


class TestFebrlAnchor(unittest.TestCase):
    """泛化路径 vs Febrl 专用路径：qrels 逐字节一致（切分算法单一事实来源）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="intake-anchor-"))
        # ① Febrl 专用路径
        cls.pkg_a = cls.tmp / "febrl_legacy"
        subprocess.run([sys.executable, "scripts/make_open_dataset.py",
                        "--csv", str(FEBRL_CSV), "--out", str(cls.pkg_a)],
                       check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        # ② 泛化路径：同一份 CSV 加 entity_id 列（rec_id 里的实体号即真值）
        src = FEBRL_CSV.read_text(encoding="utf-8-sig").splitlines()
        header = [h.strip() for h in src[0].split(",")]
        out_lines = [",".join(["record_id", "entity_id"] + header[1:])]
        for line in src[1:]:
            cells = line.split(",")
            out_lines.append(",".join([cells[0], ENT_RE.match(cells[0]).group(1)] + cells[1:]))
        rec_path = cls.tmp / "febrl_entities.csv"
        rec_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        fm_path = cls.tmp / "fieldmap.csv"
        fm_path.write_text("field,role,normalize\n"
                           "given_name,embedding\nsurname,embedding\n"
                           "street_number,embedding\naddress_1,embedding\n"
                           "address_2,embedding\nsuburb,embedding\n"
                           "postcode,embedding\nstate,embedding\n"
                           "date_of_birth,embedding,date8\n"
                           "soc_sec_id,embedding\n", encoding="utf-8")
        cls.pkg_b = cls.tmp / "febrl_client"
        cls.report = run_intake(rec_path, fm_path, out=cls.pkg_b)

    def test_qrels_byte_identical(self):
        for f in ("qrels/train.tsv", "qrels/test.tsv"):
            self.assertEqual((self.pkg_a / f).read_bytes(), (self.pkg_b / f).read_bytes(),
                             f"{f}：泛化路径切分与 Febrl 管线不一致（防泄漏口径被破坏）")

    def test_ids_identical_texts_nonempty(self):
        for name in ("queries.jsonl", "corpus.jsonl"):
            a = [json.loads(l) for l in (self.pkg_a / name).read_text().splitlines()]
            b = [json.loads(l) for l in (self.pkg_b / name).read_text().splitlines()]
            self.assertEqual([o["_id"] for o in a], [o["_id"] for o in b])
            self.assertTrue(all(o["text"].strip() for o in b))

    def test_neg_invariants(self):
        """泛化硬负例规则：异实体 + 共享 ≥1 个 embedding 字段规范化非空值。

        与 Febrl 复合键规则（同姓/同 suburb+postcode/同生日）允许有差异——
        这是泛化的代价与选择；不变量由本测试锁定。
        """
        fieldmap = load_fieldmap(self.tmp / "fieldmap.csv")
        cols, records = load_records(self.tmp / "febrl_entities.csv")
        rec_map = {r["record_id"]: r for _, r in records}

        def value(rid: str, field: str) -> str:
            return apply_normalize(str(rec_map[rid].get(field, "")).strip(),
                                   fieldmap[[f.name for f in fieldmap].index(field)].normalize)
        for line in (self.pkg_b / "neg" / "neg.jsonl").read_text().splitlines():
            o = json.loads(line)
            qid = o["qid"]
            self.assertTrue(o["neg"])
            for n in o["neg"]:
                self.assertNotEqual(ENT_RE.match(n).group(1), ENT_RE.match(qid).group(1),
                                    "负例必须异实体")
                shared = any(value(qid, f.name) and value(qid, f.name) == value(n, f.name)
                             for f in fieldmap if f.mines)
                self.assertTrue(shared, f"{qid} 与 {n} 无共享字段值（不变量破坏）")

    def test_client_path_deterministic(self):
        pkg2 = self.tmp / "febrl_client2"
        cols, records = load_records(self.tmp / "febrl_entities.csv")
        run_intake(self.tmp / "febrl_entities.csv", self.tmp / "fieldmap.csv", out=pkg2)
        for f in PKG_FILES:
            self.assertEqual((self.pkg_b / f).read_bytes(), (pkg2 / f).read_bytes(),
                             f"{f} 不确定")

    def test_report_split_matches_legacy(self):
        # train/test 实体数是全实体口径（含单记录实体），legacy qrels 只含多记录实体——
        # 切分本身的一致性已由 test_qrels_byte_identical 逐字节锁定
        sp = self.report["split"]
        self.assertEqual(sp["train_entities"] + sp["test_entities"], 2000)
        self.assertEqual(sp["test_entities"], 400)  # max(1, round(2000*0.2))
        self.assertEqual(self.report["records"], 5000)
        self.assertEqual(self.report["label_format"], "A")


if __name__ == "__main__":
    unittest.main()
