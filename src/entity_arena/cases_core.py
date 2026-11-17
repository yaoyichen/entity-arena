"""案例数据集（成对真实分数 → open_cases 形状的 payload）schema 无关核心。

从 build_open_cases.py 抽出的通用管线：θ 扫描、断言链、组件纯度、
基线字面相似度代理、方向性检索指标、五分桶、Arena/Explain 极值规则挑选、
试玩台矩阵量化。字段词典 / 基线权重 / 展示名 / 卡片与查询文案 / provenance
全部由调用方注入——Febrl 适配器（build_open_cases.py）与租户入口
（build_tenant_cases.py）共用这一份统计口径，两边数字永不漂移。

契约（与 build_demo.py:_load_open_cases 的键清单一致）：
  build_cases_payload 返回的 dict 与 data/open_cases.json 顶层同 shape；
  文案字段（title/story/note/stubs/provenance/page_labels）由注入的
  narrate_*/stub_of/provenance 决定，结构字段（theta/stats/links/…）只依赖
  记录、真值与分数——回归锚（tests/test_tenant_cases.py）据此逐字段比对。
"""

from __future__ import annotations

import math
from typing import Callable

from entity_arena.layout import build_open_network

# 叙事注入点签名（Febrl 适配器 / 租户入口各一份，统计核心对文案盲）：
#   name_of(recs, rid) -> str                     档案卡显示名
#   stub_of(rec) -> dict                          Explain 卡记录存根
#   narrate_explain(pid, ctx) -> (title, note)    R1–R6 卡文案
#   narrate_arena(cat, ctx) -> (title, story)     F1–F6 查询文案
# ctx 为纯数据 dict（已算好的统计量），文案层不得反算统计。
NameOf = Callable[[dict, str], str]
StubOf = Callable[[dict], dict]
NarrateExplain = Callable[[str, dict], tuple[dict, dict]]
NarrateArena = Callable[[str, dict], tuple[dict, dict]]


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


# ---- 字段级证据（Explain 卡 / 分桶 / 基线代理共用） ----

def lev_ratio(a: str, b: str) -> float:
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


def field_state(va: str | None, vb: str | None) -> tuple[str, float | None]:
    """与合成轨 Explain 四态同语义：exact / missing / fuzzy(≈) / conflict(✗)。"""
    if not va or not vb:
        return "missing", None
    if va == vb:
        return "exact", 1.0
    sim = round(lev_ratio(va, vb), 2)
    return ("fuzzy" if sim >= 0.7 else "conflict"), sim


def field_rows(fa: dict, fb: dict, fields_spec: dict[str, dict]) -> list[dict]:
    rows = []
    for k, label in fields_spec.items():
        if k not in fa and k not in fb:
            continue
        state, sim = field_state(fa.get(k), fb.get(k))
        rows.append({"key": k, "label": label,
                     "va": fa.get(k) or "—", "vb": fb.get(k) or "—",
                     "sim": sim, "state": state})
    return rows


def sibling_stats(records: list[dict], ent_of: dict[str, str],
                   fields_spec: dict[str, dict]) -> dict[str, dict]:
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
            n_missing = sum(1 for k in fields_spec if not fa.get(k))
            n_f = n_c = 0
            for sib in mem:
                if sib == rid:
                    continue
                for row in field_rows(fa, recs[sib]["fields"], fields_spec):
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


BUCKET_LABELS = {
    "missing": {"en": "Missing fields", "zh": "字段缺失"},
    "conflict": {"en": "Conflicting values", "zh": "字段冲突"},
    "typo_storm": {"en": "Typo storm", "zh": "错字风暴"},
    "typo": {"en": "Typos", "zh": "零星错字"},
    "lookalike": {"en": "Look-alike", "zh": "影子相似"},
    "clean": {"en": "Clean", "zh": "干净记录"},
}
BUCKET_ORDER = ["missing", "conflict", "typo_storm", "typo", "lookalike", "clean"]

ARENA_LABELS = {
    "big_entity": {"en": "Large entity", "zh": "大实体"},
    "missing": {"en": "Missing fields", "zh": "字段缺失"},
    "lookalike": {"en": "Look-alike entanglement", "zh": "影子纠缠"},
    "false_link": {"en": "Baseline false merge", "zh": "基线误连"},
    "missed_link": {"en": "Baseline missed link", "zh": "基线漏连"},
    "typo_storm": {"en": "Typo storm", "zh": "错字风暴"},
}


