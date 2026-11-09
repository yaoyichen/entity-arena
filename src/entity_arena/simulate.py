"""双模型匹配模拟 + 检索评测。

两个模型的语义：
- **baseline**：开箱即用 embedding 的代理 = 字面相似度加权（跨文字名字对它近似瞎）。
- **tuned**：微调后模型 = 字面信号 + "学到的"音译/结构等价（带真实的不完美：零表面
  重合的记录依然接不上，避免演示显得超自然）。

所有分数可被 SCRIPTED_OVERRIDES 精确钉死（剧本需要确定走向），其余由公式涌现。
指标口径（写进演示页脚注）：每条记录作为查询、检索其余记录取 top-5、
hit = ground truth 同实体；相似实体场景报 false-link rate（top-5 混入相似实体的查询占比）。
"""

from __future__ import annotations

import difflib
import random

from .synth import CITY_PAIRS, Record, World

K = 5
THRESHOLD = 0.55  # 判定"同一实体"的分数线（Explain 视图的判决线）

FIELD_WEIGHTS = {"name": 0.42, "id_number": 0.25, "dob": 0.18, "pob": 0.10, "phone": 0.05}


# ---------------------------------------------------------------- 字段相似度

def _norm_latin(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalpha())


def _name_sim(a: Record, b: Record) -> float:
    if a.name == b.name:
        return 1.0
    na, nb = a.name, b.name
    if a.name_script == "latin" and b.name_script == "latin":
        na, nb = _norm_latin(na), _norm_latin(nb)
        return difflib.SequenceMatcher(None, na, nb).ratio()
    # 跨文字（han↔latin）：baseline 的盲区，返回一个低但非零的字面分
    return 0.12


def _dob_sim(a: str | None, b: str | None) -> float | None:
    if a is None or b is None:
        return None
    if a == b:
        return 1.0
    ya, ma, da = a.split("-")
    yb, mb, db = b.split("-")
    if da and db and ma == mb and ya == yb and sorted(da) == sorted(db):
        return 0.6  # 日数字换位（如 02-30 vs 02-03）
    if ya != yb and (ma, da) == (mb, db):
        return 0.2  # 年份差一（录入错）
    return 0.0


def _id_sim(a: str | None, b: str | None) -> float | None:
    if a is None or b is None:
        return None
    if a == b:
        return 1.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return 0.5 if same >= len(a) - 2 else 0.1  # 差 ≤2 位 = OCR 级 typo


def _pob_key(p: str) -> str:
    """籍贯 → 规范城市键（中文/英文都能落到同一个键）。"""
    pl = p.lower()
    for zh, en in CITY_PAIRS:
        if zh in p or en.lower() in pl:
            return en.lower()
    return pl


def _pob_sim(a: str | None, b: str | None) -> float | None:
    if a is None or b is None:
        return None
    if a == b:
        return 1.0
    return 0.7 if _pob_key(a) == _pob_key(b) else 0.0


def _phone_sim(a: str | None, b: str | None) -> float | None:
    if a is None or b is None:
        return None
    na = a.replace(" ", "")
    nb = b.replace(" ", "")
    return 1.0 if na == nb else 0.0


def field_sims(a: Record, b: Record) -> dict[str, float | None]:
    return {
        "name": _name_sim(a, b),
        "id_number": _id_sim(a.id_number, b.id_number),
        "dob": _dob_sim(a.dob, b.dob),
        "pob": _pob_sim(a.pob, b.pob),
        "phone": _phone_sim(a.phone, b.phone),
    }


def field_state(sim: float | None) -> str:
    """Explain 视图的字段态：exact / fuzzy / conflict / missing。"""
    if sim is None:
        return "missing"
    if sim >= 0.999:
        return "exact"
    if sim >= 0.5:
        return "fuzzy"
    return "conflict"


# ---------------------------------------------------------------- 打分

def baseline_score(sims: dict[str, float | None]) -> float:
    num = den = 0.0
    for k, w in FIELD_WEIGHTS.items():
        s = sims[k]
        if s is not None:
            num += w * s
            den += w
    return num / den if den else 0.0


