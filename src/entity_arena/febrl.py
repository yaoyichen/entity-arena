"""Febrl 数据共享工具：解析、序列化、实体切分。

单一事实来源——make_open_dataset.py（训练包）与 pick_open_village.py（案例村）
必须用同一套切分，保证案例村 ⊂ test 实体、零训练泄漏。
"""

from __future__ import annotations

import csv
import random
import re
from collections import defaultdict
from pathlib import Path

SEED = 42
TEST_FRAC = 0.2
REC_RE = re.compile(r"^rec-(\d+)-(org|dup-\d+)$")


def entity_of(rec_id: str) -> str:
    m = REC_RE.match(rec_id)
    if not m:
        raise ValueError(f"无法解析 rec_id：{rec_id}（期望 rec-<n>-org / rec-<n>-dup-<k>）")
    return m.group(1)


def clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def dob_fmt(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip()
    if re.fullmatch(r"\d{8}", v):
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return v


def serialize(r: dict) -> str:
    """记录 → 嵌入文本。只写存在的字段（属性缺失是 ER 常态，不补占位符）。"""
    parts = []
    name = " ".join(x for x in (clean(r.get("given_name")), clean(r.get("surname"))) if x)
    if name:
        parts.append(f"name: {name}")
    dob = dob_fmt(clean(r.get("date_of_birth")))
    if dob:
        parts.append(f"DOB: {dob}")
    addr_bits = []
    for k in ("street_number", "address_1", "address_2"):
        v = clean(r.get(k))
        if v:
            addr_bits.append(v)
    if addr_bits:
        parts.append("address: " + " ".join(addr_bits))
    locality = []
    for k in ("suburb", "postcode", "state"):
        v = clean(r.get(k))
        if v:
            locality.append(v)
    if locality:
        parts.append("locality: " + " ".join(locality))
    ssn = clean(r.get("soc_sec_id"))
    if ssn:
        parts.append(f"SSN: {ssn}")
    return " | ".join(parts)


def load_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # Febrl CSV 表头逗号后带空格（" given_name"），键名统一去空格
        rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    if not rows:
        raise SystemExit(f"空 CSV：{path}")
    need = {"rec_id", "given_name", "surname"}
    missing = need - set(rows[0])
    if missing:
        raise SystemExit(f"缺列 {sorted(missing)}；实际列：{sorted(rows[0])}")
    return rows


def split_entities(rows: list[dict], seed: int = SEED, test_frac: float = TEST_FRAC) -> tuple[set[str], set[str], random.Random]:
    """实体级 80/20 划分（同一实体所有记录同侧）。返回 (test_ents, train_ents, rng)。

    与训练包完全同款：sorted 实体列表 → Random(seed).shuffle → 前 round(n*frac) 为 test。
    rng（已消耗 shuffle 状态）随返回值交还，供调用方延续确定性随机（兜底负例等）。
    """
    ent_of = {r["rec_id"].strip(): entity_of(r["rec_id"].strip()) for r in rows}
    ents = sorted(set(ent_of.values()))
    rng = random.Random(seed)
    rng.shuffle(ents)
    n_test = max(1, round(len(ents) * test_frac))
    return set(ents[:n_test]), set(ents[n_test:]), rng


def records_by_entity(rows: list[dict]) -> dict[str, list[str]]:
    by_ent: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_ent[entity_of(r["rec_id"].strip())].append(r["rec_id"].strip())
    for e in by_ent:
        by_ent[e].sort()
    return dict(by_ent)


def lookalike_key_indexes(rows: list[dict]) -> tuple[dict[str, list[str]], dict[tuple, list[str]], dict[str, list[str]]]:
    """硬负例同款索引：姓 / (suburb,postcode) / 生日 → 记录列表。"""
    surname_idx: dict[str, list[str]] = defaultdict(list)
    loc_idx: dict[tuple, list[str]] = defaultdict(list)
    dob_idx: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        rid = r["rec_id"].strip()
        sn = clean(r.get("surname"))
        if sn:
            surname_idx[sn.lower()].append(rid)
        sub, pc = clean(r.get("suburb")), clean(r.get("postcode"))
        if sub and pc:
            loc_idx[(sub.lower(), pc)].append(rid)
        dob = dob_fmt(clean(r.get("date_of_birth")))
        if dob:
            dob_idx[dob].append(rid)
    return dict(surname_idx), dict(loc_idx), dict(dob_idx)
