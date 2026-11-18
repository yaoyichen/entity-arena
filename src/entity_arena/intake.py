"""客户数据入口共用层 —— 数据合同（docs/data-contract.md §1–§2）的代码实现。

任意客户 schema 的记录 + 标签（格式 A：entity_id 列 / 格式 B：逐对标签文件）+
字段角色三分（embedding / unique-key / ignore）→ 校验 → 实体级切分 →
序列化 → BEIR 训练包（queries/corpus/qrels + 硬负例）。

**单一事实来源**：make_client_dataset.py（上传包构建）、后续 build_tenant_cases.py
（租户体验页复算）、er_gateway.py（在线打分）共用这一份序列化器与切分算法——
训练 / 离线复算 / 在线打分三处口径永远一致，θ 判定不因序列化漂移而翻车。

设计约定（延续仓库硬约束）：
- 纯标准库；一切随机走 seed（同输入双跑逐字节一致，report 不含墙上时钟）；
- 校验失败不静默：全量收集后一次性带原始行号报告（IntakeError.errors）；
- 缺失是信号：空字段跳过、不补占位符（与 febrl 管线同哲学）；
  仅当一条记录**全部** embedding 字段为空才判非法（序列化文本为空平台必拒）。

与 febrl 管线的关系：entity_split 与 febrl.split_entities 算法逐行同款
（sorted → Random(seed).shuffle → 前 max(1, round(n·frac)) 为 test）——
Febrl3 回归锚（tests/test_intake.py::TestFebrlAnchor）要求两条路径的
qrels 逐字节一致；硬负例规则是泛化版（同任一 neg_key 字段值的异实体，
Febrl 的 suburb+postcode 复合键是它的子集故事），差异在测试中显式断言。
"""

from __future__ import annotations

import csv
import json
import random
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

ROLES = ("embedding", "unique-key", "ignore")
NORMALIZERS = ("lower", "upper", "date8")
_DATE8 = re.compile(r"\d{8}")

# 供 report / W2 provenance 引用的口径全文（改规则必须同步改这里）
SERIALIZATION_RULE = (
    "按 fieldmap 声明顺序拼接 embedding 字段为 'field: value'，' | ' 分隔；"
    "空值跳过不补占位符（缺失是信号）；normalize 规则逐字段适用（strip 恒先做）"
)
NEG_RULE_A = (
    "硬负例 = 与查询记录在任一 neg_key 字段上共享规范化非空值、且不同实体的记录，"
    "每查询至多 {max} 个；不足时用种子随机异实体记录兜底 ≥1"
)
NEG_RULE_B = (
    "硬负例 = 客户标注 label=0 的对（金标负例，双向生效），每查询至多 {max} 个；"
    "不足时用种子随机非正例记录兜底 ≥1（格式 B 无实体号，'异实体'按标注推断）"
)


class IntakeError(ValueError):
    """intake 校验/构建失败。args[0] 为人话错误列表（带原始行号）。"""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class FieldSpec(NamedTuple):
    name: str
    role: str                 # ROLES 之一
    normalize: str | None     # NORMALIZERS 之一或 None（仅 strip）
    neg_key: bool             # 是否参与硬负例挖掘（仅 embedding 角色生效）
    # 基线代理权重（仅 embedding 角色生效；None=等权默认，相对值即可）：
    baseline_weight: float | None = None
    # 展示标签（"en|zh"，无 | 则双语同值；None=用字段名）——只进页面，不进口径：
    label: str | None = None

    @property
    def mines(self) -> bool:
        return self.role == "embedding" and self.neg_key

    def label_dict(self) -> dict:
        """字段显示标签 {"en","zh"}；label 形如 "Name|姓名"，缺省=字段名。"""
        if not self.label:
            return {"en": self.name, "zh": self.name}
        if "|" in self.label:
            en, zh = self.label.split("|", 1)
            return {"en": en or self.name, "zh": zh or en or self.name}
        return {"en": self.label, "zh": self.label}