def tuned_score(sims: dict[str, float | None], same_entity: bool, lookalike: bool, rng: random.Random) -> float:
    base = baseline_score(sims)
    if same_entity:
        # 强佐证 = 证件号高度吻合 / 电话相同；中等佐证 = 生日与籍贯完全一致。
        # 微调模型学会的是：音译等价 + typo 容错 + 有佐证时的跨文字推理。
        strong = (sims["id_number"] is not None and sims["id_number"] >= 0.5) or sims["phone"] == 1.0
        medium = sims["dob"] == 1.0 and (sims["pob"] or 0) >= 0.7
        if sims["name"] is not None and sims["name"] < 0.13 and not strong and not medium:
            oracle = 0.50  # 曾用名型：无任何佐证，微调也接不上（诚实的天花板）
        else:
            oracle = 0.90
    elif lookalike:
        oracle = 0.28  # 学会了"像 ≠ 是"
    else:
        oracle = max(0.12, base * 0.55)
    jitter = rng.uniform(-0.02, 0.02)
    return min(1.0, max(0.0, 0.30 * base + 0.70 * oracle + jitter))


# 剧本钉死分数：(rid_a, rid_b) -> {"baseline": x, "tuned": y}
# 让 Explain / Arena 的关键案例有教科书式的走向；公式分作为其余所有对的来源。
SCRIPTED_OVERRIDES: dict[tuple[str, str], dict[str, float]] = {
    # P1 王秀英(han) vs Wong Sau Ying(粤拼)：baseline 看不见，tuned 接上
    ("R401", "R402"): {"baseline": 0.34, "tuned": 0.88},
    # P2 张伟(BJ 户籍) vs 张伟(GZ 运营商)：同名异人，baseline 误连、tuned 分开
    ("R101", "R201"): {"baseline": 0.66, "tuned": 0.31},
    # P4 双胞胎 李小峰 vs 李小龙：baseline 误连、tuned 惊险分开
    ("R301", "R303"): {"baseline": 0.74, "tuned": 0.49},
    # Q6 曾用名 Victor Chen vs 陈志强：生日籍贯全同但表面零重合——两个模型都接不上，
    # tuned 更近但不过线（诚实的天花板，代理评分器无法与真音译区分，剧本钉死）
    ("R501", "R502"): {"baseline": 0.43, "tuned": 0.48},
}


def _pair_scores(a: Record, b: Record, w: World, rng: random.Random) -> tuple[float, float]:
    sims = field_sims(a, b)
    key = (a.record_id, b.record_id)
    rkey = (b.record_id, a.record_id)
    ov = SCRIPTED_OVERRIDES.get(key) or SCRIPTED_OVERRIDES.get(rkey)
    base = baseline_score(sims)
    look = b.entity_id in w.entity_map()[a.entity_id].lookalikes
    same = a.entity_id == b.entity_id
    tuned = tuned_score(sims, same, look, rng)
    if ov:
        return ov["baseline"], ov["tuned"]
    return base, tuned


# ---------------------------------------------------------------- 检索与指标

def pair_matrix(world: World, seed: int = 42) -> dict[str, dict[str, dict[str, float]]]:
    """全量有向成对分数：{model: {qid: {rid: score}}}。

    与 retrieve_all 共用同一条 rng 流（seed+1、同遍历序），因此这里算出的
    分数与 results.json 里 top-5 展示的分数逐对一致——网络视图的断言链、
    Arena 的分数条永远不可能互相矛盾。"""
    rng = random.Random(seed + 1)
    records = world.all_records()
    out: dict[str, dict[str, dict[str, float]]] = {"baseline": {}, "tuned": {}}
    for q in records:
        bl: dict[str, float] = {}
        tu: dict[str, float] = {}
        for c in records:
            if c.record_id == q.record_id:
                continue
            b, t = _pair_scores(q, c, world, rng)
            bl[c.record_id] = b
            tu[c.record_id] = t
        out["baseline"][q.record_id] = bl
        out["tuned"][q.record_id] = tu
    return out