# ---- 基线代理：经典字面相似度 ER 打分器（"微调前"的可视化口径） ----

def baseline_score(fa: dict, fb: dict, weights: dict[str, float]) -> float:
    """字段 Levenshtein 相似度加权；单侧缺失 = 0.5（无证据中性）；双侧缺失不参与。

    诚实口径：这是经典规则打分器的代理，不是基模的逐条分数——基模无法在平台
    逐条部署（deploy 仅支持已训练模型），真实基线检索指标见记分牌开源基准轨。
    weights 只需给相对权重（加权平均，绝对尺度无关）。
    """
    num = den = 0.0
    for k, w in weights.items():
        va, vb = fa.get(k), fb.get(k)
        if not va and not vb:
            continue
        den += w
        if not va or not vb:
            num += w * 0.5
        elif va == vb:
            num += w
        else:
            num += w * lev_ratio(va, vb)
    return num / den if den else 0.0


def baseline_pairs(records: list[dict], rids: list[str],
                   weights: dict[str, float]) -> list[tuple[str, str, float]]:
    recs = {r["rid"]: r for r in records}
    out = []
    for i, a in enumerate(rids):
        for b in rids[i + 1:]:
            out.append((a, b, baseline_score(recs[a]["fields"], recs[b]["fields"], weights)))
    out.sort(key=lambda p: (p[0], p[1]))
    return out


def fmt_w(weights: dict[str, float]) -> str:
    return " / ".join(f"{k} {v:.2f}" for k, v in weights.items())


def lookup(pairs: list[tuple[str, str, float]]) -> dict[tuple[str, str], float]:
    d: dict[tuple[str, str], float] = {}
    for a, b, s in pairs:
        d[(a, b)] = s
        d[(b, a)] = s
    return d


def adjacency(rids: list[str], pairs: list[tuple[str, str, float]]
              ) -> dict[str, list[tuple[str, float]]]:
    adj: dict[str, list[tuple[str, float]]] = {r: [] for r in rids}
    for a, b, s in pairs:
        adj[a].append((b, s))
        adj[b].append((a, s))
    return {r: sorted(v, key=lambda x: (-x[1], x[0])) for r, v in adj.items()}


# ---- 试玩台离线矩阵 ----

