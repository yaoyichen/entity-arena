#!/usr/bin/env python3
"""从 Febrl3 test 实体中确定性挑选"案例村"——真实前后对比网络的记录集合。

诚实原则：
  1. 只用 test 实体（与训练包同一套划分，src/entity_arena/febrl.py），零训练泄漏；
  2. 选择标准纯结构性——同姓 + (同 suburb+postcode 或同生日) 的异实体纠缠密度，
     在看到任何模型分数**之前**定稿，不是按"哪案例赢得漂亮"挑的；
  3. 選择规则与输出逐字节确定（同 CSV + 同 seed 同结果）。

挑选逻辑：
  - 候选"影子对"：不同实体、同姓（lower）、且同 (suburb,postcode) 或同 DOB 的记录对；
  - 实体图：影子对连边；取连通分量；
  - 按分量内"多记录实体数"降序（平局按最小实体号）贪心选，直到记录预算用完；
  - 输出所选实体的全部记录 + 影子对清单。

输出：data_open/village.json（随仓库提交，作为案例村的冻结事实）。
用法：python3 scripts/pick_open_village.py [--csv data_open/febrl/febrl3.csv] [--budget 48]
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
    clean, dob_fmt, entity_of, load_csv, records_by_entity, serialize, split_entities,
)

BUDGET = 48


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=ROOT / "data_open" / "febrl" / "febrl3.csv")
    ap.add_argument("--budget", type=int, default=BUDGET)
    ap.add_argument("--out", type=Path, default=ROOT / "data_open" / "village.json")
    args = ap.parse_args()

    rows = load_csv(args.csv)
    rec_map = {r["rec_id"].strip(): r for r in rows}
    test_ents, _, _ = split_entities(rows)
    by_ent = records_by_entity(rows)

    # 只看 test 记录
    test_rids = [rid for e in sorted(test_ents) for rid in by_ent[e]]

    # 影子对：与训练硬负例同款规则——异实体 + (同姓 或 同 suburb+postcode 或 同 DOB)
    sn_t: dict[str, list[str]] = defaultdict(list)
    loc_t: dict[tuple, list[str]] = defaultdict(list)
    dob_t: dict[str, list[str]] = defaultdict(list)
    for rid in test_rids:
        r = rec_map[rid]
        k = clean(r.get("surname"))
        if k:
            sn_t[k.lower()].append(rid)
        sub, pc = clean(r.get("suburb")), clean(r.get("postcode"))
        if sub and pc:
            loc_t[(sub.lower(), pc)].append(rid)
        d = dob_fmt(clean(r.get("date_of_birth")))
        if d:
            dob_t[d].append(rid)

    def cross_entity_pairs(idx: dict) -> list[tuple[str, str]]:
        out = []
        for rids in idx.values():
            s = sorted(set(rids))
            for i, a in enumerate(s):
                for b in s[i + 1:]:
                    if entity_of(a) != entity_of(b):
                        out.append((a, b) if a < b else (b, a))
        return out

    shadow_pairs: set[tuple[str, str]] = set(cross_entity_pairs(sn_t))
    shadow_pairs.update(cross_entity_pairs(loc_t))
    shadow_pairs.update(cross_entity_pairs(dob_t))

    # 实体图连通分量
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in shadow_pairs:
        adj[entity_of(a)].add(entity_of(b))
        adj[entity_of(b)].add(entity_of(a))

    seen: set[str] = set()
    comps: list[set[str]] = []
    for e in sorted(adj):
        if e in seen:
            continue
        stack, comp = [e], set()
        while stack:
            cur = stack.pop()
            if cur in comp:
                continue
            comp.add(cur)
            stack.extend(adj[cur] - comp)
        seen |= comp
        comps.append(comp)

    # 打分：分量里多记录实体越多越好（可演示"真重复 vs 影子人"），平局按最小实体号
    def comp_key(c: set[str]) -> tuple:
        multi = sum(1 for e in c if len(by_ent[e]) > 1)
        n_recs = sum(len(by_ent[e]) for e in c)
        return (-multi, -len(c), -n_recs, min(c))

    chosen_ents: set[str] = set()
    n_recs = 0
    for comp in sorted(comps, key=comp_key):
        cr = sum(len(by_ent[e]) for e in comp)
        if n_recs + cr > args.budget and chosen_ents:
            break
        chosen_ents |= comp
        n_recs += cr

    village_rids = sorted(rid for e in chosen_ents for rid in by_ent[e])
    chosen_shadow = sorted(p for p in shadow_pairs if p[0] in village_rids and p[1] in village_rids)

    if not chosen_ents or not village_rids:
        raise SystemExit("案例村为空——检查 CSV 或预算")

    def fields(rid: str) -> dict:
        r = rec_map[rid]
        addr = " ".join(x for x in (clean(r.get("street_number")), clean(r.get("address_1")),
                                    clean(r.get("address_2"))) if x)
        loc = " ".join(x for x in (clean(r.get("suburb")), clean(r.get("postcode")),
                                   clean(r.get("state"))) if x)
        return {
            "name": " ".join(x for x in (clean(r.get("given_name")), clean(r.get("surname"))) if x),
            "dob": dob_fmt(clean(r.get("date_of_birth"))),
            "address": addr or None,
            "locality": loc or None,
            "ssn": clean(r.get("soc_sec_id")),
        }

    payload = {
        "provenance": {
            "source_csv": "data_open/febrl/febrl3.csv",
            "split": "test only（seed 42 实体级 80/20，与训练包同款，零训练泄漏）",
            "selection": "结构性挑选（影子对密度），在看到任何模型分数之前定稿",
            "shadow_pair_rule": "异实体 + (同姓 或 同 suburb+postcode 或 同 DOB)——与训练硬负例同款",
            "budget": args.budget,
        },
        "records": [
            {"rid": rid, "entity": entity_of(rid), "fields": fields(rid), "text": serialize(rec_map[rid])}
            for rid in village_rids
        ],
        "shadow_pairs": [list(p) for p in chosen_shadow],
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    multi = sum(1 for e in chosen_ents if len(by_ent[e]) > 1)
    print(f"village: {len(village_rids)} records / {len(chosen_ents)} entities "
          f"(multi-record={multi}) / {len(chosen_shadow)} shadow pairs")
    for e in sorted(chosen_ents):
        tag = "multi" if len(by_ent[e]) > 1 else "single"
        print(f"  ent {e}: {len(by_ent[e])} rec [{tag}]")


if __name__ == "__main__":
    main()
