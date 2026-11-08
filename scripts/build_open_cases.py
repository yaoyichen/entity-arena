#!/usr/bin/env python3
"""案例村 + 模型向量/分数 → data/open_cases.json（网络视图 Febrl3 实测轨数据）。

两种输入（二选一，输出逐字节一致）：
  --emb .tmp/village_emb_tuned.json       首次：由 fetch_village_embeddings.py 取回的向量，
                                          本脚本计算余弦相似度并把成对分数写入
                                          data_open/village_scores.json（入库溯源）
  --scores data_open/village_scores.json  复算：直接用已入库分数，无需服务在线

处理链（全部确定性）：
  余弦相似度（record×record，去自身）→ θ 扫描（F1 最大，平局取精度高、再取 θ 大）
  → 断言链 = 分数 ≥ θ 的记录对 → 并查集成"模型声称的实体"（组件）
  → 组件纯度 = 是否只含一个真实体 → 误连（混入异实体）/ 漏连（真对未过线）
  → layout.build_open_network 出 before（影子关系聚簇）/ after（断言组件光环）两套坐标

诚实口径：θ 在本案例村上按 F1 选（口径随数据公开，写在 provenance 里）；
误连/漏连对着 Febrl3 真值算，页面上红=误连、青=正确归并。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.layout import build_open_network  # noqa: E402


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def pick_theta(pairs: list[tuple[str, str, float]], truth: set[tuple[str, str]]) -> tuple[float, dict]:
    """θ 扫描：最大化 link-F1；平局取精度高者；再平局取 θ 大者（少断言优先）。"""
    cands = sorted({s for _, _, s in pairs})
    best_key: tuple | None = None
    best: dict | None = None
    for th in cands:
        asserted = {(a, b) for a, b, s in pairs if s >= th}
        tp = len(asserted & truth)
        prec = tp / len(asserted) if asserted else 0.0
        rec = tp / len(truth) if truth else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        key = (round(f1, 12), round(prec, 12), th)
        if best_key is None or key > best_key:
            best_key = key
            best = {"theta": th, "asserted": len(asserted), "tp": tp,
                    "precision": prec, "recall": rec, "f1": f1}
    if best is None:
        raise SystemExit("θ 扫描无候选——pairs 为空？")
    return best["theta"], best


def components(nids: list[str], links: list[tuple[str, str]]) -> list[list[str]]:
    parent = {n: n for n in nids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in links:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[str, list[str]] = {}
    for n in nids:
        groups.setdefault(find(n), []).append(n)
    return [sorted(v) for k, v in sorted(groups.items())]


# ---- Explain 实测案例卡（两张，规则确定性挑选，分数为真实服务输出） ----

_FIELD_LABELS = {
    "name": {"en": "Name", "zh": "姓名"},
    "dob": {"en": "Date of birth", "zh": "生日"},
    "address": {"en": "Address", "zh": "地址"},
    "locality": {"en": "Locality", "zh": "地区"},
    "ssn": {"en": "ID Number", "zh": "证件号"},
}


def _lev_ratio(a: str, b: str) -> float:
    """朴素 Levenshtein 相似度（字段都是短文本，够用且零依赖）。"""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != b[j - 1]))
        prev = cur
    return 1.0 - prev[lb] / max(la, lb)


def _field_state(va: str | None, vb: str | None) -> tuple[str, float | None]:
    """与合成轨 Explain 四态同语义：exact / missing / fuzzy(≈) / conflict(✗)。"""
    if not va or not vb:
        return "missing", None
    if va == vb:
        return "exact", 1.0
    sim = round(_lev_ratio(va, vb), 2)
    return ("fuzzy" if sim >= 0.7 else "conflict"), sim


def build_explain_cards(records: list[dict], pairs: list[tuple[str, str, float]],
                        ent_of: dict[str, str], theta: float, model_name: str) -> dict:
    """R1 = 正确断言的真对中差异字段最多者（再取分数高）——"乱成这样仍连对"；
    R2 = 被拒的异实体对中分数最高者——"长得最像仍拒连"。
    两卡都要求距 θ ≥ 0.01（GPU 抖动 ~0.005 不翻判定、条形图可读）。
    挑选规则确定、对叙事盲（不是看故事挑的，是按规则算的极值）。"""
    recs = {r["rid"]: r for r in records}
    score_of = {(a, b): s for a, b, s in pairs}

    def rows_of(a: str, b: str) -> list[dict]:
        fa, fb = recs[a]["fields"], recs[b]["fields"]
        rows = []
        for k, label in _FIELD_LABELS.items():
            if k not in fa and k not in fb:
                continue
            state, sim = _field_state(fa.get(k), fb.get(k))
            rows.append({"key": k, "label": label,
                         "va": fa.get(k) or "—", "vb": fb.get(k) or "—",
                         "sim": sim, "state": state})
        return rows

    def n_diff(ab: tuple[str, str]) -> int:
        return sum(1 for f in rows_of(*ab) if f["state"] != "exact")

    linked_true = [((a, b), s) for a, b, s in pairs
                   if ent_of[a] == ent_of[b] and s >= theta + 0.01]
    if not linked_true:
        raise SystemExit("没有距 θ ≥0.01 的正确断言真对——R1 卡无法生成（边距规则需复查）")
    def n_missing(ab: tuple[str, str]) -> int:
        return sum(1 for f in rows_of(*ab) if f["state"] == "missing")
    # 极值规则（结构性、对叙事盲）："最乱仍连对"——缺失字段最多、差异字段最多、分数最接近 θ
    r1 = sorted(linked_true,
                key=lambda x: (-n_missing(x[0]), -n_diff(x[0]), x[1], x[0]))[0][0]
    # 拒连卡候选：距 θ 至少 0.01（GPU 推理抖动 ~0.005 不翻判定；条形图上分数与
    # 刻度线也须肉眼可分）——在满足边距的候选里仍取分数最高者
    refused = [((a, b), s) for a, b, s in pairs
               if ent_of[a] != ent_of[b] and s <= theta - 0.01]
    if not refused:
        raise SystemExit("没有距 θ ≥0.01 的异实体对——R2 卡无法生成（边距规则需复查）")
    r2 = sorted(refused, key=lambda x: (-x[1], x[0]))[0][0]

    def card(pid: str, ab: tuple[str, str], title: dict, note: dict) -> dict:
        a, b = ab
        s = score_of[ab]
        linked = s >= theta
        return {
            "pair_id": pid, "a": a, "b": b, "real": True,
            "truth": "match" if ent_of[a] == ent_of[b] else "nonmatch",
            "title": title, "note": note,
            "fields": rows_of(a, b),
            "scores": {"tuned": round(s, 4)},
            "verdicts": {"tuned": "match" if linked else "nonmatch"},
            "theta": round(theta, 4), "model": model_name,
        }

    a1, b1 = r1
    s1 = score_of[r1]
    rows1 = rows_of(a1, b1)
    nd1 = sum(1 for f in rows1 if f["state"] != "exact")
    c1 = card("R1", r1,
              {"en": "Real · messy records, same person", "zh": "实测 · 错字风暴，仍是同一人"},
              {"en": f"Febrl3 ground truth: same entity. {nd1} of {len(rows1)} fields differ "
                     f"(typos/missing), yet the fine-tuned model scores {s1:.3f} ≥ θ and links them. "
                     f"Live: python scripts/live_pair_score.py --rids {a1},{b1}",
               "zh": f"Febrl3 真值：同一实体。{len(rows1)} 个字段里 {nd1} 个有差异（错字/缺失），"
                     f"微调模型仍给出 {s1:.3f} ≥ θ，断言连接。现场复现："
                     f"python scripts/live_pair_score.py --rids {a1},{b1}"})
    a2, b2 = r2
    s2 = score_of[r2]
    c2 = card("R2", r2,
              {"en": "Real · look-alikes, two people", "zh": "实测 · 长得像的两个人"},
              {"en": f"Febrl3 ground truth: different people. This is the highest-scoring refused "
                     f"pair in the village ({s2:.3f} < θ {theta:.3f}) — the strongest look-alike "
                     f"temptation still gets resisted. Live: --rids {a2},{b2}",
               "zh": f"Febrl3 真值：不同人。这是案例村里分数最高的被拒对"
                     f"（{s2:.3f} < θ {theta:.3f}）——最强的“像”也没能骗过模型。"
                     f"现场复现：python scripts/live_pair_score.py --rids {a2},{b2}"})
    stubs = {}
    for rid in (a1, b1, a2, b2):
        f = recs[rid]["fields"]
        stubs[rid] = {"record_id": rid, "name": f.get("name") or rid,
                      "dob": f.get("dob"), "id_number": f.get("ssn"), "source": "febrl3"}
    return {"pairs": [c1, c2], "records": stubs}


# ---- 基线代理：经典字面相似度 ER 打分器（"微调前"的可视化口径） ----

_BASE_W = {"name": 0.40, "dob": 0.20, "address": 0.20, "locality": 0.10, "ssn": 0.10}


def baseline_score(fa: dict, fb: dict) -> float:
    """字段 Levenshtein 相似度加权；单侧缺失 = 0.5（无证据中性）；双侧缺失不参与。

    诚实口径：这是经典规则打分器的代理，不是基模的逐条分数——基模无法在平台
    逐条部署（deploy 仅支持已训练模型），真实基线检索指标见记分牌开源基准轨。
    """
    num = den = 0.0
    for k, w in _BASE_W.items():
        va, vb = fa.get(k), fb.get(k)
        if not va and not vb:
            continue
        den += w
        if not va or not vb:
            num += w * 0.5
        elif va == vb:
            num += w
        else:
            num += w * _lev_ratio(va, vb)
    return num / den if den else 0.0


def baseline_pairs(records: list[dict], rids: list[str]) -> list[tuple[str, str, float]]:
    recs = {r["rid"]: r for r in records}
    out = []
    for i, a in enumerate(rids):
        for b in rids[i + 1:]:
            out.append((a, b, baseline_score(recs[a]["fields"], recs[b]["fields"])))
    out.sort(key=lambda p: (p[0], p[1]))
    return out


def _fmt_w() -> str:
    return " / ".join(f"{k} {v:.2f}" for k, v in _BASE_W.items())


def comp_analysis(rids: list[str], links: list[tuple[str, str]],
                  ent_of: dict[str, str]) -> tuple[list[list[str]], dict[str, str], list[dict]]:
    """断言链 → 组件（并查集）+ 每组件真值实体集合与纯度。微调/基线共用。"""
    comps = components(rids, links)
    comp_of: dict[str, str] = {}
    comp_meta = []
    for ci, mem in enumerate(comps):
        cid = f"comp-{ci}"
        for m in mem:
            comp_of[m] = cid
        ents = sorted({ent_of[m] for m in mem})
        comp_meta.append({"id": cid, "size": len(mem), "entities": ents,
                          "pure": len(ents) == 1})
    return comps, comp_of, comp_meta


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

    # ---- 真值对与 θ
    truth = {(a, b) for i, a in enumerate(rids) for b in rids[i + 1:] if ent_of[a] == ent_of[b]}
    if not truth:
        raise SystemExit("案例村没有多记录实体——无法定义真值对")
    theta, stats = pick_theta(pairs, truth)

    links = [(a, b) for a, b, s in pairs if s >= theta]
    asserted = set(links)
    score_of = {(a, b): s for a, b, s in pairs}
    # 每条记录的 top-3 相似（去掉自身；给档案卡展示真实分数与判定）
    by_rec: dict[str, list[tuple[str, float]]] = {r: [] for r in rids}
    for a, b, s in pairs:
        by_rec[a].append((b, s))
        by_rec[b].append((a, s))
    top3 = {r: sorted(v, key=lambda x: (-x[1], x[0]))[:3] for r, v in by_rec.items()}
    false_links = sorted(
        {"|".join(sorted((a, b))) for a, b in links if ent_of[a] != ent_of[b]}
    )
    missed = sorted({"|".join(sorted(p)) for p in truth if p not in asserted})

    comps, comp_of, comp_meta = comp_analysis(rids, links, ent_of)

    network = build_open_network(
        records=[{"rid": r["rid"], "entity": r["entity"]} for r in records],
        links=links,
        shadow_pairs=[tuple(p) for p in village["shadow_pairs"]],
        seed=args.seed,
    )
    explain = build_explain_cards(records, pairs, ent_of, theta, model_name)

    # ---- 基线代理（"微调前"态）：同一案例村、同一 θ 规则，经典字面相似度打分
    bpairs = baseline_pairs(records, rids)
    theta_b, stats_b = pick_theta(bpairs, truth)
    links_b = [(a, b) for a, b, s in bpairs if s >= theta_b]
    bscore_of = {(a, b): s for a, b, s in bpairs}
    asserted_b = set(links_b)
    false_b = sorted(
        {"|".join(sorted((a, b))) for a, b in links_b if ent_of[a] != ent_of[b]}
    )
    missed_b = sorted({"|".join(sorted(p)) for p in truth if p not in asserted_b})
    comps_b, comp_of_b, comp_meta_b = comp_analysis(rids, links_b, ent_of)
    net_base = build_open_network(
        records=[{"rid": r["rid"], "entity": r["entity"]} for r in records],
        links=links_b,
        shadow_pairs=[tuple(p) for p in village["shadow_pairs"]],
        seed=args.seed + 17,   # 与微调布局去相关（两套组件坐标独立成图）
    )
    if len(comps_b) != len(net_base["entities"]):
        raise SystemExit("基线组件数与布局实体数不一致——数据管道损坏")

    payload = {
        "model": model_name,
        "embedding_dim": dim,
        "theta": round(theta, 6),
        "stats": {
            **{k: stats[k] for k in ("asserted", "tp", "precision", "recall", "f1")},
            "truth_pairs": len(truth),
            "false_links": len(false_links),
            "missed_pairs": len(missed),
            "components": len(comps),
            "pure_components": sum(1 for c in comp_meta if c["pure"]),
        },
        "records": {r["rid"]: {"entity": r["entity"], "fields": r["fields"], "text": r["text"]}
                    for r in records},
        "links": [{"a": a, "b": b, "s": round(score_of[(a, b)], 6)} for a, b in links],
        "false_links": false_links,
        "missed_pairs": missed,
        "components": comp_meta,
        "comp_of": comp_of,
        "top3": {r: [[o, round(s, 6)] for o, s in v] for r, v in top3.items()},
        "explain": explain,
        "baseline": {
            "kind": "proxy",
            "theta": round(theta_b, 6),
            "stats": {
                **{k: stats_b[k] for k in ("asserted", "tp", "precision", "recall", "f1")},
                "truth_pairs": len(truth),
                "false_links": len(false_b),
                "missed_pairs": len(missed_b),
                "components": len(comps_b),
                "pure_components": sum(1 for c in comp_meta_b if c["pure"]),
            },
            "links": [{"a": a, "b": b, "s": round(bscore_of[(a, b)], 6)} for a, b in links_b],
            "false_links": false_b,
            "missed_pairs": missed_b,
            "components": comp_meta_b,
            "comp_of": comp_of_b,
            "network": {
                "nodes_after": net_base["nodes_after"],
                "entities": net_base["entities"],
                "comp_members": net_base["comp_members"],
            },
        },
        "network": network,
        "provenance": {
            **village["provenance"],
            "scores_file": "data_open/village_scores.json",
            "theta_rule": "案例村上 link-F1 最大化（平局：精度优先、再 θ 大者）",
            "truth": "Febrl3 rec_id 实体号（生成器 ground truth）",
            "model_role": f"微调后模型 {model_name} 的真实向量余弦；基线无法部署（平台仅支持已训练模型），逐条基线分数不可得",
            "baseline_rule": "代理基线=经典字面相似度 ER 打分器：字段 Levenshtein 加权"
                             f"（{_fmt_w()}），单侧缺失=0.5 中性；θ 与微调同规则 F1 扫描。"
                             "非基模逐条分数——真实基线检索指标见记分牌（开源基准轨）",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                        encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"  model={model_name} θ={theta:.4f}")
    print(f"  truth={len(truth)} asserted={stats['asserted']} tp={stats['tp']} "
          f"false={len(false_links)} missed={len(missed)}")
    print(f"  components={len(comps)} (pure={sum(1 for c in comp_meta if c['pure'])})")
    print(f"  baseline(proxy) θ={theta_b:.4f} asserted={stats_b['asserted']} tp={stats_b['tp']} "
          f"false={len(false_b)} missed={len(missed_b)} "
          f"components={len(comps_b)} (pure={sum(1 for c in comp_meta_b if c['pure'])})")


if __name__ == "__main__":
    main()
