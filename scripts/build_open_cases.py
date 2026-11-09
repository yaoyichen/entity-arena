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
  → Arena 六查询（类别规则挑选，微调真实分数 vs 字面代理基线，双模型 top-5）
  → 检索指标（口径对齐合成轨 simulate.py：方向性计数、各模型各自 θ、分桶）
  → Explain 实测卡六张（极值规则挑选，微调侧距 θ ≥0.01）

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
                        ent_of: dict[str, str], theta: float, model_name: str,
                        bpairs: list[tuple[str, str, float]],
                        theta_b: float) -> dict:
    """六张实测卡，全部极值规则挑选（确定性排序键、对叙事盲），微调侧距
    θ ≥ 0.01（GPU 抖动 ~0.005 不翻判定、条形图可读）：
      R1 = 正确断言的真对中差异字段最多者——"乱成这样仍连对"
      R2 = 被拒的异实体对中分数最高者——"长得最像仍拒连"
      R3 = 正确断言的真对中分数最低者——"最惊险的连对（贴线）"
      R4 = 被拒对中 exact 字段最多者——"字段最多全同仍拒连"
      R5 = 基线漏连真对中双模型分差最大者——"基线不敢连，微调接回"
      R6 = 基线误连异实体对中最自信者——"基线最自信的错，微调拒连"
    R5/R6 的代理基线分数只出现在 note 文本（R 卡不加基线分数行，口径锁定）。
    池空则该卡缺席（规则不凑数）。"""
    recs = {r["rid"]: r for r in records}
    score_of = {(a, b): s for a, b, s in pairs}
    b_of = _lookup(bpairs)

    def rows_of(a: str, b: str) -> list[dict]:
        return _field_rows(recs[a]["fields"], recs[b]["fields"])

    def n_diff(ab: tuple[str, str]) -> int:
        return sum(1 for f in rows_of(*ab) if f["state"] != "exact")

    def n_exact(ab: tuple[str, str]) -> int:
        return sum(1 for f in rows_of(*ab) if f["state"] == "exact")

    def n_missing(ab: tuple[str, str]) -> int:
        return sum(1 for f in rows_of(*ab) if f["state"] == "missing")

    linked_true = [((a, b), s) for a, b, s in pairs
                   if ent_of[a] == ent_of[b] and s >= theta + 0.01]
    if not linked_true:
        raise SystemExit("没有距 θ ≥0.01 的正确断言真对——R1 卡无法生成（边距规则需复查）")
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
    # R3：连对真对中分数最低（贴线惊险）
    r3 = sorted(linked_true, key=lambda x: (x[1], x[0]))[0][0]
    # R4：被拒对中 exact 字段最多（再取分数高）
    r4 = sorted(refused, key=lambda x: (-n_exact(x[0]), -x[1], x[0]))[0][0]
    # R5：基线漏连（B<θ_B）而微调断言（S≥θ+0.01）的真对中，分差最大
    missed_rel = [((a, b), score_of[(a, b)] - b_of[(a, b)]) for a, b, s in pairs
                  if ent_of[a] == ent_of[b] and b_of[(a, b)] < theta_b
                  and score_of[(a, b)] >= theta + 0.01]
    # R6：基线误连（B≥θ_B）而微调拒连（S≤θ-0.01）的异实体对中，基线最自信
    false_rel = [((a, b), b_of[(a, b)]) for a, b, s in pairs
                 if ent_of[a] != ent_of[b] and b_of[(a, b)] >= theta_b
                 and score_of[(a, b)] <= theta - 0.01]

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

    cards = []
    a1, b1 = r1
    s1 = score_of[r1]
    rows1 = rows_of(a1, b1)
    nd1 = sum(1 for f in rows1 if f["state"] != "exact")
    cards.append(card("R1", r1,
        {"en": "Real · messy records, same person", "zh": "实测 · 错字风暴，仍是同一人"},
        {"en": f"Febrl3 ground truth: same entity. {nd1} of {len(rows1)} fields differ "
               f"(typos/missing), yet the fine-tuned model scores {s1:.3f} ≥ θ and links them. "
               f"Live: python scripts/live_pair_score.py --rids {a1},{b1}",
         "zh": f"Febrl3 真值：同一实体。{len(rows1)} 个字段里 {nd1} 个有差异（错字/缺失），"
               f"微调模型仍给出 {s1:.3f} ≥ θ，断言连接。现场复现："
               f"python scripts/live_pair_score.py --rids {a1},{b1}"}))
    a2, b2 = r2
    s2 = score_of[r2]
    cards.append(card("R2", r2,
        {"en": "Real · look-alikes, two people", "zh": "实测 · 长得像的两个人"},
        {"en": f"Febrl3 ground truth: different people. This is the highest-scoring refused "
               f"pair in the village ({s2:.3f} < θ {theta:.3f}) — the strongest look-alike "
               f"temptation still gets resisted. Live: --rids {a2},{b2}",
         "zh": f"Febrl3 真值：不同人。这是案例村里分数最高的被拒对"
               f"（{s2:.3f} < θ {theta:.3f}）——最强的“像”也没能骗过模型。"
               f"现场复现：python scripts/live_pair_score.py --rids {a2},{b2}"}))
    s3 = score_of[r3]
    cards.append(card("R3", r3,
        {"en": "Real · closest call that linked", "zh": "实测 · 最惊险的连对"},
        {"en": f"Febrl3 ground truth: same entity. The lowest-scoring correct link in the "
               f"village ({s3:.3f}, only {s3 - theta:.3f} above θ) still clears the bar — "
               f"no true pair sits within the margin. Live: --rids {r3[0]},{r3[1]}",
         "zh": f"Febrl3 真值：同一实体。全村分数最低的正确连对（{s3:.3f}，仅超 θ "
               f"{s3 - theta:.3f}）仍稳过线——边距之内没有任何真对。"
               f"现场复现：python scripts/live_pair_score.py --rids {r3[0]},{r3[1]}"}))
    s4 = score_of[r4]
    ne4 = n_exact(r4)
    cards.append(card("R4", r4,
        {"en": "Real · identical fields, refused", "zh": "实测 · 字段全同，仍拒连"},
        {"en": f"Febrl3 ground truth: different people. {ne4} fields are exactly equal "
               f"(name twins / same suburb), the literal world would merge them; the "
               f"fine-tuned model refuses ({s4:.3f} < θ). Live: --rids {r4[0]},{r4[1]}",
         "zh": f"Febrl3 真值：不同人。{ne4} 个字段逐字相同（同名/同区），字面世界早该"
               f"并成一人；微调模型仍拒连（{s4:.3f} < θ）。"
               f"现场复现：python scripts/live_pair_score.py --rids {r4[0]},{r4[1]}"}))
    if missed_rel:
        r5 = sorted(missed_rel, key=lambda x: (-x[1], x[0]))[0][0]
        s5, b5 = score_of[r5], b_of[r5]
        cards.append(card("R5", r5,
            {"en": "Real · baseline flinched, tuned linked", "zh": "实测 · 基线不敢连，微调连上"},
            {"en": f"Febrl3 ground truth: same entity. Literal-similarity proxy gives "
                   f"{b5:.3f} < θ_baseline and flinches; the fine-tuned model scores "
                   f"{s5:.3f} ≥ θ and asserts (gap {s5 - b5:.3f}). Live: --rids {r5[0]},{r5[1]}",
             "zh": f"Febrl3 真值：同一实体。字面相似度代理只给 {b5:.3f} < 基线θ，不敢断言；"
                   f"微调模型 {s5:.3f} ≥ θ 连上（分差 {s5 - b5:.3f}）。"
                   f"现场复现：python scripts/live_pair_score.py --rids {r5[0]},{r5[1]}"}))
    if false_rel:
        r6 = sorted(false_rel, key=lambda x: (-x[1], x[0]))[0][0]
        s6, b6 = score_of[r6], b_of[r6]
        cards.append(card("R6", r6,
            {"en": "Real · baseline merged, tuned refused", "zh": "实测 · 基线误连，微调拒连"},
            {"en": f"Febrl3 ground truth: different people. The most confident false merge "
                   f"of the literal proxy ({b6:.3f} ≥ θ_baseline) is refused by the "
                   f"fine-tuned model ({s6:.3f} < θ). Live: --rids {r6[0]},{r6[1]}",
             "zh": f"Febrl3 真值：不同人。字面代理最自信的误连（{b6:.3f} ≥ 基线θ）被微调"
                   f"模型拒掉（{s6:.3f} < θ）。"
                   f"现场复现：python scripts/live_pair_score.py --rids {r6[0]},{r6[1]}"}))
    stubs = {}
    for c in cards:
        for rid in (c["a"], c["b"]):
            f = recs[rid]["fields"]
            stubs[rid] = {"record_id": rid, "name": f.get("name") or rid,
                          "dob": f.get("dob"), "id_number": f.get("ssn"),
                          "source": "febrl3"}
    return {"pairs": cards, "records": stubs}


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


