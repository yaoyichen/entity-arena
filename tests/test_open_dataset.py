"""开源基准轨道：Febrl → 训练平台上传包的确定性与数据契约。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data_open" / "febrl" / "febrl3.csv"
ENT_RE = re.compile(r"^rec-(\d+)-")


def build(out: Path) -> None:
    subprocess.run([sys.executable, "scripts/make_open_dataset.py",
                    "--csv", str(CSV), "--out", str(out)],
                   check=True, cwd=ROOT, stdout=subprocess.DEVNULL)


def ent_of(rid: str) -> str:
    return ENT_RE.match(rid).group(1)


class TestOpenDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="febrl-er-test-")
        cls.pkg = Path(cls.tmp) / "pkg"
        build(cls.pkg)

    def _qids(self, split: str) -> set[str]:
        txt = (self.pkg / "qrels" / f"{split}.tsv").read_text()
        return {line.split("\t")[0] for line in txt.splitlines()}

    def test_source_csv_present(self):
        self.assertTrue(CSV.exists(), "开源基准源文件应提交进仓库")
        n = sum(1 for _ in CSV.open())
        self.assertEqual(n, 5001, "febrl3 = 表头 + 5000 条")  # 5000 条

    def test_deterministic(self):
        other = Path(self.tmp) / "pkg2"
        build(other)
        for f in ("queries.jsonl", "corpus.jsonl", "qrels/train.tsv",
                  "qrels/test.tsv", "neg/neg.jsonl"):
            self.assertEqual((self.pkg / f).read_bytes(), (other / f).read_bytes(),
                             f"{f} 不是确定性的")

    def test_contract_ids_and_texts(self):
        for name in ("queries.jsonl", "corpus.jsonl"):
            seen = set()
            for line in (self.pkg / name).read_text(encoding="utf-8").splitlines():
                o = json.loads(line)
                self.assertTrue(o["_id"] and o["text"].strip(), "id/text 非空")
                self.assertNotIn(o["_id"], seen, "同文件内 _id 重复")
                seen.add(o["_id"])
            self.assertEqual(len(seen), 5000)

    def test_qrels_no_self_no_cross_leak(self):
        for split in ("train", "test"):
            for line in (self.pkg / "qrels" / f"{split}.tsv").read_text().splitlines():
                q, d, s = line.split("\t")
                self.assertEqual(len(line.split("\t")), 3)
                self.assertNotEqual(q, d, "不得自配对")
                self.assertEqual(ent_of(q), ent_of(d), "qrels 只连同实体")
        self.assertFalse({ent_of(q) for q in self._qids("train")} &
                         {ent_of(q) for q in self._qids("test")},
                         "实体不得横跨 train/test")

    def test_neg_rules(self):
        train_q = self._qids("train")
        covered = set()
        for line in (self.pkg / "neg" / "neg.jsonl").read_text().splitlines():
            o = json.loads(line)
            covered.add(o["qid"])
            self.assertTrue(o["neg"], "每个负例条目至少 1 个负例")
            for n in o["neg"]:
                self.assertNotEqual(ent_of(n), ent_of(o["qid"]), "负例必须异实体")
        self.assertTrue(train_q <= covered, "每个 train qrels 查询都有负例")

    def test_singleton_entities_explained(self):
        """febrl3 有单记录实体：它们在 queries/corpus 里但不在 qrels —— 口径如此。"""
        all_ids = {json.loads(l)["_id"] for l in
                   (self.pkg / "queries.jsonl").read_text().splitlines()}
        qrel_ids = self._qids("train") | self._qids("test")
        singletons = all_ids - qrel_ids
        self.assertGreater(len(singletons), 0, "febrl3 应存在单记录实体")
        self.assertEqual(len(all_ids), 5000)


if __name__ == "__main__":
    unittest.main()
