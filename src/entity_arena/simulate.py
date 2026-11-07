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

def retrieve_all(world: World, seed: int = 42) -> dict[str, dict[str, list[dict]]]:
    """每条记录作为查询，两个模型各取 top-K。返回 {model: {qid: [{r, s}, ...]}}。"""
    rng = random.Random(seed + 1)
    records = world.all_records()
    out: dict[str, dict[str, list[dict]]] = {"baseline": {}, "tuned": {}}
    for q in records:
        bl: list[dict] = []
        tu: list[dict] = []
        for c in records:
            if c.record_id == q.record_id:
                continue
            b, t = _pair_scores(q, c, world, rng)
            bl.append({"r": c.record_id, "s": round(b, 4)})
            tu.append({"r": c.record_id, "s": round(t, 4)})
        bl.sort(key=lambda x: (-x["s"], x["r"]))
        tu.sort(key=lambda x: (-x["s"], x["r"]))
        out["baseline"][q.record_id] = bl[:K]
        out["tuned"][q.record_id] = tu[:K]
    return out


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