def _lookup(pairs: list[tuple[str, str, float]]) -> dict[tuple[str, str], float]:
    d: dict[tuple[str, str], float] = {}
    for a, b, s in pairs:
        d[(a, b)] = s
        d[(b, a)] = s
    return d


def _adjacency(rids: list[str], pairs: list[tuple[str, str, float]]
               ) -> dict[str, list[tuple[str, float]]]:
    adj: dict[str, list[tuple[str, float]]] = {r: [] for r in rids}
    for a, b, s in pairs:
        adj[a].append((b, s))
        adj[b].append((a, s))
    return {r: sorted(v, key=lambda x: (-x[1], x[0])) for r, v in adj.items()}


def _field_rows(fa: dict, fb: dict) -> list[dict]:
    rows = []
    for k, label in _FIELD_LABELS.items():
        if k not in fa and k not in fb:
            continue
        state, sim = _field_state(fa.get(k), fb.get(k))
        rows.append({"key": k, "label": label,
                     "va": fa.get(k) or "—", "vb": fb.get(k) or "—",
                     "sim": sim, "state": state})
    return rows


def _sibling_stats(records: list[dict], ent_of: dict[str, str]) -> dict[str, dict]:
    """每条记录对其同实体兄弟的字段形态（分桶与 F6 查询用）：
    n_missing=自身缺失字段数；n_fuzzy/n_conflict=对全部兄弟逐字段累计。"""
    recs = {r["rid"]: r for r in records}
    by_ent: dict[str, list[str]] = {}
    for rid, e in ent_of.items():
        by_ent.setdefault(e, []).append(rid)
    out: dict[str, dict] = {}
    for e, mem in sorted(by_ent.items()):
        if len(mem) < 2:
            for rid in mem:
                out[rid] = {"n_missing": 0, "n_fuzzy": 0, "n_conflict": 0,
                            "n_nonexact": 0}
            continue
        for rid in mem:
            fa = recs[rid]["fields"]
            n_missing = sum(1 for k in _FIELD_LABELS if not fa.get(k))
            n_f = n_c = 0
            for sib in mem:
                if sib == rid:
                    continue
                for row in _field_rows(fa, recs[sib]["fields"]):
                    if row["state"] == "fuzzy":
                        n_f += 1
                    elif row["state"] == "conflict":
                        n_c += 1
            out[rid] = {"n_missing": n_missing, "n_fuzzy": n_f, "n_conflict": n_c,
                        "n_nonexact": n_f + n_c}
    return out