def retrieve_all(world: World, seed: int = 42) -> dict[str, dict[str, list[dict]]]:
    """每条记录作为查询，两个模型各取 top-K。返回 {model: {qid: [{r, s}, ...]}}。"""
    mat = pair_matrix(world, seed)
    out: dict[str, dict[str, list[dict]]] = {"baseline": {}, "tuned": {}}
    for model in ("baseline", "tuned"):
        for q, scored in mat[model].items():
            ranked = sorted(({"r": r, "s": round(s, 4)} for r, s in scored.items()),
                            key=lambda x: (-x["s"], x["r"]))
            out[model][q] = ranked[:K]
    return out


# ---------------------------------------------------------------- 断言链 → 组件（网络三态用）

def undirected_scores(world: World, seed: int = 42) -> dict[str, dict[tuple[str, str], float]]:
    """无向成对分数：{model: {(a, b): s}}（a < b）。

    一对记录两个检索方向各有一个分数（tuned 带独立 jitter），取 max 作为该对
    分数——语义是"较强的那次检索断言"：任一方向把对方捞回并过了 θ 就算连。"""
    mat = pair_matrix(world, seed)
    out: dict[str, dict[tuple[str, str], float]] = {"baseline": {}, "tuned": {}}
    rids = sorted(r.record_id for r in world.all_records())
    for model in ("baseline", "tuned"):
        for i, a in enumerate(rids):
            for b in rids[i + 1:]:
                out[model][(a, b)] = max(mat[model][a][b], mat[model][b][a])
    return out


