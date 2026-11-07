"""合成人物记录 + 关系网。

设计原则：
1. **核心剧本人物手工设计**（张伟同名、双胞胎、音译变体、曾用名、DOB 冲突）——
   演示叙事的骨架，保证每个"剧本查询"都有确定的故事走向。
2. **填充人物种子生成**——提供规模感和指标的统计面，全部确定性（seed 钉死）。
3. 所有噪声注入都显式打 noise_tags（对应 Jeff 邮件里的用例场景），这是指标分组的依据。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 常量元数据

SOURCES: list[str] = [
    "household_registry",
    "border_crossing",
    "bank_kyc",
    "telecom",
    "hotel_checkin",
]

SOURCE_META: dict[str, dict[str, str]] = {
    "household_registry": {"code": "HR", "en": "Household Registry", "zh": "户籍系统"},
    "border_crossing": {"code": "BC", "en": "Border Crossing", "zh": "出入境记录"},
    "bank_kyc": {"code": "BK", "en": "Bank KYC", "zh": "银行开户"},
    "telecom": {"code": "TC", "en": "Telecom Operator", "zh": "运营商"},
    "hotel_checkin": {"code": "HT", "en": "Hotel Check-in", "zh": "酒店入住"},
}

# 场景标签 = Jeff 邮件 8 条用例的归并（similar_entity 对应"区分相似实体"，是负向指标）
SCENARIO_META: dict[str, dict[str, str]] = {
    "transliteration": {"en": "Transliteration variant", "zh": "跨文字音译变体"},
    "name_variant": {"en": "Name typo / order / format", "zh": "拼写·顺序·格式变体"},
    "missing_partial": {"en": "Missing attributes", "zh": "属性缺失"},
    "conflicting": {"en": "Conflicting attributes", "zh": "属性冲突"},
    "cross_source": {"en": "Cross-source linking", "zh": "跨源关联"},
    "similar_entity": {"en": "Look-alike entity (must NOT match)", "zh": "相似实体（不应匹配）"},
}

RELATION_META: dict[str, dict[str, str]] = {
    "family": {"en": "Family", "zh": "亲属"},
    "co_address": {"en": "Shared address", "zh": "同址"},
    "co_travel": {"en": "Co-travel", "zh": "同行"},
    "shared_phone": {"en": "Shared phone", "zh": "共用号码"},
}

# 简繁映射（跨境记录的繁体噪声，只覆盖本数据集用到的字）
HAN_TO_TRAD: dict[str, str] = {
    "张": "張", "刘": "劉", "陈": "陳", "杨": "楊", "黄": "黃", "赵": "趙",
    "孙": "孫", "吴": "吳", "强": "強", "杰": "傑", "丽": "麗", "伟": "偉",
}

# 填充人物姓名池 + 手写音译（确定性，不引拼音库）
FILLER_NAMES: list[tuple[str, str, str]] = [
    # (汉字名, 拼音, 粤语式罗马化)
    ("王芳", "Wang Fang", "Wong Fong"),
    ("李娜", "Li Na", "Lee Nah"),
    ("张敏", "Zhang Min", "Cheung Man"),
    ("刘洋", "Liu Yang", "Lau Yeung"),
    ("陈静", "Chen Jing", "Chan Ching"),
    ("杨帆", "Yang Fan", "Yeung Fan"),
    ("赵磊", "Zhao Lei", "Chiu Lui"),
    ("黄敏仪", "Huang Minyi", "Wong Man-yee"),
    ("周杰", "Zhou Jie", "Chow Kit"),
    ("吴倩", "Wu Qian", "Ng Sin"),
    ("徐强", "Xu Qiang", "Tsui Keung"),
    ("孙丽", "Sun Li", "Suen Lai"),
]

FILLER_CITIES: list[tuple[str, str]] = [
    ("湖北省武汉市", "Wuhan"),
    ("江苏省南京市", "Nanjing"),
    ("四川省绵阳市", "Mianyang"),
    ("山东省青岛市", "Qingdao"),
    ("福建省厦门市", "Xiamen"),
    ("湖南省长沙市", "Changsha"),
    ("陕西省西安市", "Xi'an"),
    ("辽宁省大连市", "Dalian"),
    ("云南省昆明市", "Kunming"),
    ("安徽省合肥市", "Hefei"),
    ("江西省南昌市", "Nanchang"),
    ("河北省石家庄市", "Shijiazhuang"),
]

REGION_CODES: dict[str, str] = {
    "北京": "110105", "上海": "310104", "广州": "440106", "成都": "510104",
    "深圳": "440305", "杭州": "330102", "武汉": "420102", "南京": "320102",
    "绵阳": "510703", "青岛": "370203", "厦门": "350206", "长沙": "430102",
    "西安": "610102", "大连": "210203", "昆明": "530102", "合肥": "340102",
    "南昌": "360102", "石家庄": "130102",
}

# 城市 中↔英 锚点表：籍贯跨文字匹配用（含核心人物城市 + 填充人物城市）
CITY_PAIRS: list[tuple[str, str]] = [
    ("北京", "Beijing"), ("上海", "Shanghai"), ("广州", "Guangzhou"), ("成都", "Chengdu"),
    ("深圳", "Shenzhen"), ("杭州", "Hangzhou"),
] + [(zh, en) for zh, en in FILLER_CITIES]


# ---------------------------------------------------------------- 数据结构

@dataclass
class Record:
    record_id: str
    entity_id: str
    name: str
    name_script: str  # "han" | "latin"
    id_number: str | None
    dob: str | None  # YYYY-MM-DD（冲突场景故意含非法日期串，如 1978-02-30）
    pob: str | None  # 籍贯/常住地
    phone: str | None
    source: str
    noise_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "entity_id": self.entity_id,
            "name": self.name,
            "name_script": self.name_script,
            "id_number": self.id_number,
            "dob": self.dob,
            "pob": self.pob,
            "phone": self.phone,
            "source": self.source,
            "source_code": SOURCE_META[self.source]["code"],
            "noise_tags": self.noise_tags,
        }


@dataclass
class Entity:
    entity_id: str
    canonical: str  # 规范汉字名
    canonical_latin: str
    lookalikes: list[str] = field(default_factory=list)  # 相似实体（不应匹配）
    records: list[Record] = field(default_factory=list)


@dataclass
class Relation:
    a: str  # record_id
    b: str
    rel: str  # family / co_address / co_travel / shared_phone
    note: dict[str, str]  # {"en": ..., "zh": ...}


@dataclass
class World:
    entities: list[Entity]
    relations: list[Relation]

    def all_records(self) -> list[Record]:
        return [r for e in self.entities for r in e.records]

    def record_map(self) -> dict[str, Record]:
        return {r.record_id: r for r in self.all_records()}

    def entity_map(self) -> dict[str, Entity]:
        return {e.entity_id: e for e in self.entities}


# ---------------------------------------------------------------- 核心剧本人物

def R(
    rid: str, eid: str, name: str, script: str, source: str,
    id_number: str | None, dob: str | None, pob: str | None, phone: str | None,
    noise: list[str] | None = None,
) -> Record:
    return Record(rid, eid, name, script, id_number, dob, pob, phone, source, list(noise or []))


def _core_cast() -> tuple[list[Entity], list[Relation]]:
    """6 组手工人物：同名异人 / 双胞胎 / 音译 / 曾用名 / DOB 冲突 + 2 个常规。"""

    zhang_bj = Entity(
        "E01", "张伟", "Zhang Wei", lookalikes=["E02"],
        records=[
            R("R101", "E01", "张伟", "han", "household_registry",
              "110105198307140037", "1983-07-14", "北京市朝阳区", None),
            R("R102", "E01", "Zhang Wei", "latin", "bank_kyc",
              "110105198307140031", "1983-07-14", "Beijing", "+86 139 1122 3344",
              ["id_typo", "cross_source"]),
            R("R103", "E01", "ZHANG Wie", "latin", "hotel_checkin",
              None, None, None, "+86 139 1122 3344", ["typo", "missing_partial"]),
            R("R104", "E01", "ZHANG, WEI", "latin", "border_crossing",
              None, "1983-07-14", "Beijing", None, ["name_variant", "cross_source"]),
        ],
    )

    zhang_gz = Entity(
        "E02", "张伟", "Zhang Wei", lookalikes=["E01"],
        records=[
            R("R201", "E02", "张伟", "han", "telecom",
              None, "1979-03-22", "广东省广州市", "+86 137 2211 4455"),
            R("R202", "E02", "张伟", "han", "household_registry",
              "440106197903221238", "1979-03-22", "广州市天河区", None),
        ],
    )

    twin_a = Entity(
        "E03", "李小峰", "Li Xiaofeng", lookalikes=["E04"],
        records=[
            R("R301", "E03", "李小峰", "han", "household_registry",
              "510104199509090131", "1995-09-09", "四川省成都市", None),
            R("R302", "E03", "Xiaofeng Li", "latin", "telecom",
              None, "1995-09-09", "Chengdu", "+86 158 8877 6655", ["name_variant"]),
        ],
    )

    twin_b = Entity(
        "E04", "李小龙", "Li Xiaolong", lookalikes=["E03"],
        records=[
            R("R303", "E04", "李小龙", "han", "household_registry",
              "510104199509090149", "1995-09-09", "四川省成都市", None),
            R("R304", "E04", "Li Xiaolong", "latin", "hotel_checkin",
              None, None, "Chengdu", None, ["missing_partial"]),
        ],
    )

    wang = Entity(
        "E05", "王秀英", "Wang Xiuying",
        records=[
            R("R401", "E05", "王秀英", "han", "household_registry",
              "310104197506128024", "1975-06-12", "上海市静安区", None),
            R("R402", "E05", "Wong Sau Ying", "latin", "border_crossing",
              None, "1975-06-12", "Shanghai", None, ["transliteration"]),
            R("R403", "E05", "Wang Xiu-ying", "latin", "bank_kyc",
              "310104197506128024", "1975-06-12", "Shanghai", "+86 136 5566 7788",
              ["name_variant"]),
            R("R404", "E05", "Xiuying Wang", "latin", "telecom",
              None, None, "Shanghai", "+86 136 5566 7788",
              ["name_variant", "missing_partial"]),
        ],
    )

    victor = Entity(
        "E06", "陈志强", "Victor Chen",
        records=[
            R("R501", "E06", "Victor Chen", "latin", "telecom",
              None, "1988-11-03", "Shenzhen", "+86 150 9988 1122", ["alias"]),
            R("R502", "E06", "陈志强", "han", "bank_kyc",
              "440305198811032016", "1988-11-03", "深圳市", "+86 137 6677 8899", ["cross_source"]),
        ],
    )

    liu = Entity(
        "E07", "刘建华", "Liu Jianhua",
        records=[
            R("R601", "E07", "刘建华", "han", "household_registry",
              "330102197802032516", "1978-02-03", "浙江省杭州市", None),
            R("R602", "E07", "Liu Jianhua", "latin", "bank_kyc",
              "330102197802032516", "1978-02-30", "Hangzhou", "+86 138 6655 4433",
              ["conflicting"]),
            R("R603", "E07", "劉建華", "han", "border_crossing",
              None, "1978-02-03", "Hangzhou", None, ["trad_variant"]),
        ],
    )

    entities = [zhang_bj, zhang_gz, twin_a, twin_b, wang, victor, liu]

    relations = [
        Relation("R103", "R402", "co_travel",
                 {"en": "Same hotel booking, 2019-04", "zh": "同一订单，2019-04"}),
        Relation("R301", "R303", "family",
                 {"en": "Siblings (twin brothers)", "zh": "同胞兄弟（双胞胎）"}),
        Relation("R602", "R502", "shared_phone",
                 {"en": "Same contact number on file", "zh": "预留联系号码相同"}),
        Relation("R201", "R304", "co_address",
                 {"en": "Registered same address, 2021", "zh": "2021 年同址登记"}),
    ]
    return entities, relations


# ---------------------------------------------------------------- 填充人物

def _make_id(rng: random.Random, city_zh: str, dob: str, seq: int) -> str:
    region = REGION_CODES.get(city_zh[:2], "110105")
    body = region + dob.replace("-", "") + f"{seq:03d}"
    check = "0123456789X"[rng.randrange(11)]
    return body + check


def _dob_str(dob: tuple[int, int, int]) -> str:
    y, m, d = dob
    return f"{y:04d}-{m:02d}-{d:02d}"


def _typo_latin(rng: random.Random, name: str) -> str:
    """拉丁名注入一个确定性 typo：交换相邻字母或丢一个字母。"""
    i = rng.randrange(1, max(2, len(name) - 1))
    if rng.random() < 0.5 and i + 1 < len(name):
        return name[:i] + name[i + 1] + name[i] + name[i + 2:]
    return name[:i] + name[i + 1:]


def _fillers(rng: random.Random) -> tuple[list[Entity], list[Relation]]:
    entities: list[Entity] = []
    relations: list[Relation] = []
    for idx, (han, pinyin, cantonese) in enumerate(FILLER_NAMES):
        eid = f"F{idx + 1:02d}"
        # 城市从池子里带放回抽样（制造同城干扰项——ER 评测里 distractor 的真实感）
        city_zh, city_en = FILLER_CITIES[rng.randrange(len(FILLER_CITIES))]
        dob = (rng.randrange(1965, 2001), rng.randrange(1, 13), rng.randrange(1, 29))
        dob_s = _dob_str(dob)
        true_id = _make_id(rng, city_zh, dob_s, rng.randrange(1, 900))
        phone = f"+86 {13 if idx % 2 else 15}{rng.randrange(8)}{rng.randrange(10)} {rng.randrange(1000, 9999)} {rng.randrange(1000, 9999)}"

        # 每人 2-3 条记录；保证至少一条 han 一条 latin（跨文字场景有统计面）
        n_rec = 2 + (1 if idx % 3 == 0 else 0)
        sources = rng.sample(SOURCES, n_rec)
        if "household_registry" not in sources:
            sources[0] = "household_registry"

        records: list[Record] = []
        han_used = False
        latin_used = False
        for j, src in enumerate(sources):
            rid = f"RF{idx + 1:02d}{j + 1}"  # RF 前缀：与核心人物 R1xx-R6xx 永不撞车
            noise: list[str] = []
            use_han = src == "household_registry" or (rng.random() < 0.4 and not han_used)
            if use_han:
                name, script = han, "han"
                han_used = True
                # 跨境记录可能写繁体
                if src == "border_crossing" and rng.random() < 0.6:
                    name = "".join(HAN_TO_TRAD.get(c, c) for c in name)
                    noise.append("trad_variant")
            else:
                base = cantonese if (src == "border_crossing" and rng.random() < 0.5) else pinyin
                if rng.random() < 0.35:  # 顺序颠倒 / 首字母缩写
                    parts = base.split()
                    base = (parts[-1] + " " + parts[0]) if len(parts) == 2 else base
                    noise.append("name_variant")
                if rng.random() < 0.3:
                    base = _typo_latin(rng, base)
                    noise.append("typo")
                name, script = base, "latin"
                latin_used = True
                if src == "border_crossing":
                    noise.append("transliteration")

            id_number: str | None = true_id
            if src in ("hotel_checkin", "border_crossing") or rng.random() < 0.25:
                id_number = None
                noise.append("missing_partial")
            elif rng.random() < 0.15:
                pos = rng.randrange(len(true_id))
                id_number = true_id[:pos] + str(rng.randrange(10)) + true_id[pos + 1:]
                noise.append("id_typo")

            dob_r = dob_s
            if rng.random() < 0.18:
                y, m, d = dob
                dob_r = f"{y + 1:04d}-{m:02d}-{d:02d}"
                noise.append("conflicting")
            elif src == "hotel_checkin" and rng.random() < 0.5:
                dob_r = None
                noise.append("missing_partial")

            pob = city_zh if use_han else city_en
            phone_r = None if (src in ("household_registry", "border_crossing")) else phone

            records.append(Record(rid, eid, name, script, id_number, dob_r, pob, phone_r, src, noise))

        # 兜底：确保跨文字混合（音译场景的统计面）
        if not (han_used and latin_used):
            rec = records[0]
            parts = pinyin.split()
            rec.name, rec.name_script = parts[-1] + " " + parts[0], "latin"
            rec.noise_tags.append("transliteration")

        entities.append(Entity(eid, han, pinyin, records=records))

    # 填充人物之间的关系（供 after 状态网络成形）
    relations.append(Relation("RF011", "RF051", "co_travel",
                              {"en": "Same flight PEK→XIY, 2023-07", "zh": "同航班 PEK→XIY，2023-07"}))
    relations.append(Relation("RF021", "RF111", "co_address",
                              {"en": "Same rental contract, 2022", "zh": "2022 年同一租房合同"}))
    relations.append(Relation("RF091", "RF101", "family",
                              {"en": "Cousins (family registry)", "zh": "堂亲（族谱同载）"}))
    return entities, relations


# ---------------------------------------------------------------- 世界装配

def build_world(seed: int = 42) -> World:
    rng = random.Random(seed)
    core_e, core_r = _core_cast()
    fill_e, fill_r = _fillers(rng)
    entities = core_e + fill_e
    relations = core_r + fill_r
    # 失败不静默：record_id 全局唯一，撞车直接炸
    rids = [r.record_id for e in entities for r in e.records]
    if len(rids) != len(set(rids)):
        dup = sorted({r for r in rids if rids.count(r) > 1})
        raise ValueError(f"record_id collision: {dup}")
    known = {r.record_id for e in entities for r in e.records}
    for rel in relations:
        if rel.a not in known or rel.b not in known:
            raise ValueError(f"relation references unknown record: {rel.a}/{rel.b}")
    # 排序钉死顺序（dict/set 迭代序风险：本项目不用 set，但排序是好习惯）
    entities.sort(key=lambda e: e.entity_id)
    return World(entities=entities, relations=relations)