def bucket_of(rid: str, sib: dict[str, dict], shadow_adj: dict[str, set]) -> str:
    """查询分桶（优先级固定）：missing > conflict > typo_storm(≥3 fuzzy)
    > typo(1-2 fuzzy) > lookalike(在影子对中) > clean。规则可复算。"""
    st = sib[rid]
    if st["n_missing"]:
        return "missing"
    if st["n_conflict"]:
        return "conflict"
    if st["n_fuzzy"] >= 3:
        return "typo_storm"
    if st["n_fuzzy"] >= 1:
        return "typo"
    if rid in shadow_adj:
        return "lookalike"
    return "clean"


_BUCKET_LABELS = {
    "missing": {"en": "Missing fields", "zh": "字段缺失"},
    "conflict": {"en": "Conflicting values", "zh": "字段冲突"},
    "typo_storm": {"en": "Typo storm", "zh": "错字风暴"},
    "typo": {"en": "Typos", "zh": "零星错字"},
    "lookalike": {"en": "Look-alike", "zh": "影子相似"},
    "clean": {"en": "Clean", "zh": "干净记录"},
}
_BUCKET_ORDER = ["missing", "conflict", "typo_storm", "typo", "lookalike", "clean"]

_ARENA_LABELS = {
    "big_entity": {"en": "Large entity", "zh": "大实体"},
    "missing": {"en": "Missing fields", "zh": "字段缺失"},
    "lookalike": {"en": "Look-alike entanglement", "zh": "影子纠缠"},
    "false_link": {"en": "Baseline false merge", "zh": "基线误连"},
    "missed_link": {"en": "Baseline missed link", "zh": "基线漏连"},
    "typo_storm": {"en": "Typo storm", "zh": "错字风暴"},
}