def link_components(world: World, scores: dict[tuple[str, str], float], theta: float
                    ) -> tuple[list[dict], dict[str, str], list[dict]]:
    """断言链（分数 ≥ θ）→ 并查集组件。返回 (links, comp_of, comp_meta)：
    links = [{a, b, s}]（按 rid 排序）；comp_of = rid → comp_id；
    comp_meta = [{id, size, members, entities, pure}]（与 open_cases 基线块同 shape）。"""
    rids = sorted(r.record_id for r in world.all_records())
    ent_of = {r.record_id: r.entity_id for r in world.all_records()}
    links = [{"a": a, "b": b, "s": round(s, 6)}
             for (a, b), s in sorted(scores.items()) if s >= theta]
    parent = {r: r for r in rids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for l in links:
        ra, rb = find(l["a"]), find(l["b"])
        if ra != rb:
            parent[ra] = rb
    groups: dict[str, list[str]] = {}
    for r in rids:
        groups.setdefault(find(r), []).append(r)
    comp_of: dict[str, str] = {}
    comp_meta: list[dict] = []
    for ci, root in enumerate(sorted(groups)):
        members = sorted(groups[root])
        cid = f"comp-{ci}"
        for m in members:
            comp_of[m] = cid
        ents = sorted({ent_of[m] for m in members})
        comp_meta.append({"id": cid, "size": len(members), "members": members,
                          "entities": ents, "pure": len(ents) == 1})
    return links, comp_of, comp_meta


def link_stats(world: World, links: list[dict], comp_of: dict[str, str],
               comp_meta: list[dict]) -> tuple[dict, list[str], list[str]]:
    """网络三态统计条口径（与 build_open_cases.py 对齐，其中漏连为组件级）：
    断言/误连 = 直接断言对（与图上红线 1:1）；漏连 = 真对落在不同组件
    （与图上红虚线 ⊘ 1:1——直接断言层面漏掉但被链式合并的真对不算漏，
    否则虚线会画在光环内部、与"已归并"的视觉自相矛盾）。"""
    ent_of = {r.record_id: r.entity_id for r in world.all_records()}
    rids = sorted(ent_of)
    truth = {(a, b) for i, a in enumerate(rids) for b in rids[i + 1:]
             if ent_of[a] == ent_of[b]}
    asserted = {(l["a"], l["b"]) for l in links}
    false_links = sorted("|".join(p) for p in asserted if ent_of[p[0]] != ent_of[p[1]])
    missed = sorted("|".join(p) for p in truth if comp_of[p[0]] != comp_of[p[1]])
    tp = len(asserted & truth)
    prec = tp / len(asserted) if asserted else 0.0
    rec = tp / len(truth) if truth else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {
        "asserted": len(asserted),
        "tp": tp,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "truth_pairs": len(truth),
        "false_links": len(false_links),
        "missed_pairs": len(missed),
        "components": len(comp_meta),
        "pure_components": sum(1 for c in comp_meta if c["pure"]),
    }, false_links, missed


def _scenario_of(q: Record, world: World) -> str:
    ent = world.entity_map()[q.entity_id]
    if ent.lookalikes:
        return "similar_entity"
    tags: set[str] = set()
    for r in ent.records:
        if r.record_id != q.record_id:
            tags.update(r.noise_tags)
    tags.update(q.noise_tags)
    for s in ("transliteration", "conflicting", "missing_partial", "name_variant", "typo",
              "id_typo", "trad_variant", "alias"):
        if s in tags:
            return "transliteration" if s in ("transliteration", "trad_variant") else (
                "conflicting" if s == "conflicting" else (
                    "missing_partial" if s == "missing_partial" else "name_variant"))
    return "cross_source"


def compute_metrics(world: World, results: dict) -> dict:
    emap = world.entity_map()
    records = world.all_records()

    def per_model(model: str) -> dict:
        recall_hits = recall_den = 0
        assert_hits = assert_den = 0  # 断言链路（top-K 内 score≥阈值）的命中/总数
        link_hits = 0  # 被断言的真链路数（分子 of link_recall）
        by_sc: dict[str, dict] = {}
        fp_q = fp_den = 0
        for q in records:
            ent = emap[q.entity_id]
            relevant = {r.record_id for r in ent.records} - {q.record_id}
            res = results[model][q.record_id]
            top = [h["r"] for h in res]
            hits = sum(1 for r in top if r in relevant)
            asserted = [h["r"] for h in res if h["s"] >= THRESHOLD]
            a_hits = sum(1 for r in asserted if r in relevant)
            sc = _scenario_of(q, world)
            d = by_sc.setdefault(sc, {"n": 0, "recall_hits": 0, "recall_den": 0,
                                      "assert_hits": 0, "assert_den": 0, "link_hits": 0, "fp": 0})
            d["n"] += 1
            d["recall_hits"] += hits
            d["recall_den"] += len(relevant)
            d["assert_hits"] += a_hits
            d["assert_den"] += len(asserted)
            d["link_hits"] += a_hits
            recall_hits += hits
            recall_den += len(relevant)
            assert_hits += a_hits
            assert_den += len(asserted)
            link_hits += a_hits
            if ent.lookalikes:
                fp_den += 1
                look_set = set()
                for lk in ent.lookalikes:
                    look_set |= {r.record_id for r in emap[lk].records}
                if any(r in look_set for r in asserted):
                    fp_q += 1
                    d["fp"] += 1
        return {
            "recall@5": round(recall_hits / recall_den, 4) if recall_den else 0.0,
            "link_recall": round(link_hits / recall_den, 4) if recall_den else None,
            "link_precision": round(assert_hits / assert_den, 4) if assert_den else None,
            "false_link_rate": round(fp_q / fp_den, 4) if fp_den else 0.0,
            "_by": by_sc,
        }

    bl = per_model("baseline")
    tu = per_model("tuned")
    scenarios = []
    for sc in sorted({**bl["_by"], **tu["_by"]}):
        b, t = bl["_by"].get(sc), tu["_by"].get(sc)

        def agg(d: dict) -> dict:
            return {
                "recall@5": round(d["recall_hits"] / d["recall_den"], 4) if d["recall_den"] else None,
                "link_recall": round(d["link_hits"] / d["recall_den"], 4) if d["recall_den"] else None,
                "link_precision": round(d["assert_hits"] / d["assert_den"], 4) if d["assert_den"] else None,
                "false_link_rate": round(d["fp"] / d["n"], 4) if sc == "similar_entity" else None,
            }

        scenarios.append({
            "scenario": sc,
            "n": (b or t)["n"],
            "baseline": agg(b) if b else {},
            "tuned": agg(t) if t else {},
        })
    return {"k": K, "threshold": THRESHOLD, "overall": {"baseline": {k: v for k, v in bl.items() if k != "_by"},
                                                        "tuned": {k: v for k, v in tu.items() if k != "_by"}},
            "by_scenario": scenarios}
