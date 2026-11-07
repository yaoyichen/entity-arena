#!/usr/bin/env python3
"""生成演示数据：world → 双模型检索结果 → 指标 → 网络布局 → 剧本查询/案例。

用法：python scripts/make_mock_data.py [--seed 42] [--out data]
产物：data/{records.jsonl, results.json, metrics.json, network.json, queries.json, pairs.json, meta.json}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from entity_arena import SEED  # noqa: E402
from entity_arena.layout import build_network  # noqa: E402
from entity_arena.simulate import (  # noqa: E402
    THRESHOLD,
    compute_metrics,
    field_sims,
    field_state,
    retrieve_all,
)
from entity_arena.synth import SCENARIO_META, SOURCE_META, build_world  # noqa: E402


def scripted_queries() -> list[dict]:
    """Arena 视图的 6 个剧本查询（story 双语）。"""
    return [
        {
            "query_id": "Q1", "record_id": "R103", "scenario": "missing_partial",
            "title": {"en": "A bare hotel slip → the full identity", "zh": "一张光秃秃的入住单 → 完整身份"},
            "story": {
                "en": "No ID, no DOB — only a typo'd name and a phone number. Both models find the bank record; only the tuned one drags the household registry copy back into candidates, closing the merge chain.",
                "zh": "无证件号、无生日，只有一个拼错的姓名和一个手机号。两家都能靠电话锁定银行记录；只有微调模型把户籍那条原始记录捞回候选——归并链由此闭合。",
            },
        },
        {
            "query_id": "Q2", "record_id": "R402", "scenario": "transliteration",
            "title": {"en": "Cantonese romanization vs Hanzi", "zh": "粤语罗马化 vs 汉字"},
            "story": {
                "en": "\"Wong Sau Ying\" (HK-style) recognizes its latin cousins, but the Hanzi registry copy 「王秀英」 sits at 0.34 for baseline — below threshold, unasserted. Tuned puts it at #1 with 0.88.",
                "zh": "「Wong Sau Ying」能认出拉丁表亲，但汉字户籍记录「王秀英」在 baseline 眼里只有 0.34——不过线、不敢断言。微调后它直接被顶到第一位（0.88）。",
            },
        },
        {
            "query_id": "Q3", "record_id": "R602", "scenario": "conflicting",
            "title": {"en": "Impossible DOB (Feb 30)", "zh": "不存在的生日（2 月 30 日）"},
            "story": {
                "en": "DOB conflicts, name crosses scripts — baseline lands at 0.50, five hundredths short of daring to link. Tuned learns conflict tolerance: an exact 18-digit ID outweighs one bad field (0.77).",
                "zh": "生日冲突、名字跨文字——baseline 算出 0.50，差 0.05 没敢连。微调学会了冲突容忍：18 位证件号全同，压过一个坏字段（0.77）。",
            },
        },
        {
            "query_id": "Q4", "record_id": "R201", "scenario": "similar_entity",
            "title": {"en": "Two 张伟, one is a stranger", "zh": "两个张伟，一个是陌生人"},
            "story": {
                "en": "The most common name in the corpus. The wrong link fabricates a person that doesn't exist.",
                "zh": "语料里最常见的人名。错误的关联会凭空捏造出一个不存在的人。",
            },
        },
        {
            "query_id": "Q5", "record_id": "R301", "scenario": "similar_entity",
            "title": {"en": "Twin brothers, near-identical attributes", "zh": "双胞胎：属性几乎全同"},
            "story": {
                "en": "Same DOB, same hometown, one character apart in the given name. Only the ID disagrees.",
                "zh": "同年同月同日生、同籍贯，名字只差一个字。只有证件号在坚持。",
            },
        },
        {
            "query_id": "Q6", "record_id": "R501", "scenario": "name_variant",
            "title": {"en": "An alias no model can bridge (honest miss)", "zh": "谁都接不上的曾用名（诚实的失手）"},
            "story": {
                "en": "\"Victor Chen\" vs 「陈志强」: zero surface overlap. Tuned gets closer — still below threshold. We show this on purpose.",
                "zh": "「Victor Chen」与「陈志强」：表面零重合。微调后分数更近了——但仍未过线。我们故意展示这个。",
            },
        },
    ]


def scripted_pairs(world) -> list[dict]:
    """Explain 视图的 4 张匹配解释卡。字段态/分数由 simulate 层真实计算或剧本钉死。"""
    rmap = world.record_map()
    specs = [
        {
            "pair_id": "P1", "a": "R401", "b": "R402", "truth": "match",
            "title": {"en": "Transliteration: same person, alien surfaces", "zh": "音译变体：同一人，两张面孔"},
            "note": {
                "en": "Name similarity is ~0.12 lexically — baseline is blind; the tuned model learned the equivalence.",
                "zh": "字面相似度只有 ~0.12——baseline 全盲；微调后的模型学会了这层等价。",
            },
        },
        {
            "pair_id": "P2", "a": "R101", "b": "R201", "truth": "non_match",
            "title": {"en": "Same exact name, different people", "zh": "名字完全相同，却是两个人"},
            "note": {
                "en": "Name matches 100% — and that's the trap. DOB/ID/POB all disagree; baseline falls in, tuned climbs out.",
                "zh": "名字 100% 一致——这正是陷阱。生日/证件/籍贯全部不一致；baseline 掉了进去，微调爬了出来。",
            },
        },
        {
            "pair_id": "P3", "a": "R601", "b": "R602", "truth": "match",
            "title": {"en": "Conflict tolerated when ID is exact", "zh": "证件号全同时，容忍冲突"},
            "story_note": None,
            "note": {
                "en": "DOB says Feb 30 — impossible. Baseline computes 0.50, five hundredths below threshold, and flinches. Tuned weighs an exact 18-digit ID over one bad field: 0.77, linked.",
                "zh": "生日写的是 2 月 30 日——不存在的日期。baseline 算出 0.50，差 0.05 缩了回去。微调后的模型让 18 位全同的证件号压过一个坏字段：0.77，连上。",
            },
        },
        {
            "pair_id": "P4", "a": "R301", "b": "R303", "truth": "non_match",
            "title": {"en": "Twins: one character apart", "zh": "双胞胎：一字之差"},
            "note": {
                "en": "李小峰 vs 李小龙 — fuzzy name, identical DOB & hometown. Tuned separates them just below threshold.",
                "zh": "李小峰 vs 李小龙——名字模糊相近、生日籍贯全同。微调后的分数停在阈值之下，分开了他们。",
            },
        },
    ]
    from entity_arena.simulate import SCRIPTED_OVERRIDES

    out = []
    field_order = [("name", "Name", "姓名"), ("id_number", "ID Number", "证件号"),
                   ("dob", "Date of Birth", "出生日期"), ("pob", "Place of Origin", "籍贯"),
                   ("phone", "Phone", "电话")]
    for spec in specs:
        a, b = rmap[spec["a"]], rmap[spec["b"]]
        sims = field_sims(a, b)
        fields = []
        for key, en, zh in field_order:
            va = getattr(a, key)
            vb = getattr(b, key)
            sim = sims[key]
            fields.append({
                "key": key, "label": {"en": en, "zh": zh},
                "va": va if va is not None else "—",
                "vb": vb if vb is not None else "—",
                "sim": None if sim is None else round(sim, 2),
                "state": field_state(sim),
            })
        ov = SCRIPTED_OVERRIDES.get((a.record_id, b.record_id)) or \
            SCRIPTED_OVERRIDES.get((b.record_id, a.record_id))
        if ov:
            b_score, t_score = ov["baseline"], ov["tuned"]
        else:
            from entity_arena.simulate import baseline_score

            b_score = round(baseline_score(sims), 2)
            # P3 由公式给出（证件号全同 → 双模型都应过线）
            t_score = round(0.30 * b_score + 0.70 * 0.90, 2)
        out.append({
            "pair_id": spec["pair_id"], "a": a.record_id, "b": b.record_id,
            "truth": spec["truth"], "title": spec["title"], "note": spec["note"],
            "fields": fields,
            "scores": {"baseline": b_score, "tuned": t_score},
            "verdicts": {
                "baseline": "match" if b_score >= THRESHOLD else "no_match",
                "tuned": "match" if t_score >= THRESHOLD else "no_match",
            },
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    args = ap.parse_args()

    world = build_world(args.seed)
    results = retrieve_all(world, args.seed)
    metrics = compute_metrics(world, results)
    network = build_network(world, args.seed)
    queries = scripted_queries()
    pairs = scripted_pairs(world)

    args.out.mkdir(parents=True, exist_ok=True)

    with open(args.out / "records.jsonl", "w", encoding="utf-8") as f:
        for r in sorted(world.all_records(), key=lambda x: x.record_id):
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    (args.out / "results.json").write_text(
        json.dumps({"k": 5, "models": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "network.json").write_text(
        json.dumps(network, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "queries.json").write_text(
        json.dumps(queries, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "pairs.json").write_text(
        json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "meta.json").write_text(
        json.dumps({
            "seed": args.seed,
            "n_entities": len(world.entities),
            "n_records": len(world.all_records()),
            "n_relations": len(world.relations),
            "sources": SOURCE_META,
            "scenarios": SCENARIO_META,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    entities_meta = {
        e.entity_id: {
            "canonical": e.canonical,
            "canonical_latin": e.canonical_latin,
            "lookalikes": e.lookalikes,
            "members": sorted(r.record_id for r in e.records),
        }
        for e in world.entities
    }
    (args.out / "entities.json").write_text(
        json.dumps(entities_meta, ensure_ascii=False, indent=1), encoding="utf-8")

    o = metrics["overall"]
    print(f"entities={len(world.entities)} records={len(world.all_records())} "
          f"relations={len(world.relations)}")
    for m in ("baseline", "tuned"):
        d = o[m]
        print(f"  {m:<9} recall@5={d['recall@5']:.3f}  link_recall={d['link_recall']:.3f}  "
              f"link_precision={d['link_precision']:.3f}  false_link={d['false_link_rate']:.3f}")
    print("--- by scenario (link_recall, asserted) ---")
    for s in metrics["by_scenario"]:
        b = s["baseline"].get("link_recall")
        t = s["tuned"].get("link_recall")
        fb = s["baseline"].get("false_link_rate")
        ft = s["tuned"].get("false_link_rate")
        extra = f"  (false-link {fb}→{ft})" if fb is not None else ""
        bb = f"{b:.2f}" if b is not None else " n/a"
        tt = f"{t:.2f}" if t is not None else " n/a"
        print(f"  {s['scenario']:<15} n={s['n']:<3} {bb} → {tt}{extra}")
    print(f"data written to {args.out}/")


if __name__ == "__main__":
    main()