# ---------------------------------------------------------------- 字段角色表

def _norm_role(r: str) -> str:
    return r.strip().replace("_", "-").lower()


def load_fieldmap(path: Path) -> list[FieldSpec]:
    """字段角色表：CSV 表（合同 §1.3 的 table 形态，零依赖不引 YAML 解析器）
    或 JSON 数组。列：field,role[,normalize][,neg_key][,baseline_weight][,label]；
    role 三选一。baseline_weight 仅 embedding 角色生效（基线代理权重，相对值）；
    label="en|zh" 为展示标签（缺省=字段名），只进页面不进口径。
    """
    if path.suffix == ".json":
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise IntakeError([f"fieldmap JSON 解析失败：{e}"]) from e
        if not isinstance(items, list) or not items:
            raise IntakeError(["fieldmap JSON 必须是非空对象数组"])
        raw = [(
            str(it.get("field", "")).strip(),
            _norm_role(str(it.get("role", ""))),
            (it.get("normalize") or None),
            it.get("neg_key", True),
            it.get("baseline_weight"),
            (str(it["label"]).strip() if it.get("label") else None),
        ) for it in items]
        where = [f"fieldmap[{i}]" for i in range(len(raw))]
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            raise IntakeError([f"空 fieldmap：{path}"])
        hdr = [h.strip().lower() for h in rows[0]]
        for need in ("field", "role"):
            if need not in hdr:
                raise IntakeError([f"fieldmap 表头缺 {need} 列；实际 {hdr}"
                                   "（至少 field,role，可选 normalize,neg_key,baseline_weight）"])
        ci, cr = hdr.index("field"), hdr.index("role")
        cn = hdr.index("normalize") if "normalize" in hdr else None
        ck = hdr.index("neg_key") if "neg_key" in hdr else None
        cw = hdr.index("baseline_weight") if "baseline_weight" in hdr else None
        cl = hdr.index("label") if "label" in hdr else None
        raw, where = [], []
        for ln, r in enumerate(rows[1:], start=2):
            if not any(c.strip() for c in r):
                continue
            def cell(i: int | None) -> str:
                return r[i].strip() if i is not None and i < len(r) else ""
            nk = cell(ck)
            bw = cell(cw)
            try:
                bwv = float(bw) if bw else None
            except ValueError:
                bwv = bw   # 非数字：原样带回，校验环统一报人话
            raw.append((cell(ci), _norm_role(cell(cr)), cell(cn) or None,
                        nk.lower() not in ("0", "false", "no"), bwv,
                        cell(cl) or None))
            where.append(f"fieldmap 第 {ln} 行")

    fields, seen, errs = [], set(), []
    for (name, role, norm, nk, bw, lb), w in zip(raw, where):
        if not name:
            errs.append(f"{w}：field 为空")
            continue
        if name in seen:
            errs.append(f"{w}：字段 {name} 重复声明")
            continue
        seen.add(name)
        if role not in ROLES:
            errs.append(f"{w}：字段 {name} 的 role={role!r} 非法（可选 {ROLES}）")
            continue
        if norm is not None and norm not in NORMALIZERS:
            errs.append(f"{w}：字段 {name} 的 normalize={norm!r} 非法（可选 {NORMALIZERS}）")
            continue
        if bw is not None and (not isinstance(bw, (int, float)) or bw < 0):
            errs.append(f"{w}：字段 {name} 的 baseline_weight={bw!r} 非法（≥0 的数字）")
            continue
        fields.append(FieldSpec(name, role, norm, bool(nk),
                                float(bw) if isinstance(bw, (int, float)) else None,
                                lb))
    if fields and not any(fs.role == "embedding" for fs in fields):
        errs.append("fieldmap 没有任何 embedding 字段——序列化文本会全为空")
    if errs:
        raise IntakeError(errs)
    return fields


# ---------------------------------------------------------------- 记录与标签