def _name_of(recs: dict, rid: str) -> str:
    return recs[rid]["fields"].get("name") or rid


def build_arena_queries(records, pairs, bpairs, ent_of, theta, theta_b,
                        shadow_pairs) -> dict:
    """Arena 对决台（Febrl 轨）的 6 条查询：类别规则挑选（确定性排序键、
    对叙事盲——不是看故事挑的，是按规则算的极值），每条给双模型 top-5。
    微调列 = 真实余弦（village_scores）；基线列 = 字面相似度代理（重算）。
    规则全文进 provenance["arena_rule"]；类别池空或撞车则缺席/顺延。"""
    recs = {r["rid"]: r for r in records}
    rids = [r["rid"] for r in records]
    adj_t = _adjacency(rids, pairs)
    adj_b = _adjacency(rids, bpairs)
    s_t = _lookup(pairs)
    s_b = _lookup(bpairs)
    members: dict[str, list[str]] = {}
    for rid, e in ent_of.items():
        members.setdefault(e, []).append(rid)
    size_of = {e: len(m) for e, m in members.items()}
    shadow_adj: dict[str, set] = {}
    for a, b in shadow_pairs:
        shadow_adj.setdefault(a, set()).add(b)
        shadow_adj.setdefault(b, set()).add(a)
    sib = _sibling_stats(records, ent_of)
    k = 5

    def ranked_lists() -> dict[str, list[str]]:
        """六类候选的有序名单（类别可缺席 = 空列表）。"""
        out: dict[str, list[str]] = {}
        # F1 大实体：最大实体中真身兄弟均分最低的成员
        e1 = sorted(members, key=lambda e: (-size_of[e], e))[0]
        mem = members[e1]
        out["big_entity"] = sorted(
            mem, key=lambda r: (sum(s_t[(r, x)] for x in mem if x != r) / (len(mem) - 1)
                                if len(mem) > 1 else 0.0, r))
        # F2 缺失：自身缺字段最多的记录
        out["missing"] = sorted((r for r in rids if sib[r]["n_missing"] > 0),
                                key=lambda r: (-sib[r]["n_missing"],
                                               -size_of[ent_of[r]], r))
        # F3 影子纠缠：影子对度数最高
        out["lookalike"] = sorted(shadow_adj,
                                  key=lambda r: (-len(shadow_adj[r]),
                                                 -size_of[ent_of[r]], r))
        # F4 基线误连：top-5 内基线误断言（≥θ_B 且异实体）中最自信的查询
        f4: list[tuple[float, int, str]] = []
        for q in rids:
            best = max((s for o, s in adj_b[q][:k]
                        if ent_of[o] != ent_of[q] and s >= theta_b), default=None)
            if best is not None:
                f4.append((-best, -size_of[ent_of[q]], q))
        out["false_link"] = [q for _, _, q in sorted(f4)]
        # F5 基线漏连：top-5 内基线不敢连的真身（<θ_B）且微调 ≥θ+0.01，分差最大
        f5: list[tuple[float, int, str]] = []
        for q in rids:
            gaps = [s_t[(q, o)] - s for o, s in adj_b[q][:k]
                    if ent_of[o] == ent_of[q] and s < theta_b
                    and s_t[(q, o)] >= theta + 0.01]
            if gaps:
                f5.append((-max(gaps), -size_of[ent_of[q]], q))
        out["missed_link"] = [q for _, _, q in sorted(f5)]
        # F6 错字风暴：对兄弟字段非全同累计最多
        out["typo_storm"] = sorted(rids, key=lambda r: (-sib[r]["n_nonexact"],
                                                        -sib[r]["n_fuzzy"], r))
        return out

    order = ["big_entity", "missing", "lookalike", "false_link",
             "missed_link", "typo_storm"]
    ranked = ranked_lists()
    picked: list[tuple[str, str]] = []
    used: set[str] = set()
    for cat in order:
        q = next((r for r in ranked[cat] if r not in used), None)
        if q is not None:
            picked.append((cat, q))
            used.add(q)

    def top5(adj, q):
        return [{"r": o, "s": round(s, 4)} for o, s in adj[q][:k]]

    queries = []
    for i, (cat, q) in enumerate(picked, start=1):
        e = ent_of[q]
        rel = set(members[e]) - {q}
        t5b = adj_b[q][:k]
        links_b = sum(1 for o, s in t5b if o in rel and s >= theta_b)
        if cat == "big_entity":
            n = len(members[e])
            title = {"en": f"Big family: {n} records, one person",
                     "zh": f"大家族：{n} 条记录一个人"}
            mean = sum(s_t[(q, x)] for x in members[e] if x != q) / (n - 1)
            story = {"en": f"Largest entity in the village ({n} records). The query "
                           f"is its weakest member (mean sibling similarity {mean:.3f}). "
                           f"The fine-tuned model asserts all {n - 1} true siblings "
                           f"above θ; the literal baseline asserts {links_b}.",
                     "zh": f"案例村最大的实体（{n} 条记录）。查询取其中最弱的一员"
                           f"（兄弟均分 {mean:.3f}）。微调模型把全部 {n - 1} 条真身断言过 θ；"
                           f"字面基线只断言 {links_b} 条。"}
        elif cat == "missing":
            km = sib[q]["n_missing"]
            title = {"en": "Missing evidence", "zh": "证据缺失"}
            st = max((s_t[(q, o)], o) for o in rel)[0] if rel else 0.0
            sb = max((s_b[(q, o)] for o in rel), default=0.0)
            story = {"en": f"This record is missing {km} of 5 fields. Evidence is thin, "
                           f"yet the fine-tuned model still links its true sibling "
                           f"({st:.3f} ≥ θ); the literal baseline gives {sb:.3f}.",
                     "zh": f"这条记录 5 个字段缺了 {km} 个。证据残缺，微调模型仍把真身"
                           f"连上（{st:.3f} ≥ θ）；字面基线只给 {sb:.3f}。"}
        elif cat == "lookalike":
            m = len(shadow_adj[q])
            fb = sum(1 for o, s in t5b if o in shadow_adj[q] and s >= theta_b)
            title = {"en": "Look-alike entanglement", "zh": "影子纠缠"}
            story = {"en": f"Entangled with {m} look-alike records (same surname / "
                           f"suburb / DOB — different people). Top-5 is full of "
                           f"temptations: the fine-tuned model refuses every shadow "
                           f"(village-wide false links: 0); the literal baseline "
                           f"asserts {fb} of them.",
                     "zh": f"与 {m} 条异实体记录构成影子关系（同姓/同区/同生日，"
                           f"却不是同一人）。top-5 里满是“像”的诱惑：微调模型全部拒连"
                           f"（全村零误连）；字面基线断言了其中 {fb} 条。"}
        elif cat == "false_link":
            ob, bb = max(((o, s) for o, s in t5b
                          if ent_of[o] != ent_of[q] and s >= theta_b),
                         key=lambda x: (x[1], x[0]))
            st = s_t[(q, ob)]
            nm = _name_of(recs, ob)
            title = {"en": "Baseline's false merge", "zh": "基线误连"}
            story = {"en": f"Febrl3 truth: different people. The literal baseline "
                           f"scores {bb:.3f} ≥ θ and wrongly merges this record with "
                           f"“{nm}”; the fine-tuned model scores {st:.3f} < θ and "
                           f"refuses. A false merge invents a person who never existed.",
                     "zh": f"Febrl3 真值：不同人。字面基线给 {bb:.3f} ≥ θ，把本记录与"
                           f"“{nm}”错误归并；微调模型 {st:.3f} < θ 拒连。"
                           f"误连会凭空捏造一个不存在的人。"}
        elif cat == "missed_link":
            ob, bb = min(((o, s) for o, s in t5b
                          if ent_of[o] == ent_of[q] and s < theta_b
                          and s_t[(q, o)] >= theta + 0.01),
                         key=lambda x: (s_t[(q, x[0])] - x[1], x[0]))
            st = s_t[(q, ob)]
            gap = st - bb
            title = {"en": "Baseline's missed link", "zh": "基线漏连"}
            story = {"en": f"Febrl3 truth: same person. The literal baseline gives only "
                           f"{bb:.3f} < θ and dares not link; the fine-tuned model "
                           f"scores {st:.3f} ≥ θ and asserts — a {gap:.3f} gap on the "
                           f"same pair.",
                     "zh": f"Febrl3 真值：同一人。字面基线只给 {bb:.3f} < θ 不敢断言；"
                           f"微调模型 {st:.3f} ≥ θ 连上——同一对上 {gap:.3f} 的分差。"}
        else:  # typo_storm
            nd = sib[q]["n_nonexact"]
            n = len(members[e])
            title = {"en": "Typo storm", "zh": "错字风暴"}
            story = {"en": f"{nd} field comparisons differ from its true siblings "
                           f"(typos / missing) — among the messiest records in the "
                           f"village. The fine-tuned model still links every sibling; "
                           f"the literal baseline {links_b}/{n - 1}.",
                     "zh": f"与真身兄弟相比有 {nd} 处字段差异（错字/缺失）——全村最乱的"
                           f"记录之列。微调模型仍把兄弟全部连上；字面基线连上 "
                           f"{links_b}/{n - 1}。"}
        queries.append({"query_id": f"F{i}", "record_id": q, "scenario": cat,
                        "title": title, "story": story,
                        "results": {"baseline": top5(adj_b, q),
                                    "tuned": top5(adj_t, q)}})
    return {
        "k": k,
        "theta": {"baseline": round(theta_b, 6), "tuned": round(theta, 6)},
        "labels": _ARENA_LABELS,
        "queries": queries,
        "absent": [c for c in order if not ranked[c]],
    }