def build_play_matrix(rids: list[str], pairs: list[tuple[str, str, float]],
                      theta: float) -> dict:
    """试玩台（页面内 playground）离线分数矩阵：全量对 1e-4 量化 → u16 小端 → base64。

    rid 排序后上三角行主序索引（i<j：k = i*(2n-i-1)/2 + j-i-1），前端同公式解码。
    量化后判定（q ≥ θ）必须与全精度判定逐对一致，否则 raise——页面试玩台的
    LINKED/REFUSED 不允许与网络视图断言链出现任何不一致（失败不静默）。
    """
    import base64
    import struct
    order = sorted(rids)
    pos = {r: i for i, r in enumerate(order)}
    n = len(order)
    vals = [-1] * (n * (n - 1) // 2)
    for a, b, s in pairs:
        i, j = sorted((pos[a], pos[b]))
        k = i * (2 * n - i - 1) // 2 + (j - i - 1)
        q = math.floor(s * 10000 + 0.5)   # round-half-up，0≤q≤10000 恒在 u16 内
        if (q / 10000 >= theta) != (s >= theta):
            raise SystemExit(
                f"play matrix: 量化翻转判定 {a} × {b}（s={s:.6f} q={q / 10000:.4f} "
                f"θ={theta:.6f}）——收紧量化网格或全精度入库")
        vals[k] = q
    if -1 in vals:
        raise SystemExit("play matrix: pairs 未覆盖全量两两组合（数据管道损坏）")
    b64 = base64.b64encode(struct.pack(f"<{len(vals)}H", *vals)).decode("ascii")
    lo = (min(vals) // 100) / 100 - 0.01   # 量程下限（刻度条只画有效区间）
    return {"n": n, "order": order, "b64": b64, "lo": round(lo, 2)}


# ---- Explain 实测案例卡（极值规则挑选，分数为真实服务输出） ----

def build_explain_cards(records: list[dict], pairs: list[tuple[str, str, float]],
                        ent_of: dict[str, str], theta: float, model_name: str,
                        bpairs: list[tuple[str, str, float]],
                        theta_b: float, fields_spec: dict[str, dict],
                        stub_of: StubOf, narrate: NarrateExplain) -> dict:
    """六张实测卡，全部极值规则挑选（确定性排序键、对叙事盲），微调侧距
    θ ≥ 0.01（GPU 抖动 ~0.005 不翻判定、条形图可读）：
      R1 = 正确断言的真对中差异字段最多者——"乱成这样仍连对"
      R2 = 被拒的异实体对中分数最高者——"长得最像仍拒连"
      R3 = 正确断言的真对中分数最低者——"最惊险的连对（贴线）"
      R4 = 被拒对中 exact 字段最多者——"字段最多全同仍拒连"
      R5 = 基线漏连真对中双模型分差最大者——"基线不敢连，微调接回"
      R6 = 基线误连异实体对中最自信者——"基线最自信的错，微调拒连"
    R5/R6 的代理基线分数只出现在 note 文本（R 卡不加基线分数行，口径锁定）。
    池空则该卡缺席（规则不凑数）。文案（title/note）由 narrate 注入。"""
    recs = {r["rid"]: r for r in records}
    score_of = {(a, b): s for a, b, s in pairs}
    b_of = lookup(bpairs)

    def rows_of(a: str, b: str) -> list[dict]:
        return field_rows(recs[a]["fields"], recs[b]["fields"], fields_spec)

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

    def card(pid: str, ab: tuple[str, str]) -> dict:
        a, b = ab
        s = score_of[ab]
        linked = s >= theta
        rows = rows_of(a, b)
        ctx = {"a": a, "b": b, "s": s, "b_score": b_of.get(ab), "theta": theta,
               "theta_b": theta_b, "rows": rows, "n_rows": len(rows),
               "n_diff": sum(1 for f in rows if f["state"] != "exact"),
               "n_exact": sum(1 for f in rows if f["state"] == "exact"),
               "n_missing": sum(1 for f in rows if f["state"] == "missing")}
        title, note = narrate(pid, ctx)
        return {
            "pair_id": pid, "a": a, "b": b, "real": True,
            "truth": "match" if ent_of[a] == ent_of[b] else "nonmatch",
            "title": title, "note": note,
            "fields": rows,
            "scores": {"tuned": round(s, 4)},
            "verdicts": {"tuned": "match" if linked else "nonmatch"},
            "theta": round(theta, 4), "model": model_name,
        }

    cards = [card("R1", r1), card("R2", r2), card("R3", r3), card("R4", r4)]
    if missed_rel:
        r5 = sorted(missed_rel, key=lambda x: (-x[1], x[0]))[0][0]
        cards.append(card("R5", r5))
    if false_rel:
        r6 = sorted(false_rel, key=lambda x: (-x[1], x[0]))[0][0]
        cards.append(card("R6", r6))
    stubs = {}
    for c in cards:
        for rid in (c["a"], c["b"]):
            stubs[rid] = stub_of(recs[rid])
    return {"pairs": cards, "records": stubs}


# ---- Arena 对决查询 ----

def build_arena_queries(records, pairs, bpairs, ent_of, theta, theta_b,
                        shadow_pairs, fields_spec: dict[str, dict],
                        name_of: NameOf, narrate: NarrateArena) -> dict:
    """Arena 对决台的 6 条查询：类别规则挑选（确定性排序键、对叙事盲——
    不是看故事挑的，是按规则算的极值），每条给双模型 top-5。
    微调列 = 真实余弦（scores 文件）；基线列 = 字面相似度代理（重算）。
    规则全文进 provenance["arena_rule"]；类别池空或撞车则缺席/顺延。
    文案（title/story）由 narrate 注入，ctx 只带已算好的统计量。"""
    recs = {r["rid"]: r for r in records}
    rids = [r["rid"] for r in records]
    adj_t = adjacency(rids, pairs)
    adj_b = adjacency(rids, bpairs)
    s_t = lookup(pairs)
    s_b = lookup(bpairs)
    members: dict[str, list[str]] = {}
    for rid, e in ent_of.items():
        members.setdefault(e, []).append(rid)
    size_of = {e: len(m) for e, m in members.items()}
    shadow_adj: dict[str, set] = {}
    for a, b in shadow_pairs:
        shadow_adj.setdefault(a, set()).add(b)
        shadow_adj.setdefault(b, set()).add(a)
    sib = sibling_stats(records, ent_of, fields_spec)
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
            mean = sum(s_t[(q, x)] for x in members[e] if x != q) / (n - 1)
            ctx = {"q": q, "n": n, "mean": mean, "links_b": links_b}
        elif cat == "missing":
            st = max((s_t[(q, o)], o) for o in rel)[0] if rel else 0.0
            sb = max((s_b[(q, o)] for o in rel), default=0.0)
            ctx = {"q": q, "km": sib[q]["n_missing"], "st": st, "sb": sb,
                   "n_fields": len(fields_spec)}
        elif cat == "lookalike":
            fb = sum(1 for o, s in t5b if o in shadow_adj[q] and s >= theta_b)
            ctx = {"q": q, "m": len(shadow_adj[q]), "fb": fb,
                   "theta": theta, "theta_b": theta_b}
        elif cat == "false_link":
            ob, bb = max(((o, s) for o, s in t5b
                          if ent_of[o] != ent_of[q] and s >= theta_b),
                         key=lambda x: (x[1], x[0]))
            ctx = {"q": q, "ob": ob, "bb": bb, "st": s_t[(q, ob)],
                   "nm": name_of(recs, ob)}
        elif cat == "missed_link":
            ob, bb = min(((o, s) for o, s in t5b
                          if ent_of[o] == ent_of[q] and s < theta_b
                          and s_t[(q, o)] >= theta + 0.01),
                         key=lambda x: (s_t[(q, x[0])] - x[1], x[0]))
            ctx = {"q": q, "ob": ob, "bb": bb, "st": s_t[(q, ob)],
                   "gap": s_t[(q, ob)] - bb}
        else:  # typo_storm
            ctx = {"q": q, "nd": sib[q]["n_nonexact"], "n": len(members[e]),
                   "links_b": links_b}
        title, story = narrate(cat, ctx)
        queries.append({"query_id": f"F{i}", "record_id": q, "scenario": cat,
                        "title": title, "story": story,
                        "results": {"baseline": top5(adj_b, q),
                                    "tuned": top5(adj_t, q)}})
    return {
        "k": k,
        "theta": {"baseline": round(theta_b, 6), "tuned": round(theta, 6)},
        "labels": ARENA_LABELS,
        "queries": queries,
        "absent": [c for c in order if not ranked[c]],
    }


# ---- 检索评测指标 ----

def build_metrics(records, pairs, bpairs, ent_of, theta, theta_b,
                  shadow_pairs, fields_spec: dict[str, dict]) -> dict:
    """检索评测指标：口径与合成轨 simulate.py 逐条对齐——
    每条记录作查询、检索其余取 top-5、hit=同实体；断言 = top-5 内分数
    ≥ 各模型自己的 θ（各自 F1 最优）。方向性计数（每条真链路两端各计一次），
    跨轨可比。false_link_rate = 影子纠缠查询中断言了影子伙伴的占比。"""
    rids = [r["rid"] for r in records]
    adj_t = adjacency(rids, pairs)
    adj_b = adjacency(rids, bpairs)
    members: dict[str, list[str]] = {}
    for rid, e in ent_of.items():
        members.setdefault(e, []).append(rid)
    shadow_adj: dict[str, set] = {}
    for a, b in shadow_pairs:
        shadow_adj.setdefault(a, set()).add(b)
        shadow_adj.setdefault(b, set()).add(a)
    sib = sibling_stats(records, ent_of, fields_spec)
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
    for b in BUCKET_ORDER:
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
        "bucket_labels": BUCKET_LABELS,
    }


# ---- 影子对（"before" 态的相似纠缠 + lookalike 分桶来源） ----

def mine_shadow_pairs(records: list[dict], ent_of: dict[str, str],
                      fields: list, max_bucket: int = 8) -> list[tuple[str, str]]:
    """自动挖掘影子对：与硬负例同源的"相似纠缠"规则——在任一 neg_key 字段上
    共享规范化非空值、且不同实体的记录对；桶内成员数 > max_bucket 的值不算
    （过热的值（如同一城市）会把图连成毛球，宁可少画）。确定性排序去重。

    与 pick_open_village 的 Febrl 复合键规则不同源（那是选村剧本的一部分，
    规则进 village provenance）；这里是租户默认，规则进租户 provenance。
    """
    from entity_arena.intake import apply_normalize, _to_text
    rid_of_val: dict[tuple[str, str], list[str]] = {}
    for r in records:
        for fs in fields:
            if not fs.mines:
                continue
            v = apply_normalize(_to_text(r["raw"].get(fs.name)), fs.normalize)
            if v:
                rid_of_val.setdefault((fs.name, v), []).append(r["rid"])
    pairs: set[tuple[str, str]] = set()
    for (_, _v), mem in sorted(rid_of_val.items()):
        if len(mem) > max_bucket:
            continue
        for i, a in enumerate(mem):
            for b in mem[i + 1:]:
                if ent_of[a] != ent_of[b]:
                    pairs.add((a, b) if a < b else (b, a))
    return sorted(pairs)


# ---- 全量 payload 组装（键序 = open_cases.json 顶层序，勿改） ----

def build_cases_payload(*, records: list[dict], ent_of: dict[str, str],
                        pairs: list[tuple[str, str, float]], model_name: str,
                        dim: int, shadow_pairs: list[tuple[str, str]],
                        fields_spec: dict[str, dict], weights: dict[str, float],
                        name_of: NameOf, stub_of: StubOf,
                        narrate_explain: NarrateExplain, narrate_arena: NarrateArena,
                        provenance: dict, seed: int = 42) -> dict:
    """统计主管线：θ → 断言链 → 组件 → 网络三态 → 基线代理 → Arena/指标/
    Explain/试玩台 → payload（与 data/open_cases.json 顶层同 shape）。

    records 形如 {"rid","entity","fields","text"}；ent_of / shadow_pairs 与其
    对齐；provenance 由调用方给全（theta_rule/missed_rule/baseline_rule/
    arena_rule/metrics_rule/explain_rule/play_rule 必含，失败不静默由调用方保证）。
    """
    rids = [r["rid"] for r in records]

    # ---- 真值对与 θ
    truth = {(a, b) for i, a in enumerate(rids) for b in rids[i + 1:] if ent_of[a] == ent_of[b]}
    if not truth:
        raise SystemExit("案例集没有多记录实体——无法定义真值对")
    theta, stats = pick_theta(pairs, truth)

    links = [(a, b) for a, b, s in pairs if s >= theta]
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
    # 漏连 = 真对落在不同组件（组件级漏并，与图上红虚线 ⊘ 1:1）。直接断言层面
    # 漏掉、但被链式合并进同一组件的真对不算——虚线画在光环内部会与"已归并"
    # 的视觉自相矛盾；对级漏连口径由记分牌（top-5 断言指标）覆盖。
    comps, comp_of, comp_meta = comp_analysis(rids, links, ent_of)
    missed = sorted("|".join(sorted(p)) for p in truth
                    if comp_of[p[0]] != comp_of[p[1]])

    network = build_open_network(
        records=[{"rid": r["rid"], "entity": r["entity"]} for r in records],
        links=links,
        shadow_pairs=shadow_pairs,
        seed=seed,
    )

    # ---- 基线代理（"微调前"态）：同一案例集、同一 θ 规则，经典字面相似度打分
    bpairs = baseline_pairs(records, rids, weights)
    theta_b, stats_b = pick_theta(bpairs, truth)
    links_b = [(a, b) for a, b, s in bpairs if s >= theta_b]
    bscore_of = {(a, b): s for a, b, s in bpairs}
    false_b = sorted(
        {"|".join(sorted((a, b))) for a, b in links_b if ent_of[a] != ent_of[b]}
    )
    comps_b, comp_of_b, comp_meta_b = comp_analysis(rids, links_b, ent_of)
    missed_b = sorted("|".join(sorted(p)) for p in truth
                      if comp_of_b[p[0]] != comp_of_b[p[1]])
    net_base = build_open_network(
        records=[{"rid": r["rid"], "entity": r["entity"]} for r in records],
        links=links_b,
        shadow_pairs=shadow_pairs,
        seed=seed + 17,   # 与微调布局去相关（两套组件坐标独立成图）
    )
    if len(comps_b) != len(net_base["entities"]):
        raise SystemExit("基线组件数与布局实体数不一致——数据管道损坏")

    # ---- Arena 对决查询 + 检索指标（双模型：真实微调 vs 字面代理）
    explain = build_explain_cards(records, pairs, ent_of, theta, model_name,
                                  bpairs, theta_b, fields_spec, stub_of, narrate_explain)
    arena = build_arena_queries(records, pairs, bpairs, ent_of, theta, theta_b,
                                shadow_pairs, fields_spec, name_of, narrate_arena)
    metrics = build_metrics(records, pairs, bpairs, ent_of, theta, theta_b,
                            shadow_pairs, fields_spec)
    play = build_play_matrix(rids, pairs, theta)

    return {
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
        "play": play,
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
        "provenance": provenance,
    }
