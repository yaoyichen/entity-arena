#!/usr/bin/env python3
"""案例村 + 模型向量/分数 → data/open_cases.json（网络视图 Febrl3 实测轨数据）。

两种输入（二选一，输出逐字节一致）：
  --emb .tmp/village_emb_tuned.json       首次：由 fetch_village_embeddings.py 取回的向量，
                                          本脚本计算余弦相似度并把成对分数写入
                                          data_open/village_scores.json（入库溯源）
  --scores data_open/village_scores.json  复算：直接用已入库分数，无需服务在线

统计主管线（θ 扫描、断言链、组件、指标、Arena/Explain 极值规则、试玩台矩阵）
在 `src/entity_arena/cases_core.py`（schema 无关，与租户入口共用）；本文件是
**Febrl3 适配器**：注入字段词典、基线权重、卡片/查询文案、village 溯源。
`tests/test_tenant_cases.py::TestFebrlRebuildAnchor` 锁定重构后输出与
data/open_cases.json 逐字节一致——改 cases_core 统计逻辑先跑该锚。

诚实口径：θ 在本案例村上按 F1 选（口径随数据公开，写在 provenance 里）；
误连/漏连对着 Febrl3 真值算，页面上红=误连、青=正确归并。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.cases_core import (  # noqa: E402
    build_cases_payload,
    cosine,
    fmt_w,
)

# ---- Febrl3 专属：字段词典 / 基线代理权重 / 展示名 / Explain 卡存根 ----

_FIELD_LABELS = {
    "name": {"en": "Name", "zh": "姓名"},
    "dob": {"en": "Date of birth", "zh": "生日"},
    "address": {"en": "Address", "zh": "地址"},
    "locality": {"en": "Locality", "zh": "地区"},
    "ssn": {"en": "ID Number", "zh": "证件号"},
}

_BASE_W = {"name": 0.40, "dob": 0.20, "address": 0.20, "locality": 0.10, "ssn": 0.10}


def _name_of(recs: dict, rid: str) -> str:
    return recs[rid]["fields"].get("name") or rid


def _stub_of(rec) -> dict:
    rid, f = rec["rid"], rec["fields"]
    return {"record_id": rid, "name": f.get("name") or rid,
            "dob": f.get("dob"), "id_number": f.get("ssn"),
            "source": "febrl3"}


def _narrate_explain(pid: str, ctx: dict) -> tuple[dict, dict]:
    """R1–R6 卡文案（ctx 只带已算好的统计量；文案层不反算）。"""
    a, b, s, th = ctx["a"], ctx["b"], ctx["s"], ctx["theta"]
    if pid == "R1":
        nd, nr = ctx["n_diff"], ctx["n_rows"]
        return ({"en": "Real · messy records, same person", "zh": "实测 · 错字风暴，仍是同一人"},
                {"en": f"Febrl3 ground truth: same entity. {nd} of {nr} fields differ "
                       f"(typos/missing), yet the fine-tuned model scores {s:.3f} ≥ θ and links them. "
                       f"Live: python scripts/live_pair_score.py --rids {a},{b}",
                 "zh": f"Febrl3 真值：同一实体。{nr} 个字段里 {nd} 个有差异（错字/缺失），"
                       f"微调模型仍给出 {s:.3f} ≥ θ，断言连接。现场复现："
                       f"python scripts/live_pair_score.py --rids {a},{b}"})
    if pid == "R2":
        return ({"en": "Real · look-alikes, two people", "zh": "实测 · 长得像的两个人"},
                {"en": f"Febrl3 ground truth: different people. This is the highest-scoring refused "
                       f"pair in the village ({s:.3f} < θ {th:.3f}) — the strongest look-alike "
                       f"temptation still gets resisted. Live: --rids {a},{b}",
                 "zh": f"Febrl3 真值：不同人。这是案例村里分数最高的被拒对"
                       f"（{s:.3f} < θ {th:.3f}）——最强的“像”也没能骗过模型。"
                       f"现场复现：python scripts/live_pair_score.py --rids {a},{b}"})
    if pid == "R3":
        return ({"en": "Real · closest call that linked", "zh": "实测 · 最惊险的连对"},
                {"en": f"Febrl3 ground truth: same entity. The lowest-scoring correct link in the "
                       f"village ({s:.3f}, only {s - th:.3f} above θ) still clears the bar — "
                       f"no true pair sits within the margin. Live: --rids {a},{b}",
                 "zh": f"Febrl3 真值：同一实体。全村分数最低的正确连对（{s:.3f}，仅超 θ "
                       f"{s - th:.3f}）仍稳过线——边距之内没有任何真对。"
                       f"现场复现：python scripts/live_pair_score.py --rids {a},{b}"})
    if pid == "R4":
        ne = ctx["n_exact"]
        return ({"en": "Real · identical fields, refused", "zh": "实测 · 字段全同，仍拒连"},
                {"en": f"Febrl3 ground truth: different people. {ne} fields are exactly equal "
                       f"(name twins / same suburb), the literal world would merge them; the "
                       f"fine-tuned model refuses ({s:.3f} < θ). Live: --rids {a},{b}",
                 "zh": f"Febrl3 真值：不同人。{ne} 个字段逐字相同（同名/同区），字面世界早该"
                       f"并成一人；微调模型仍拒连（{s:.3f} < θ）。"
                       f"现场复现：python scripts/live_pair_score.py --rids {a},{b}"})
    bs = ctx["b_score"]
    if pid == "R5":
        return ({"en": "Real · baseline flinched, tuned linked", "zh": "实测 · 基线不敢连，微调连上"},
                {"en": f"Febrl3 ground truth: same entity. Literal-similarity proxy gives "
                       f"{bs:.3f} < θ_baseline and flinches; the fine-tuned model scores "
                       f"{s:.3f} ≥ θ and asserts (gap {s - bs:.3f}). Live: --rids {a},{b}",
                 "zh": f"Febrl3 真值：同一实体。字面相似度代理只给 {bs:.3f} < 基线θ，不敢断言；"
                       f"微调模型 {s:.3f} ≥ θ 连上（分差 {s - bs:.3f}）。"
                       f"现场复现：python scripts/live_pair_score.py --rids {a},{b}"})
    return ({"en": "Real · baseline merged, tuned refused", "zh": "实测 · 基线误连，微调拒连"},
            {"en": f"Febrl3 ground truth: different people. The most confident false merge "
                   f"of the literal proxy ({bs:.3f} ≥ θ_baseline) is refused by the "
                   f"fine-tuned model ({s:.3f} < θ). Live: --rids {a},{b}",
             "zh": f"Febrl3 真值：不同人。字面代理最自信的误连（{bs:.3f} ≥ 基线θ）被微调"
                   f"模型拒掉（{s:.3f} < θ）。"
                   f"现场复现：python scripts/live_pair_score.py --rids {a},{b}"})


def _narrate_arena(cat: str, ctx: dict) -> tuple[dict, dict]:
    """F1–F6 查询文案（ctx 只带已算好的统计量）。"""
    if cat == "big_entity":
        n, mean, links_b = ctx["n"], ctx["mean"], ctx["links_b"]
        return ({"en": f"Big family: {n} records, one person",
                 "zh": f"大家族：{n} 条记录一个人"},
                {"en": f"Largest entity in the village ({n} records). The query "
                       f"is its weakest member (mean sibling similarity {mean:.3f}). "
                       f"The fine-tuned model asserts all {n - 1} true siblings "
                       f"above θ; the literal baseline asserts {links_b}.",
                 "zh": f"案例村最大的实体（{n} 条记录）。查询取其中最弱的一员"
                       f"（兄弟均分 {mean:.3f}）。微调模型把全部 {n - 1} 条真身断言过 θ；"
                       f"字面基线只断言 {links_b} 条。"})
    if cat == "missing":
        km, st, sb, nf = ctx["km"], ctx["st"], ctx["sb"], ctx["n_fields"]
        return ({"en": "Missing evidence", "zh": "证据缺失"},
                {"en": f"This record is missing {km} of {nf} fields. Evidence is thin, "
                       f"yet the fine-tuned model still links its true sibling "
                       f"({st:.3f} ≥ θ); the literal baseline gives {sb:.3f}.",
                 "zh": f"这条记录 {nf} 个字段缺了 {km} 个。证据残缺，微调模型仍把真身"
                       f"连上（{st:.3f} ≥ θ）；字面基线只给 {sb:.3f}。"})
    if cat == "lookalike":
        m, fb = ctx["m"], ctx["fb"]
        return ({"en": "Look-alike entanglement", "zh": "影子纠缠"},
                {"en": f"Entangled with {m} look-alike records (same surname / "
                       f"suburb / DOB — different people). Top-5 is full of "
                       f"temptations: the fine-tuned model refuses every shadow "
                       f"(village-wide false links: 0); the literal baseline "
                       f"asserts {fb} of them.",
                 "zh": f"与 {m} 条异实体记录构成影子关系（同姓/同区/同生日，"
                       f"却不是同一人）。top-5 里满是“像”的诱惑：微调模型全部拒连"
                       f"（全村零误连）；字面基线断言了其中 {fb} 条。"})
    if cat == "false_link":
        bb, st, nm = ctx["bb"], ctx["st"], ctx["nm"]
        return ({"en": "Baseline's false merge", "zh": "基线误连"},
                {"en": f"Febrl3 truth: different people. The literal baseline "
                       f"scores {bb:.3f} ≥ θ and wrongly merges this record with "
                       f"“{nm}”; the fine-tuned model scores {st:.3f} < θ and "
                       f"refuses. A false merge invents a person who never existed.",
                 "zh": f"Febrl3 真值：不同人。字面基线给 {bb:.3f} ≥ θ，把本记录与"
                       f"“{nm}”错误归并；微调模型 {st:.3f} < θ 拒连。"
                       f"误连会凭空捏造一个不存在的人。"})
    if cat == "missed_link":
        bb, st, gap = ctx["bb"], ctx["st"], ctx["gap"]
        return ({"en": "Baseline's missed link", "zh": "基线漏连"},
                {"en": f"Febrl3 truth: same person. The literal baseline gives only "
                       f"{bb:.3f} < θ and dares not link; the fine-tuned model "
                       f"scores {st:.3f} ≥ θ and asserts — a {gap:.3f} gap on the "
                       f"same pair.",
                 "zh": f"Febrl3 真值：同一人。字面基线只给 {bb:.3f} < θ 不敢断言；"
                       f"微调模型 {st:.3f} ≥ θ 连上——同一对上 {gap:.3f} 的分差。"})
    nd, n, links_b = ctx["nd"], ctx["n"], ctx["links_b"]
    return ({"en": "Typo storm", "zh": "错字风暴"},
            {"en": f"{nd} field comparisons differ from its true siblings "
                   f"(typos / missing) — among the messiest records in the "
                   f"village. The fine-tuned model still links every sibling; "
                   f"the literal baseline {links_b}/{n - 1}.",
             "zh": f"与真身兄弟相比有 {nd} 处字段差异（错字/缺失）——全村最乱的"
                   f"记录之列。微调模型仍把兄弟全部连上；字面基线连上 "
                   f"{links_b}/{n - 1}。"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--village", type=Path, default=ROOT / "data_open" / "village.json")
    ap.add_argument("--emb", type=Path, default=None, help="向量文件（首次路径）")
    ap.add_argument("--scores", type=Path, default=ROOT / "data_open" / "village_scores.json",
                    help="成对分数（入库溯源 / 复算路径）")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "open_cases.json")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    village = json.loads(args.village.read_text(encoding="utf-8"))
    records = village["records"]
    rids = [r["rid"] for r in records]
    ent_of = {r["rid"]: r["entity"] for r in records}

    # ---- 成对分数：从向量算（并写溯源文件）或直接读入库分数
    if args.emb is not None:
        emb = json.loads(args.emb.read_text(encoding="utf-8"))
        vecs = emb["vectors"]
        missing = [rid for rid in rids if rid not in vecs]
        if missing:
            raise SystemExit(f"向量缺失 {len(missing)} 条，如 {missing[:3]}")
        pairs: list[tuple[str, str, float]] = []
        for i, a in enumerate(rids):
            for b in rids[i + 1:]:
                pairs.append((a, b, cosine(vecs[a], vecs[b])))
        pairs.sort(key=lambda p: (p[0], p[1]))
        args.scores.parent.mkdir(parents=True, exist_ok=True)
        args.scores.write_text(json.dumps({
            "model": emb["model"], "dim": emb["dim"], "n_records": len(rids),
            "pairs": [[a, b, round(s, 6)] for a, b, s in pairs],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        model_name, dim = emb["model"], emb["dim"]
    else:
        sc = json.loads(args.scores.read_text(encoding="utf-8"))
        pairs = [(a, b, float(s)) for a, b, s in sc["pairs"]]
        model_name, dim = sc["model"], sc["dim"]

    shadow = [tuple(p) for p in village["shadow_pairs"]]
    provenance = {
        **village["provenance"],
        "scores_file": "data_open/village_scores.json",
        "theta_rule": "案例村上 link-F1 最大化（平局：精度优先、再 θ 大者）",
        "missed_rule": "漏连=真对落在不同组件（组件级漏并，与图上红虚线 ⊘ 一一对应）；"
                       "直接断言层面漏掉但被链式合并进同一组件的真对不计——"
                       "对级漏连口径由记分牌 top-5 断言指标覆盖",
        "truth": "Febrl3 rec_id 实体号（生成器 ground truth）",
        "model_role": f"微调后模型 {model_name} 的真实向量余弦；基线无法部署（平台仅支持已训练模型），逐条基线分数不可得",
        "baseline_rule": "代理基线=经典字面相似度 ER 打分器：字段 Levenshtein 加权"
                         f"（{fmt_w(_BASE_W)}），单侧缺失=0.5 中性；θ 与微调同规则 F1 扫描。"
                         "非基模逐条分数——真实基线检索指标见记分牌（开源基准轨）",
        "arena_rule": "Arena 六查询按类别规则挑选（确定性排序键、对叙事盲、先于任何"
                      "文案）：F1 最大实体中兄弟均分最低成员 / F2 自身缺失字段最多 / "
                      "F3 影子对度数最高 / F4 基线 top-5 内最自信误断言 / F5 基线 "
                      "top-5 内漏断真链中双模型分差最大 / F6 对兄弟字段非全同累计"
                      "最多；依次取用并去重，类别池空则缺席并记于 arena.absent。"
                      "微调列=village_scores 真实余弦；基线列=字面代理（同上）",
        "metrics_rule": "检索评测口径与合成轨 simulate.py 一致：每条记录作查询、"
                        "检索其余取 top-5、hit=同实体；断言=top-5 内分数 ≥ 各模型"
                        "自己的 θ（各自由 F1 扫描选出）；方向性计数（每条真链路两端"
                        "各计一次，跨轨可比）；false_link_rate=影子纠缠查询中断言了"
                        "影子伙伴的占比；分桶优先级 missing>conflict>typo_storm(≥3 "
                        "fuzzy)>typo(1-2 fuzzy)>lookalike>clean（对同实体兄弟逐字段"
                        "可复算）；单记录实体查询无真身链路，recall/link 指标记 "
                        "null（页面渲染'—'），不算 0。两模型 recall@5 均为 1.0 属"
                        "本村数据性质（真值对分数全域高于跨实体对）——差异体现在"
                        "断言指标，照登不隐藏",
        "explain_rule": "实测卡极值规则（对叙事盲）：R1 连对真对中缺失→差异字段最多；"
                        "R2 被拒异实体对中分数最高；R3 连对真对中分数最低（贴线）；"
                        "R4 被拒对中 exact 字段最多；R5 基线漏连真对中双模型分差最大；"
                        "R6 基线误连异实体对中最自信。全部要求微调侧距 θ ≥0.01"
                        "（GPU 抖动 ~0.005 不翻判定）；R5/R6 的基线分数为字面代理，"
                        "只出现在 note 文本",
        "play_rule": "试玩台（页面内 playground）分数 = village_scores 全量两两对"
                     "按 1e-4 网格量化的真实余弦（u16 打包嵌入页面，排序 rid 上三角"
                     "行主序索引）；构建期逐对断言量化判定与全精度判定一致，"
                     "不一致即 raise。页内任意挑对打分与 MCP er_score_pair 离线模式"
                     "同源同值",
    }

    payload = build_cases_payload(
        records=records, ent_of=ent_of, pairs=pairs, model_name=model_name,
        dim=dim, shadow_pairs=shadow, fields_spec=_FIELD_LABELS, weights=_BASE_W,
        name_of=_name_of, stub_of=_stub_of, narrate_explain=_narrate_explain,
        narrate_arena=_narrate_arena, provenance=provenance, seed=args.seed,
    )
    stats, stats_b = payload["stats"], payload["baseline"]["stats"]
    arena, metrics, play = payload["arena"], payload["metrics"], payload["play"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                        encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"  model={model_name} θ={payload['theta']:.4f}")
    print(f"  truth={stats['truth_pairs']} asserted={stats['asserted']} tp={stats['tp']} "
          f"false={stats['false_links']} missed={stats['missed_pairs']}")
    print(f"  components={stats['components']} (pure={stats['pure_components']})")
    print(f"  baseline(proxy) θ={payload['baseline']['theta']:.4f} "
          f"asserted={stats_b['asserted']} tp={stats_b['tp']} "
          f"false={stats_b['false_links']} missed={stats_b['missed_pairs']} "
          f"components={stats_b['components']} (pure={stats_b['pure_components']})")
    print(f"  arena: {len(arena['queries'])} queries"
          + (f" (absent: {','.join(arena['absent'])})" if arena["absent"] else ""))
    ov = metrics["overall"]
    print(f"  metrics: baseline link_recall={ov['baseline']['link_recall']} "
          f"link_precision={ov['baseline']['link_precision']} | "
          f"tuned link_recall={ov['tuned']['link_recall']} "
          f"link_precision={ov['tuned']['link_precision']}")
    print(f"  explain cards: {[c['pair_id'] for c in payload['explain']['pairs']]}")
    print(f"  play matrix: {play['n']} rids, {play['n'] * (play['n'] - 1) // 2} pairs "
          f"(b64 {len(play['b64']) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