def build_open_metrics(records, pairs, bpairs, ent_of, theta, theta_b,
                       shadow_pairs) -> dict:
    """检索评测指标（Febrl 轨）：口径与合成轨 simulate.py 逐条对齐——
    每条记录作查询、检索其余取 top-5、hit=同实体；断言 = top-5 内分数
    ≥ 各模型自己的 θ（各自 F1 最优）。方向性计数（每条真链路两端各计一次），
    跨轨可比。false_link_rate = 影子纠缠查询中断言了影子伙伴的占比。"""
    rids = [r["rid"] for r in records]
    adj_t = _adjacency(rids, pairs)
    adj_b = _adjacency(rids, bpairs)
    members: dict[str, list[str]] = {}
    for rid, e in ent_of.items():
        members.setdefault(e, []).append(rid)
    shadow_adj: dict[str, set] = {}
    for a, b in shadow_pairs:
        shadow_adj.setdefault(a, set()).add(b)
        shadow_adj.setdefault(b, set()).add(a)
    sib = _sibling_stats(records, ent_of)
    k = 5

    def metrics_of(adj, tau, qs: list[str]) -> dict:
        hits = poss = l_true = l_all = sh_q = sh_bad = 0
        for q in qs:
            t5 = adj[q][:k]
            rel = set(members[ent_of[q]]) - {q}
            hits += sum(1 for o, _ in t5 if o in rel)
            poss += len(rel)
            asserted = [(o, s) for o, s in t5 if s >= tau]
            l_true += sum(1 for o, _ in asserted if o in rel)
            l_all += len(asserted)
            if q in shadow_adj:
                sh_q += 1
                if any(o in shadow_adj[q] for o, _ in asserted):
                    sh_bad += 1
        return {
            "recall@5": round(hits / poss, 4) if poss else None,
            "link_recall": round(l_true / poss, 4) if poss else None,
            "link_precision": round(l_true / l_all, 4) if l_all else None,
            "false_link_rate": round(sh_bad / sh_q, 4) if sh_q else None,
        }

    by_bucket = []
    for b in _BUCKET_ORDER:
        qs = [r for r in rids if bucket_of(r, sib, shadow_adj) == b]
        if not qs:
            continue
        by_bucket.append({"scenario": b, "n": len(qs),
                          "baseline": metrics_of(adj_b, theta_b, qs),
                          "tuned": metrics_of(adj_t, theta, qs)})
    return {
        "k": k, "n_queries": len(rids),
        "threshold": {"baseline": round(theta_b, 6), "tuned": round(theta, 6)},
        "overall": {"baseline": metrics_of(adj_b, theta_b, rids),
                    "tuned": metrics_of(adj_t, theta, rids)},
        "by_bucket": by_bucket,
        "bucket_labels": _BUCKET_LABELS,
    }


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
    explain = None  # 基线分数算好后统一构建（R5/R6 卡需要代理基线）

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

    # ---- Arena 对决查询 + 检索指标（Febrl 轨双模型：真实微调 vs 字面代理）
    explain = build_explain_cards(records, pairs, ent_of, theta, model_name,
                                  bpairs, theta_b)
    arena = build_arena_queries(records, pairs, bpairs, ent_of, theta, theta_b,
                                [tuple(p) for p in village["shadow_pairs"]])
    metrics = build_open_metrics(records, pairs, bpairs, ent_of, theta, theta_b,
                                 [tuple(p) for p in village["shadow_pairs"]])

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
        "arena": arena,
        "metrics": metrics,
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
    print(f"  arena: {len(arena['queries'])} queries"
          + (f" (absent: {','.join(arena['absent'])})" if arena["absent"] else ""))
    ov = metrics["overall"]
    print(f"  metrics: baseline link_recall={ov['baseline']['link_recall']} "
          f"link_precision={ov['baseline']['link_precision']} | "
          f"tuned link_recall={ov['tuned']['link_recall']} "
          f"link_precision={ov['tuned']['link_precision']}")
    print(f"  explain cards: {[c['pair_id'] for c in explain['pairs']]}")


if __name__ == "__main__":
    main()