def _to_text(v) -> str:
    """字段值 → 文本：None→""，字符串 strip，数字/布尔转字符串。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def apply_normalize(v: str, rule: str | None) -> str:
    """strip 恒先做；date8 与 febrl.dob_fmt 同款（YYYYMMDD → YYYY-MM-DD，其余原样）。"""
    v = v.strip()
    if not v or not rule:
        return v
    if rule == "lower":
        return v.lower()
    if rule == "upper":
        return v.upper()
    if rule == "date8":
        return f"{v[:4]}-{v[4:6]}-{v[6:]}" if _DATE8.fullmatch(v) else v
    raise IntakeError([f"未知 normalize 规则：{rule}（可选 {NORMALIZERS}）"])


def load_records(path: Path) -> tuple[set[str], list[tuple[int, dict]]]:
    """记录文件：CSV（utf-8-sig、键值均 strip）或 JSONL（每行一对象）。

    返回 (列名全集, [(原始行号, 记录字典)])；CSV 行号从 2 起（1 为表头），
    JSONL 从 1 起。不在这里做业务校验——validate 阶段统一收错。
    """
    if path.suffix == ".jsonl":
        out: list[tuple[int, dict]] = []
        cols: set[str] = set()
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise IntakeError([f"记录第 {ln} 行不是合法 JSON：{e}"]) from e
            if not isinstance(obj, dict):
                raise IntakeError([f"记录第 {ln} 行不是 JSON 对象"])
            out.append((ln, obj))
            cols.update(obj.keys())
        return cols, out
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise IntakeError([f"空记录文件：{path}"])
    hdr = [h.strip() for h in rows[0]]
    recs = []
    for ln, r in enumerate(rows[1:], start=2):
        vals = [c.strip() for c in r] + [""] * (len(hdr) - len(r))
        recs.append((ln, dict(zip(hdr, vals[:len(hdr)]))))
    return set(hdr), recs


def load_pairs(path: Path) -> list[tuple[int, str, str, str]]:
    """格式 B 标签文件（CSV）：record_id_a,record_id_b,label（label 原样返回，
    合法性在 validate 阶段统一收错）。行号从 2 起。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise IntakeError([f"空标签文件：{path}"])
    hdr = [h.strip().lower() for h in rows[0]]
    for need in ("record_id_a", "record_id_b", "label"):
        if need not in hdr:
            raise IntakeError([f"标签文件表头缺 {need} 列；实际 {hdr}"
                               "（期望 record_id_a,record_id_b,label）"])
    ca, cb, cl = hdr.index("record_id_a"), hdr.index("record_id_b"), hdr.index("label")
    out = []
    for ln, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        def cell(i: int) -> str:
            return r[i].strip() if i < len(r) else ""
        out.append((ln, cell(ca), cell(cb), cell(cl)))
    return out


# ---------------------------------------------------------------- 校验

