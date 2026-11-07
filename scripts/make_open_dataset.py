#!/usr/bin/env python3
"""开源 ER 基准（Febrl）→ inf-retriever 训练平台上传包。

输入：Febrl 原始 CSV（recordlinkage 生成或任何镜像，列名一致即可）：
  rec_id, given_name, surname, street_number, address_1, address_2,
  suburb, postcode, state, date_of_birth, soc_sec_id
  rec_id 形如 rec-<实体号>-org / rec-<实体号>-dup-<k>，实体号即 ground truth。

输出（BEIR 风格，见 .claude/skills/inf-retriever-finetune/references/data-format.md）：
  <out>/queries.jsonl    全部记录（每条记录都是一个查询）
  <out>/corpus.jsonl     全部记录（同一批文本，作为被检索文档）
  <out>/qrels/train.tsv  训练实体（80%）的同实体对，score=1
  <out>/qrels/test.tsv   测试实体（20%）的同实体对，score=1
  <out>/neg/neg.jsonl    每个训练查询 ≤5 个硬负例（同姓/同 suburb+postcode/同生日
                         的**异实体**记录；不足时用种子随机异实体记录补齐 ≥1）

口径注意：查询与文档同池，自检索（top-1 常是自身）对两个模型是同口径的均匀代价，
前后对比公平；绝对值解读时按"有效 k-1"理解。

用法：python3 scripts/make_open_dataset.py --csv data_open/febrl/febrl3.csv --out .tmp/febrl_er
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

SEED = 42
TEST_FRAC = 0.2
MAX_NEG = 5

REC_RE = re.compile(r"^rec-(\d+)-(org|dup-\d+)$")


def entity_of(rec_id: str) -> str:
    m = REC_RE.match(rec_id)
    if not m:
        raise ValueError(f"无法解析 rec_id：{rec_id}（期望 rec-<n>-org / rec-<n>-dup-<k>）")
    return m.group(1)


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _dob_fmt(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip()
    if re.fullmatch(r"\d{8}", v):
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return v


def serialize(r: dict) -> str:
    """记录 → 嵌入文本。只写存在的字段（属性缺失是 ER 常态，不补占位符）。"""
    parts = []
    name = " ".join(x for x in (_clean(r.get("given_name")), _clean(r.get("surname"))) if x)
    if name:
        parts.append(f"name: {name}")
    dob = _dob_fmt(_clean(r.get("date_of_birth")))
    if dob:
        parts.append(f"DOB: {dob}")
    addr_bits = []
    for k in ("street_number", "address_1", "address_2"):
        v = _clean(r.get(k))
        if v:
            addr_bits.append(v)
    if addr_bits:
        parts.append("address: " + " ".join(addr_bits))
    locality = []
    for k in ("suburb", "postcode", "state"):
        v = _clean(r.get(k))
        if v:
            locality.append(v)
    if locality:
        parts.append("locality: " + " ".join(locality))
    ssn = _clean(r.get("soc_sec_id"))
    if ssn:
        parts.append(f"SSN: {ssn}")
    return " | ".join(parts)


def load_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # Febrl CSV 表头逗号后带空格（" given_name"），键名统一去空格
        rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    if not rows:
        raise SystemExit(f"空 CSV：{path}")
    need = {"rec_id", "given_name", "surname"}
    missing = need - set(rows[0])
    if missing:
        raise SystemExit(f"缺列 {sorted(missing)}；实际列：{sorted(rows[0])}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path(".tmp/febrl_er"))
    ap.add_argument("--test-frac", type=float, default=TEST_FRAC)
    args = ap.parse_args()

    rng = random.Random(SEED)
    rows = load_csv(args.csv)
    rec_ids = [r["rec_id"].strip() for r in rows]
    if len(rec_ids) != len(set(rec_ids)):
        raise SystemExit("rec_id 重复")

    ent_of = {rid: entity_of(rid) for rid in rec_ids}
    ents = sorted({ent_of[rid] for rid in rec_ids})
    by_ent: dict[str, list[str]] = defaultdict(list)
    for rid in rec_ids:
        by_ent[ent_of[rid]].append(rid)
    for e in by_ent:
        by_ent[e].sort()

    # 实体级划分（同一实体的所有记录同侧，防泄漏）
    rng.shuffle(ents)
    n_test = max(1, round(len(ents) * args.test_frac))
    test_ents = set(ents[:n_test])
    train_ents = set(ents[n_test:])

    # queries / corpus：全部记录
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "qrels").mkdir(exist_ok=True)
    (args.out / "neg").mkdir(exist_ok=True)
    rec_map = {r["rec_id"].strip(): r for r in rows}
    with open(args.out / "queries.jsonl", "w", encoding="utf-8") as fq, \
         open(args.out / "corpus.jsonl", "w", encoding="utf-8") as fc:
        for rid in sorted(rec_ids):
            obj = {"_id": rid, "text": serialize(rec_map[rid])}
            fq.write(json.dumps(obj, ensure_ascii=False) + "\n")
            fc.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # qrels：每个查询 → 同实体其余记录（score 1）
    n_pairs = {"train": 0, "test": 0}
    for split, ent_set in (("train", train_ents), ("test", test_ents)):
        with open(args.out / "qrels" / f"{split}.tsv", "w", encoding="utf-8") as f:
            for e in sorted(ent_set):
                for rid in by_ent[e]:
                    for other in by_ent[e]:
                        if other != rid:
                            f.write(f"{rid}\t{other}\t1\n")
                            n_pairs[split] += 1

    # 硬负例：同姓 / 同 suburb+postcode / 同生日的异实体记录（对训练查询）
    surname_idx: dict[str, list[str]] = defaultdict(list)
    loc_idx: dict[tuple, list[str]] = defaultdict(list)
    dob_idx: dict[str, list[str]] = defaultdict(list)
    for rid in sorted(rec_ids):
        r = rec_map[rid]
        sn = _clean(r.get("surname"))
        if sn:
            surname_idx[sn.lower()].append(rid)
        sub, pc = _clean(r.get("suburb")), _clean(r.get("postcode"))
        if sub and pc:
            loc_idx[(sub.lower(), pc)].append(rid)
        dob = _dob_fmt(_clean(r.get("date_of_birth")))
        if dob:
            dob_idx[dob].append(rid)

    n_hard = n_fallback = 0
    all_sorted = sorted(rec_ids)
    with open(args.out / "neg" / "neg.jsonl", "w", encoding="utf-8") as f:
        for e in sorted(train_ents):
            for rid in by_ent[e]:
                cand: set[str] = set()
                sn = _clean(rec_map[rid].get("surname"))
                if sn:
                    cand.update(x for x in surname_idx[sn.lower()] if ent_of[x] != e)
                sub, pc = _clean(rec_map[rid].get("suburb")), _clean(rec_map[rid].get("postcode"))
                if sub and pc:
                    cand.update(x for x in loc_idx[(sub.lower(), pc)] if ent_of[x] != e)
                dob = _dob_fmt(_clean(rec_map[rid].get("date_of_birth")))
                if dob:
                    cand.update(x for x in dob_idx[dob] if ent_of[x] != e)
                negs = sorted(cand)[:MAX_NEG]
                n_hard += len(negs)
                if not negs:
                    # 兜底：种子随机异实体记录（保证 use_hard_negatives=true 可用）
                    while len(negs) < 1:
                        pick = rng.choice(all_sorted)
                        if ent_of[pick] != e:
                            negs = [pick]
                    n_fallback += 1
                f.write(json.dumps({"qid": rid, "neg": negs}, ensure_ascii=False) + "\n")

    # 统计 + 自检（失败不静默）
    n_train_q = sum(len(by_ent[e]) for e in train_ents)
    n_test_q = sum(len(by_ent[e]) for e in test_ents)
    dup_ents = sum(1 for e in ents if len(by_ent[e]) > 1)
    assert n_pairs["train"] > 0 and n_pairs["test"] > 0, "train/test qrels 为空"
    print(f"records={len(rec_ids)} entities={len(ents)} "
          f"(multi-record={dup_ents})")
    print(f"train: {len(train_ents)} ents / {n_train_q} queries / {n_pairs['train']} pairs")
    print(f"test : {len(test_ents)} ents / {n_test_q} queries / {n_pairs['test']} pairs")
    print(f"hard negatives: {n_hard} mined, {n_fallback} queries fell back to random")
    print(f"package written to {args.out}/")


if __name__ == "__main__":
    main()
