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

解析/序列化/切分等共享逻辑在 src/entity_arena/febrl.py（与 pick_open_village.py
共用，保证案例村与训练包同一套实体划分——案例村 ⊂ test 实体，零训练泄漏）。

用法：python3 scripts/make_open_dataset.py --csv data_open/febrl/febrl3.csv --out .tmp/febrl_er
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.febrl import (  # noqa: E402
    SEED, TEST_FRAC, clean, dob_fmt, entity_of, load_csv, lookalike_key_indexes,
    records_by_entity, serialize, split_entities,
)

MAX_NEG = 5


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path(".tmp/febrl_er"))
    ap.add_argument("--test-frac", type=float, default=TEST_FRAC)
    args = ap.parse_args()

    rows = load_csv(args.csv)
    rec_ids = [r["rec_id"].strip() for r in rows]
    if len(rec_ids) != len(set(rec_ids)):
        raise SystemExit("rec_id 重复")

    ent_of = {rid: entity_of(rid) for rid in rec_ids}
    by_ent = records_by_entity(rows)
    ents = sorted(by_ent)

    # 实体级划分（同一实体的所有记录同侧，防泄漏）；rng 延续用于兜底负例（保字节级确定）
    test_ents, train_ents, rng = split_entities(rows, SEED, args.test_frac)

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
    surname_idx, loc_idx, dob_idx = lookalike_key_indexes(rows)
    n_hard = n_fallback = 0
    all_sorted = sorted(rec_ids)
    with open(args.out / "neg" / "neg.jsonl", "w", encoding="utf-8") as f:
        for e in sorted(train_ents):
            for rid in by_ent[e]:
                cand: set[str] = set()
                sn = clean(rec_map[rid].get("surname"))
                if sn:
                    cand.update(x for x in surname_idx[sn.lower()] if ent_of[x] != e)
                sub, pc = clean(rec_map[rid].get("suburb")), clean(rec_map[rid].get("postcode"))
                if sub and pc:
                    cand.update(x for x in loc_idx[(sub.lower(), pc)] if ent_of[x] != e)
                dob = dob_fmt(clean(rec_map[rid].get("date_of_birth")))
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