def validate(records: list[tuple[int, dict]], fields: list[FieldSpec], *,
             pairs: list[tuple[int, str, str, str]] | None = None,
             entity_mode: bool = True) -> dict:
    """合同 §2.1 全量校验。返回 {rec_map, ent_of|None, pair_canon|None, warnings}；
    任一硬错误 → IntakeError（全量收集，一次报完，行号定位）。
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not records:
        raise IntakeError(["记录文件没有数据行"])

    # record_id：存在、非空、唯一
    seen: dict[str, int] = {}
    rec_map: dict[str, dict] = {}
    for ln, rec in records:
        rid = _to_text(rec.get("record_id"))
        if not rid:
            errors.append(f"记录第 {ln} 行：record_id 为空")
            continue
        if rid in seen:
            errors.append(f"record_id 重复：{rid}（第 {seen[rid]} 行 与 第 {ln} 行）")
            continue
        seen[rid] = ln
        rec_map[rid] = rec
    if errors:
        raise IntakeError(errors)  # 后续校验都以 rec_map 为准，先挡住

    cols = {k for _, rec in records for k in rec}

    # fieldmap ↔ 记录列
    for fs in fields:
        if fs.name in ("record_id", "entity_id"):
            if fs.role == "embedding":
                errors.append(f"fieldmap：{fs.name} 是基础设施列，不允许 embedding"
                              "（请声明 ignore 或 unique-key）")
        elif fs.name not in cols:
            errors.append(f"fieldmap 声明了记录中不存在的字段：{fs.name}")
    undeclared = sorted((cols - {fs.name for fs in fields}) - {"record_id", "entity_id"})
    if undeclared:
        warnings.append(f"记录列未在 fieldmap 声明、按 ignore 处理（不进嵌入）："
                        f"{', '.join(undeclared)}")

    # 序列化非空（全 embedding 字段为空的记录平台必拒）
    emb = [fs for fs in fields if fs.role == "embedding"]
    empty_text = [rid for rid in sorted(rec_map) if not serialize_record(rec_map[rid], fields)]
    if empty_text:
        shown = ", ".join(empty_text[:5]) + ("…" if len(empty_text) > 5 else "")
        errors.append(f"有 {len(empty_text)} 条记录的所有 embedding 字段均为空"
                      f"（序列化文本为空，平台会拒绝）：{shown}")

    ent_of: dict[str, str] | None = None
    pair_canon: dict[tuple[str, str], tuple[int, int]] | None = None  # (a,b)→(label, 首现行号)

    if pairs is None:  # 格式 A
        if not entity_mode:
            raise IntakeError(["内部错误：无标签文件时必须走格式 A"])
        if "entity_id" not in cols:
            errors.append("记录文件缺 entity_id 列（格式 A）；"
                          "或改用格式 B：--pairs 提供逐对标签文件")
        ent_of = {}
        for ln, rec in records:
            rid = _to_text(rec.get("record_id"))
            if rid not in rec_map:
                continue
            eid = _to_text(rec.get("entity_id"))
            if not eid:
                errors.append(f"记录第 {ln} 行（{rid}）：entity_id 为空")
                continue
            ent_of[rid] = eid
        if ent_of and len(set(ent_of.values())) < 2:
            errors.append(f"单实体数据（{len(rec_map)} 条记录全部属于 "
                          f"{next(iter(set(ent_of.values())))!r}）——无法做实体级切分")
    else:  # 格式 B
        if "entity_id" in cols:
            warnings.append("记录文件带 entity_id 列但使用了 --pairs（格式 B）："
                            "以逐对标签为准，entity_id 列被忽略")
        pair_canon = {}
        pos = 0
        for ln, a, b, lab in pairs:
            if a not in rec_map:
                errors.append(f"标签第 {ln} 行：record_id_a={a!r} 不存在于记录文件")
                continue
            if b not in rec_map:
                errors.append(f"标签第 {ln} 行：record_id_b={b!r} 不存在于记录文件")
                continue
            if a == b:
                errors.append(f"标签第 {ln} 行：{a} 与自身配对（label 文件不该有自配对）")
                continue
            if lab not in ("0", "1"):
                errors.append(f"标签第 {ln} 行：label={lab!r} 非法（只接受 0 或 1）")
                continue
            key = (a, b) if a < b else (b, a)
            if key in pair_canon:
                if pair_canon[key][0] != int(lab):
                    errors.append(f"标签第 {ln} 行：对 {key} 与第 {pair_canon[key][1]} 行"
                                  "标签冲突（同为该对但 label 不同）")
                continue
            pair_canon[key] = (int(lab), ln)
            pos += int(lab)
        if pair_canon is not None and pos == 0:
            errors.append("标签文件没有任何 label=1 的正例对——无训练/评测信号")
        elif pair_canon is not None and pos < 2:
            errors.append(f"正例对仅 {pos} 条——切分后 train/test 必有一侧为空")

    if errors:
        raise IntakeError(errors)
    return {"rec_map": rec_map, "ent_of": ent_of, "pair_canon": pair_canon,
            "warnings": warnings}


# ---------------------------------------------------------------- 切分与序列化

def serialize_record(rec: dict, fields: list[FieldSpec]) -> str:
    """记录 → 嵌入文本（口径见 SERIALIZATION_RULE；unique-key/ignore 不进文本）。"""
    parts = []
    for fs in fields:
        if fs.role != "embedding":
            continue
        v = apply_normalize(_to_text(rec.get(fs.name)), fs.normalize)
        if v:
            parts.append(f"{fs.name}: {v}")
    return " | ".join(parts)


def entity_split(ent_ids, seed: int, test_frac: float) -> tuple[set[str], set[str], random.Random]:
    """实体级 80/20（与 febrl.split_entities 逐行同款算法——回归锚依赖此一致性）。

    返回 (test_ents, train_ents, rng)；rng 已消耗 shuffle 状态，供兜底负例延续确定性。
    """
    ents = sorted(set(ent_ids))
    rng = random.Random(seed)
    rng.shuffle(ents)
    n_test = max(1, round(len(ents) * test_frac))
    return set(ents[:n_test]), set(ents[n_test:]), rng


def _norm_value(rec: dict, fs: FieldSpec) -> str:
    return apply_normalize(_to_text(rec.get(fs.name)), fs.normalize)


def mine_negatives(rec_map: dict[str, dict], ent_of: dict[str, str],
                   train_rids: list[str], fields: list[FieldSpec],
                   max_neg: int, rng: random.Random) -> tuple[list[tuple[str, list[str]]], int]:
    """格式 A 硬负例（口径见 NEG_RULE_A）。train_rids 的顺序即输出顺序（实体主序）。"""
    miners = [fs for fs in fields if fs.mines]
    idx: dict[str, dict[str, list[str]]] = {fs.name: defaultdict(list) for fs in miners}
    for rid in sorted(rec_map):
        for fs in miners:
            v = _norm_value(rec_map[rid], fs)
            if v:
                idx[fs.name][v].append(rid)
    all_sorted = sorted(rec_map)
    out: list[tuple[str, list[str]]] = []
    n_fallback = 0
    for rid in train_rids:
        e = ent_of[rid]
        cand: set[str] = set()
        for fs in miners:
            v = _norm_value(rec_map[rid], fs)
            if v:
                cand.update(x for x in idx[fs.name][v] if ent_of[x] != e)
        cand.discard(rid)
        negs = sorted(cand)[:max_neg]
        if not negs:
            while True:
                pick = rng.choice(all_sorted)
                if ent_of[pick] != e:
                    negs = [pick]
                    break
            n_fallback += 1
        out.append((rid, negs))
    return out, n_fallback


# ---------------------------------------------------------------- 训练包

def write_package(out: Path, texts: dict[str, str],
                  qrels_train: list[tuple[str, str]], qrels_test: list[tuple[str, str]],
                  neg_items: list[tuple[str, list[str]]]) -> None:
    """BEIR 包落盘（顺序即调用方给定顺序——格式 A 需与 febrl 管线逐字节一致）。"""
    out.mkdir(parents=True, exist_ok=True)
    (out / "qrels").mkdir(exist_ok=True)
    (out / "neg").mkdir(exist_ok=True)
    with open(out / "queries.jsonl", "w", encoding="utf-8") as fq, \
         open(out / "corpus.jsonl", "w", encoding="utf-8") as fc:
        for rid in sorted(texts):
            obj = {"_id": rid, "text": texts[rid]}
            line = json.dumps(obj, ensure_ascii=False) + "\n"
            fq.write(line)
            fc.write(line)
    for split, pairs in (("train", qrels_train), ("test", qrels_test)):
        with open(out / "qrels" / f"{split}.tsv", "w", encoding="utf-8") as f:
            for q, d in pairs:
                f.write(f"{q}\t{d}\t1\n")
    with open(out / "neg" / "neg.jsonl", "w", encoding="utf-8") as f:
        for qid, negs in neg_items:
            f.write(json.dumps({"qid": qid, "neg": negs}, ensure_ascii=False) + "\n")


def zip_package(out: Path, zip_path: Path | None = None) -> Path:
    """确定性 zip（固定时间戳/属性/排序，双跑逐字节一致），可直接 upload_dataset。

    intake_report.json 不进包——它是内部溯源，平台校验只认四件套。
    """
    zp = zip_path or out.parent / f"{out.name}.zip"
    files = sorted(p for p in out.rglob("*")
                   if p.is_file() and p.name != "intake_report.json")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            info = zipfile.ZipInfo(p.relative_to(out).as_posix(),
                                   date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
    return zp


def run_intake(records_path: Path, fieldmap_path: Path,
               pairs_path: Path | None = None, *, out: Path,
               seed: int = 42, test_frac: float = 0.2, max_neg: int = 5,
               make_zip: bool = False) -> dict:
    """全流程：加载 → 校验 → 切分 → 序列化 → 训练包 + intake_report.json。

    返回 report（dict，同时落盘）；校验/守卫失败 raise IntakeError。
    """
    if not 0.0 < test_frac < 1.0:
        raise IntakeError([f"test_frac 必须在 (0,1)：{test_frac}"])
    if max_neg < 1:
        raise IntakeError([f"max_neg 必须 ≥1：{max_neg}"])

    fields = load_fieldmap(fieldmap_path)
    cols, records = load_records(records_path)
    pairs = load_pairs(pairs_path) if pairs_path else None
    v = validate(records, fields, pairs=pairs, entity_mode=pairs is None)
    rec_map, warnings = v["rec_map"], v["warnings"]

    texts = {rid: serialize_record(rec_map[rid], fields) for rid in rec_map}

    if pairs is None:
        # ---- 格式 A：实体级切分 + 挖掘硬负例（循环结构与 febrl 管线一致，保字节对齐）
        ent_of: dict[str, str] = v["ent_of"]
        by_ent: dict[str, list[str]] = defaultdict(list)
        for rid in rec_map:
            by_ent[ent_of[rid]].append(rid)
        for e in by_ent:
            by_ent[e].sort()
        test_ents, train_ents, rng = entity_split(ent_of.values(), seed, test_frac)
        qrels: dict[str, list[tuple[str, str]]] = {"train": [], "test": []}
        for split, ent_set in (("train", train_ents), ("test", test_ents)):
            for e in sorted(ent_set):
                for rid in by_ent[e]:
                    for other in by_ent[e]:
                        if other != rid:
                            qrels[split].append((rid, other))
        train_rids = [rid for e in sorted(train_ents) for rid in by_ent[e]]
        neg_items, n_fallback = mine_negatives(rec_map, ent_of, train_rids,
                                               fields, max_neg, rng)
        report = {
            "label_format": "A",
            "split": {"method": "entity-level（同一实体全部记录同侧，防泄漏）",
                      "seed": seed, "test_frac": test_frac,
                      "train_entities": len(train_ents), "test_entities": len(test_ents)},
            "negatives": {"rule": NEG_RULE_A.format(max=max_neg),
                          "max_per_query": max_neg,
                          "mined": sum(len(n) for _, n in neg_items),
                          "fallback_queries": n_fallback},
            "entities": len(by_ent),
        }
    else:
        # ---- 格式 B：正例对切分 + 金标负例（label=0）
        canon: dict[tuple[str, str], tuple[int, int]] = v["pair_canon"]
        positives = sorted(k for k, (lab, _) in canon.items() if lab == 1)
        zeros: dict[str, set[str]] = defaultdict(set)
        for (a, b), (lab, _) in canon.items():
            if lab == 0:
                zeros[a].add(b)
                zeros[b].add(a)
        pos_partners: dict[str, set[str]] = defaultdict(set)
        for (a, b), (lab, _) in canon.items():
            if lab == 1:
                pos_partners[a].add(b)
                pos_partners[b].add(a)
        shuffled = list(positives)
        rng = random.Random(seed)
        rng.shuffle(shuffled)
        n_test = min(max(1, round(len(shuffled) * test_frac)), len(shuffled) - 1)
        test_set = set(shuffled[:n_test])
        qrels = {"train": [], "test": []}
        for split, plist in (("train", [p for p in positives if p not in test_set]),
                             ("test", [p for p in positives if p in test_set])):
            for a, b in plist:
                qrels[split].append((a, b))
                qrels[split].append((b, a))
        neg_items, n_fallback = [], 0
        all_sorted = sorted(rec_map)
        for qid in sorted({q for q, _ in qrels["train"]}):
            negs = sorted(zeros.get(qid, set()))[:max_neg]
            if not negs:
                # 死循环防护：正例伙伴覆盖其余全部记录时无第三人可选——人话报错
                if len(pos_partners.get(qid, ())) >= len(all_sorted) - 1:
                    raise IntakeError([
                        f"格式 B 负例回退失败：记录 {qid} 没有 label=0 金标负例，"
                        "而它的正例伙伴又覆盖了其余全部记录（无第三人可作负例）——"
                        "请为其补 label=0 对，或减少正例对"])
                while True:
                    pick = rng.choice(all_sorted)
                    if pick != qid and pick not in pos_partners.get(qid, set()):
                        negs = [pick]
                        break
                n_fallback += 1
            neg_items.append((qid, negs))
        report = {
            "label_format": "B",
            "split": {"method": "by-pair（正例对为单位切分；金标负例全局共享，"
                                 "与语料同池口径一致）",
                      "seed": seed, "test_frac": test_frac,
                      "train_pairs": len(positives) - n_test, "test_pairs": n_test},
            "negatives": {"rule": NEG_RULE_B.format(max=max_neg),
                          "max_per_query": max_neg,
                          "mined": sum(len(n) for _, n in neg_items),
                          "fallback_queries": n_fallback},
            "positive_pairs": len(positives), "zero_pairs": len(canon) - len(positives),
        }

    if not qrels["train"] or not qrels["test"]:
        raise IntakeError([f"切分后 qrels 为空：train={len(qrels['train'])} "
                           f"test={len(qrels['test'])}（数据太小或 test_frac 不当）"])

    write_package(out, texts, qrels["train"], qrels["test"], neg_items)

    report.update({
        "tool": "scripts/make_client_dataset.py",
        "contract": "docs/data-contract.md (v0.1)",
        "serialization": SERIALIZATION_RULE,
        "records": len(rec_map),
        "fields": [{"field": fs.name, "role": fs.role,
                    "normalize": fs.normalize, "neg_key": fs.neg_key} for fs in fields],
        "embedding_fields": [fs.name for fs in fields if fs.role == "embedding"],
        "unique_key_fields": [fs.name for fs in fields if fs.role == "unique-key"],
        "ignored_fields": sorted([fs.name for fs in fields if fs.role == "ignore"]
                                 + [w for w in undeclared_names(fields, rec_map)]),
        "qrels": {"train": len(qrels["train"]), "test": len(qrels["test"])},
        "package": {"queries": len(texts), "corpus": len(texts)},
        "warnings": warnings,
        "source": {"records": records_path.name, "fieldmap": fieldmap_path.name,
                   "pairs": pairs_path.name if pairs_path else None},
    })
    (out / "intake_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if make_zip:
        report["zip"] = str(zip_package(out))
    return report


def undeclared_names(fields: list[FieldSpec], rec_map: dict[str, dict]) -> list[str]:
    """记录里出现但 fieldmap 未声明的业务列（report 里按 ignore 归档）。"""
    declared = {fs.name for fs in fields}
    cols = {k for rec in rec_map.values() for k in rec} - {"record_id", "entity_id"}
    return sorted(cols - declared)
