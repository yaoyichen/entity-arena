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

    comps = components(rids, links)
    comp_of: dict[str, str] = {}
    comp_meta = []
    for ci, mem in enumerate(comps):
        cid = f"comp-{ci}"
        for m in mem:
            comp_of[m] = cid
        ents = {ent_of[m] for m in mem}
        comp_meta.append({"id": cid, "size": len(mem), "entities": sorted(ents), "pure": len(ents) == 1})

    network = build_open_network(
        records=[{"rid": r["rid"], "entity": r["entity"]} for r in records],
        links=links,
        shadow_pairs=[tuple(p) for p in village["shadow_pairs"]],
        seed=args.seed,
    )

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
        "network": network,
        "provenance": {
            **village["provenance"],
            "scores_file": "data_open/village_scores.json",
            "theta_rule": "案例村上 link-F1 最大化（平局：精度优先、再 θ 大者）",
            "truth": "Febrl3 rec_id 实体号（生成器 ground truth）",
            "model_role": f"微调后模型 {model_name} 的真实向量余弦；基线无法部署（平台仅支持已训练模型），逐条基线分数不可得",
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


if __name__ == "__main__":
    main()
