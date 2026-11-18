#!/usr/bin/env python3
"""租户案例集构建：客户记录 + 字段角色表 + 成对真实分数 → open_cases 形状 payload。

让"客户拿到自己数据的 demo 页"成为一条命令（数据合同 docs/data-contract.md §3.3）：
统计主管线与 Febrl3 实测轨共用 `src/entity_arena/cases_core.py`（θ 扫描、断言链、
组件纯度、方向性指标、五分桶、Arena/Explain 极值规则、试玩台矩阵）——两边数字
同一份口径，永不漂移。本文件只做租户特有的事：intake 复用（校验/序列化/真值）、
字段词典与基线权重从 fieldmap 生成、影子对挖掘、页面文案覆盖（page_labels）。

输入（客户侧三件，合同 §1）：
  --records  客户记录 CSV/JSONL（record_id + 业务字段 [+ entity_id 列=格式 A]）
  --fieldmap 字段角色表 field,role[,normalize][,neg_key][,baseline_weight]
  --scores   成对真实分数（{model,dim,pairs:[[a,b,s],…]}，取样脚本泛化自
             fetch_village_embeddings.py；复算输入，与 --emb 二选一）
  --emb      向量文件（首次取样：本脚本算余弦并把溯源 scores 落到向量旁
             <同名>.scores.json，不占 --scores 输入位）
  --pairs    格式 B 对标签文件（record_id_a,record_id_b,label；与 entity_id 二选一）
可选：--shadow-pairs 显式影子对 / 默认按 neg_key 字段值自动挖掘（桶 ≤ --shadow-max-bucket）；
  --name-field 档案卡显示名字段（默认第一个 embedding 字段）；
  --dataset-label / --truth-label 页面与 provenance 的数据集/真值称谓；
  --demo-dir  一键组装完整演示页数据目录（合成轨 + 开源基准 + 本租户轨）并构建
              demo.html——交付物直接可发。

输出与 data/open_cases.json 完全同 shape（键清单以 build_demo.py:_load_open_cases
为准）→ build_demo --data-dir 即点亮，页面零改动。额外两键：
  page_labels  按 i18n 键覆盖文案（页面 t() 优先查找；不带该键=主构建，零影响）
  play_fields  试玩台字段表的字段名列表（不带=Febrl 默认五字段）

诚实口径（硬约束 §5）：θ 由 link-F1 扫描得出并写进 provenance，永不手调；
基线列=字面相似度代理（等权默认，fieldmap baseline_weight 可覆盖），照旧声明；
文案里的数字全部来自本 payload 实算，不手写。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.cases_core import (  # noqa: E402
    build_cases_payload,
    cosine,
    fmt_w,
    mine_shadow_pairs,
)
from entity_arena.intake import (  # noqa: E402
    IntakeError,
    _to_text,
    apply_normalize,
    load_fieldmap,
    load_pairs,
    load_records,
    serialize_record,
    validate,
)


def _fail(errors: list[str]) -> None:
    for e in errors[:20]:
        print(f"  ✗ {e}", file=sys.stderr)
    if len(errors) > 20:
        print(f"  … 共 {len(errors)} 条", file=sys.stderr)
    raise SystemExit(1)


# ---- 真值/实体（格式 A：entity_id 列；格式 B：label=1 对的连通分量） ----

def _entities_format_b(rids: list[str],
                       labeled: list[tuple[int, str, str, str]]) -> dict[str, str]:
    """格式 B：label=1 对并查集成"实体"（单记录自成实体，实体号=成员最小 rid）。

    labeled 为 load_pairs 的返回（行号, rid_a, rid_b, label 字符串；合法性已由
    validate 保证 ∈ {0,1}）。实体号只用于组件纯度/网络分簇的一致性标记，
    不进任何对外叙事。
    """
    parent = {r: r for r in rids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, a, b, lab in labeled:
        if str(lab).strip() != "1":
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[str, list[str]] = {}
    for r in rids:
        groups.setdefault(find(r), []).append(r)
    ent_of = {}
    for mem in groups.values():
        eid = min(mem)
        for r in mem:
            ent_of[r] = eid
    return ent_of


def _canon(pos: dict[str, int], x: str, y: str) -> tuple[str, str]:
    """按记录文件序取定向（与 Febrl 轨 rids 序同机制：truth/断言集合方向一致）。"""
    return (x, y) if pos[x] < pos[y] else (y, x)


# ---- MCP 交付包（W4）：租户四元组 → 开箱 env + 启动命令 ----

def _write_mcp_bundle(out_dir: Path, *, records: list[dict], pairs: list,
                      shadow: list, emb_fields: list, label: str,
                      truth_label: str, model: str, dim: int, theta: float,
                      cases_name: str, server_src: Path) -> dict:
    """写交付包七件套（确定性，重跑覆盖）：records/scores 由调用方
    已有数据派生成 er_mcp_server 认的形状；er_meta.json 驱动其租户口径
    （标签/字段表/θ——与 cases 产物同源，永不手调）；er_mcp_server.py 单文件
    拷贝（零依赖，可独立运行）；er_mcp.env + START.md 即「开箱 env + 启动命令」。
    令牌绝不写入任何文件（在线模式由操作员注入 EMB_TOKEN）。
    返回 er_meta 内容（供 page_labels 的 cmd/cfg 与测试断言）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    server = (out_dir / "er_mcp_server.py")
    server.write_text(server_src.read_text(encoding="utf-8"), encoding="utf-8")
    (out_dir / "embed_client.py").write_text(
        (ROOT / "src" / "entity_arena" / "embed_client.py").read_text(encoding="utf-8"),
        encoding="utf-8")
    (out_dir / "records.json").write_text(json.dumps({
        "records": records,
        "shadow_pairs": [list(p) for p in shadow],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "scores.json").write_text(json.dumps({
        "model": model, "dim": dim, "n_records": len(records),
        "pairs": [[a, b, round(float(s), 6)] for a, b, s in pairs],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    meta = {
        "label": label, "truth_label": truth_label, "model": model, "dim": dim,
        "theta": theta,
        "theta_src": f"link-F1 optimal on this dataset (build_tenant_cases.py scan; "
                     f"same source as {cases_name})",
        "fields": [[fs.name, fs.label_dict()["en"]] for fs in emb_fields],
        "example_rid": records[0]["rid"],
        "free_text_example": records[0]["text"],
        "data_note": f"customer pilot data ({label}); treat as confidential",
        "docs_ref": "START.md (in this bundle)",
        "n_shadow_pairs": len(shadow),
    }
    (out_dir / "er_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    b = str(out_dir.resolve())
    env = f"""# entity-er MCP 交付包环境变量（build_tenant_cases.py 生成，重跑覆盖）
export ER_VILLAGE="{b}/records.json"
export ER_SCORES="{b}/scores.json"
export ER_META="{b}/er_meta.json"
# 在线自由文本模式（可选）：平台令牌由操作员注入，绝不写进本包
# export EMB_TOKEN=<platform bearer token>
# export ER_EMB_URL=<部署服务地址，默认当前部署>
"""
    (out_dir / "er_mcp.env").write_text(env, encoding="utf-8")
    cfg = json.dumps({"mcpServers": {"entity-er": {
        "command": "python3", "args": [f"{b}/er_mcp_server.py"],
        "env": {"ER_VILLAGE": f"{b}/records.json",
                "ER_SCORES": f"{b}/scores.json",
                "ER_META": f"{b}/er_meta.json"}},
    }}, indent=2)
    start = f"""# entity-er MCP 交付包 · {label}

同一套打分器（模型 `{model}`，θ={theta:.4f}，与演示页/评测报告同源）装进
任意 MCP 客户端。离线 rid 对零配置零网络；在线自由文本需平台令牌。

## 注册（二选一）

**Claude Code（一行命令，env 内联）：**

```bash
claude mcp add entity-er -- env ER_VILLAGE={b}/records.json ER_SCORES={b}/scores.json ER_META={b}/er_meta.json python3 {b}/er_mcp_server.py
```

**Claude Desktop / 任意 MCP 客户端（服务器配置，env 随配置走）：**

```json
{cfg}
```

## 两种打分模式

- **离线 · 数据集 rid 对**：零配置零网络。分数来自预取样真实矩阵
  （{len(records)} 条记录全量 {len(records) * (len(records) - 1) // 2:,} 对，
  部署模型确定性输出，离线 = 在线）。
- **在线 · 自由文本**：`export EMB_TOKEN=<平台令牌>` 后任意两条记录实时打分。
  自由文本按字段序拼 `字段: 值 | …`（示例：`{records[0]['text']}`），
  与训练完全同口径。

## 诚实声明

每个分数都是微调模型的真实输出；判定（LINKED/REFUSED）是模型断言，
与 {truth_label} 核对（客户试点数据，注意保密）。θ = {theta:.4f} 由本数据集
link-F1 扫描选出（与 {cases_name} 同源，永不手调）——换数据先重扫。

## 文件清单

| 文件 | 作用 |
|---|---|
| `er_mcp_server.py` | MCP 服务器（纯标准库 Python ≥3.11） |
| `embed_client.py` | embeddings 客户端共用层（TLS1.2/重试，原样拷贝） |
| `records.json` | 本数据集记录（rid/实体/字段/序列化文本） |
| `scores.json` | 预取样全量成对真实分数（离线矩阵） |
| `er_meta.json` | 租户口径元数据（标签/字段表/θ——服务器启动读取） |
| `er_mcp.env` | 环境变量（source 后直接起服务器） |
| `START.md` | 本文件 |
"""
    (out_dir / "START.md").write_text(start, encoding="utf-8")
    print(f"mcp bundle ready: {out_dir} "
          f"(server + embed_client + records + scores + meta + env + START.md)")
    return meta


# ---- 页面文案覆盖（page_labels）与叙事（narrate_*） ----

def _page_labels(*, label: str, truth_label: str, n: int, n_ents: int,
                 n_shadows: int, theta: float, theta_b: float,
                 n_pairs: int, weights_note: str,
                 tuned_f: int, tuned_m: int, free_ph: str,
                 play_field_names: list[str] | None = None,
                 ex_name: str = "", ex_pair: tuple[str, str] = ("", ""),
                 ex_free: tuple[str, str] = ("", ""),
                 mcp_cmd: str | None = None, mcp_cfg: str | None = None) -> dict:
    """租户页 i18n 覆盖：只覆盖提到 Febrl3/案例村/228 的数据轨文案键。

    数字全部来自实算 payload；θ 值写进 sb.proto.open（主构建里是手写的，
    租户侧改为构建期注入——同一处文案不再有手工数字）。
    W4 追加 connect 视图与 MCP 交付包相关键（conn.* / home.v.connect /
    conn.install.cmd|cfg——后两键仅 --mcp-dir 在场时注入，否则页面走 Febrl 默认）。
    """
    th, thb = f"{theta:.3f}", f"{theta_b:.3f}"
    en_name = label
    out = {
        "net.track.open": {"en": f"{en_name} · real model", "zh": f"{label} · 真实模型"},
        "arena.sub.open": {
            "en": f"{en_name}, {n} real records. Left: literal-similarity baseline proxy. "
                  f"Right: the fine-tuned model's real scores. Same identity should be "
                  f"LINKED; look-alikes must NOT be.",
            "zh": f"{label}，{n} 条真实记录。左栏：字面相似度基线代理；右栏：微调模型"
                  f"真实分数。同一身份的记录应被连接（LINKED）；相似的记录绝不能连上。"},
        "exp.sub.open": {
            "en": f"Field-by-field evidence from {en_name}. Scores are the fine-tuned "
                  f"model's real outputs; θ is this track's F1-optimal threshold. Cards "
                  f"are picked by deterministic extreme-value rules, not by story.",
            "zh": f"{label} 的逐字段证据。分数是微调模型的真实输出，θ 是本轨 F1 最优"
                  f"判定线。案例卡由确定性的极值规则挑出，不是看故事挑的。"},
        "sb.sub.open": {
            "en": f"Every one of the {n} records queries the other {n - 1}; top-5 "
                  f"retrieved; links asserted at each model's own θ. Tuned column: real "
                  f"model scores. Baseline column: literal-similarity proxy (base models "
                  f"cannot be served pairwise) — real baseline metrics below.",
            "zh": f"{n} 条记录两两检索，各取 top-5，按各模型自己的 θ 断言。微调列："
                  f"真实模型分数；基线列：字面相似度代理（基模无法逐对部署）——"
                  f"真实基线指标见下方开源基准板块。"},
        "sb.proto.open": {
            "en": f"Protocol — each of the {n} records queries the other {n - 1}; models "
                  f"return top-5 with scores; a hit = same ground-truth identity; links "
                  f"asserted at each model's own F1-optimal θ (tuned {th}, baseline proxy "
                  f"{thb}). Directional counting, identical to the scripted track. "
                  f"Fine-tuned scores are real model outputs; pairwise baseline is a "
                  f"literal-similarity proxy ({weights_note}) — real base-model numbers "
                  f"are in the open-benchmark board below. Buckets classify each query by "
                  f"its field profile against true siblings (missing > conflict > "
                  f"typo-storm > typo > look-alike).",
            "zh": f"口径——{n} 条记录互为查询，各取 top-5；hit=同真值身份；断言=top-5 内"
                  f"分数 ≥ 各模型自己的 F1 最优 θ（微调 {th}，基线代理 {thb}）。方向性"
                  f"计数，与剧本轨同口径。微调列=真实模型输出；基线列=字面相似度代理"
                  f"（{weights_note}）——真实基模数字见下方开源基准板块。分桶按查询对"
                  f"真身兄弟的字段形态（missing > conflict > 错字风暴 > 零星错字 > "
                  f"影子相似）。"},
        "play.sub": {
            "en": f"Two ways to score a pair. Pick from the dataset: all {n_pairs:,} pairs "
                  f"embedded offline. Or type any two records and score them live.",
            "zh": f"两种出题方式。挑记录对：全部 {n_pairs:,} 对分数已离线内嵌。"
                  f"或直接输入两条记录，在线实时打分。"},
        "play.none": {
            "en": f"{en_name} data is not bundled in this build — the playground needs it. "
                  f"Rebuild after the scores file lands.",
            "zh": f"本构建未打入 {label} 数据——试玩台需要它。分数文件入库后重新构建即可。"},
        "play.truth.same": {"en": f"{truth_label}: same identity", "zh": f"{truth_label}：同一身份"},
        "play.truth.diff": {"en": f"{truth_label}: different people", "zh": f"{truth_label}：不同人"},
        "play.tab.pick": {"en": "Pick records", "zh": "挑记录对"},
        "play.free.sub": {
            "en": "Fill in both records field by field — scored live by the deployed "
                  "model. Serialization follows this dataset's field order, exactly "
                  "as in training.",
            "zh": "逐字段填写两条记录——由部署模型在线打分。序列化按本数据集的"
                  "字段序，与训练完全同口径。"},
        "play.free.ph": {"en": free_ph, "zh": free_ph},
        "play.free.cmd": {
            "en": "python3 scripts/er_gateway.py --registry <tenants.json>",
            "zh": "python3 scripts/er_gateway.py --registry <租户注册表.json>"},
        "play.free.err2": {
            "en": "then open this page from the address it prints (same origin is "
                  "required). Pick-a-pair scoring keeps working offline meanwhile.",
            "zh": "然后从它打印的地址打开本页（必须同源）。挑记录对离线打分不受影响。"},
        "badge.open": {"en": f"{en_name.upper()} · REAL MODEL SCORES",
                       "zh": f"{label} · 真实模型实测"},
        "home.st.village": {"en": f"{en_name} · real scores", "zh": f"{label} · 真实分数"},
        "home.st.village.t": {
            "en": f"records · {{E}} identities · {n_shadows:,} look-alike shadow pairs",
            "zh": f"条记录 · {{E}} 个身份 · {n_shadows:,} 对影子相似"},
        "home.st.tuned.open.t": {
            "en": f"F1 · {{A}}/{{T}} true pairs asserted · {tuned_f} false · {tuned_m} missed",
            "zh": f"F1 · 断言 {{A}}/{{T}} 真对 · {tuned_f} 误连 · {tuned_m} 漏连"},
        "home.step0.chip.open": {
            "en": f"{en_name} · {{N}} rec · {{E}} id",
            "zh": f"{label} · {{N}} 条记录 · {{E}} 个身份"},
        "home.tr.open.t": {
            "en": f"Real records from the client intake pipeline — serialization, "
                  f"splits and live scoring all share one source of truth. Fine-tuned "
                  f"scores are the deployed model's real outputs; the pairwise baseline "
                  f"is a literal-similarity proxy — real base-model metrics sit in the "
                  f"Scoreboard.",
            "zh": f"{label}：客户数据入口的真实记录——序列化、切分与在线打分同一份口径。"
                  f"微调分数=部署模型真实输出；逐对基线=字面相似度代理——真实基模指标"
                  f"见记分牌。"},
        "home.step2.t.open": {
            "en": f"The fine-tuned model asserts with real scores — on this dataset the "
                  f"components are pure and look-alikes are refused.",
            "zh": f"微调模型用真实分数断言——本数据集上组件纯净，相似记录被拒连。"},
        "home.tr.open.h": {"en": f"{en_name} · measured track", "zh": f"{label} · 实测轨"},
        "stat.rec.open": {"en": f"{{N}} rec / {{E}} ent · {en_name}",
                          "zh": f"{{N}} 条记录 / {{E}} 个身份 · {label}"},
        "home.v.play": {
            "en": f"All {n_pairs:,} pairs embedded offline. Pick two, guess first, then "
                  f"reveal the real score and verdict.",
            "zh": f"全部 {n_pairs:,} 对分数离线内嵌。挑两条，先猜再揭晓真实分数与判定。"},
        "footer.note.open": {
            "en": f"Open track: {en_name} ({n} records / {n_ents} identities / "
                  f"{n_shadows:,} look-alike pairs), fine-tuned scores = real model "
                  f"outputs, pairwise baseline = literal-similarity proxy.",
            "zh": f"实测轨：{label}（{n} 条记录 / {n_ents} 个身份 / {n_shadows:,} 对影子"
                  f"相似），微调分数=真实模型实测，逐对基线=字面相似度代理。"},
        "net.syn.base.note": {
            "en": f"Scripted track: both scorers are deterministic proxy formulas "
                  f"(seed 42), not real model outputs. Real model measurements are on "
                  f"the {en_name} track.",
            "zh": f"剧本轨：两个打分器都是种子确定性的代理公式（非真实模型输出）。"
                  f"真实模型实测见 {label} 轨。"},
        # ---- W4：connect 视图（MCP 接入）——去掉案例村/仓库措辞，数字实算 ----
        "conn.sub": {
            "en": f"This page tells the story offline; the MCP server ships the same scorer — "
                  f"real model scores, θ verdict, field-by-field evidence — into any MCP "
                  f"client (Claude Code, Claude Desktop, any agent). Pure standard library, "
                  f"zero dependencies; {en_name} rid pairs score offline with zero config.",
            "zh": f"本页离线讲故事；MCP 服务器把同一个打分器——真实模型分数、θ 判定、"
                  f"逐字段证据——装进任意 MCP 客户端（Claude Code、Claude Desktop、任何 "
                  f"agent）。纯标准库零依赖；{label} rid 对离线打分、零配置。"},
        "conn.s1.t": {
            "en": "One command with the delivery bundle's env — approve once and done.",
            "zh": "带上交付包的环境变量一行命令——点一次允许即可。"},
        "conn.install.repo": {
            "en": "The bundle carries er_mcp_server.py plus this dataset's records / "
                  "scores / meta; paths in the commands above are absolute at delivery "
                  "time.",
            "zh": "交付包里有 er_mcp_server.py 与本数据集的记录/分数/元数据；上面命令中的"
                  "路径为交付时的绝对路径。"},
        "conn.t2.d": {
            "en": f"Search the {n}-record dataset "
                  f"({' / '.join(play_field_names or [])} substring) → rids for scoring.",
            "zh": f"检索 {n} 条数据集记录"
                  f"（{'/'.join(play_field_names or [])}子串）→ 供打分用的 rid。"},
        "conn.ex1": {
            "en": f"Find records named {ex_name} in the dataset, then score the two most "
                  f"alike — same identity?",
            "zh": f"在数据集里找叫 {ex_name} 的记录，挑最像的两条打分：是同一身份吗？"},
        "conn.ex2": {
            "en": f"Score {ex_pair[0]} vs {ex_pair[1]}: cosine, above θ or not, and what "
                  f"does {truth_label} say?",
            "zh": f"给 {ex_pair[0]} 和 {ex_pair[1]} 打分：余弦多少？过没过 θ？"
                  f"{truth_label} 怎么说？"},
        "conn.ex3": {
            "en": f"(live mode, needs EMB_TOKEN) Same identity? A: \"{ex_free[0]}\" "
                  f"B: \"{ex_free[1]}\"",
            "zh": f"（在线模式，需 EMB_TOKEN）是同一身份吗？A：“{ex_free[0]}” "
                  f"B：“{ex_free[1]}”"},
        "conn.mode.off.h": {
            "en": "Offline · dataset rid pairs", "zh": "离线 · 数据集 rid 对"},
        "conn.mode.off.t": {
            "en": f"Zero config. Scores come from the pre-sampled real matrix — all "
                  f"{n_pairs:,} pairs of the {n}-record dataset. The deployed model is "
                  f"deterministic, so offline equals live; and it never breaks on stage.",
            "zh": f"零配置。分数来自预取样真实矩阵——{n} 条记录的全量 {n_pairs:,} 对。"
                  f"部署模型确定性输出，离线=在线；上台绝不掉链子。"},
        "conn.mode.on.t": {
            "en": f"Set the EMB_TOKEN environment variable, then any two free-text records "
                  f"(mixing in dataset rids is fine) are scored live by the deployed "
                  f"fine-tuned model — bring your own data, same θ ruler. Free text is "
                  f"serialized field by field, exactly as in training.",
            "zh": f"设置 EMB_TOKEN 环境变量后，任意两条自由文本记录（混搭数据集 rid 也行）"
                  f"由部署的微调模型实时打分——带自己的数据来，同一把 θ 尺子。自由文本按"
                  f"字段序列化，与训练完全同口径。"},
        "conn.proto": {
            "en": f"Honesty — every score is the fine-tuned model's real output (offline: "
                  f"pre-sampled deterministic matrix; live: deployed service). θ = {{TH}} "
                  f"was picked by link-F1 scan on this dataset; a verdict is the model's "
                  f"assertion, checked against {truth_label} (customer pilot data). "
                  f"Dataset: {n} records / {n_ents} identities / {n_shadows:,} look-alike "
                  f"shadow pairs. Full guide: the delivery bundle's START.md.",
            "zh": f"诚实声明——每个分数都是微调模型的真实输出（离线：预取样确定性矩阵；"
                  f"在线：部署服务）。θ = {{TH}} 由本数据集 link-F1 扫描选出；判定是模型的"
                  f"断言，与 {truth_label} 核对（客户试点数据）。数据集：{n} 条记录 / "
                  f"{n_ents} 个身份 / {n_shadows:,} 对影子相似对。完整指南见交付包 "
                  f"START.md。"},
        "home.v.connect": {
            "en": "Take the scorer into your own AI via MCP — zero config for dataset "
                  "rid pairs.",
            "zh": "通过 MCP 把打分器装进你的 AI——数据集 rid 对零配置。"},
        # 容量实测卡是平台侧口径（bench 数据平台共享）：去掉案例村/仓库脚本措辞
        "conn.bench.sub": {
            "en": "How much traffic can the model service actually take? Load-tested "
                  "{D} against the live /v1/embeddings of the same deployment: "
                  "single-record latency vs. batch throughput, concurrency swept until "
                  "the platform rate limit answered.",
            "zh": "模型服务到底能扛多少流量？{D} 对同一部署的线上 /v1/embeddings 实测"
                  "压测：单条延迟与批量吞吐两个口径，并发一路爬到平台限流开口说话为止。"},
        "conn.bench.note": {
            "en": "Method: pure-stdlib load-test harness (TLS1.2, keep-alive), 15s per "
                  "step. Single-record scoring sends 2 records per request — two orders "
                  "of magnitude below saturation. Red bar = the step where the platform "
                  "answered 429 (GPU queue full); the honest ceiling, not a failure.",
            "zh": "方法：纯标准库压测工具（TLS1.2、连接复用），每档 15 秒。单条打分一次"
                  "只发 2 条记录——离饱和还有两个数量级。红柱 = 平台回 429（GPU 队列满）"
                  "的那一档：这是诚实的上限，不是故障。"},
    }
    if mcp_cmd is not None and mcp_cfg is not None:
        out["conn.install.cmd"] = {"en": mcp_cmd, "zh": mcp_cmd}
        out["conn.install.cfg"] = {"en": mcp_cfg, "zh": mcp_cfg}
    return out


def _narrate_explain(truth_label: str):
    def narrate(pid: str, ctx: dict) -> tuple[dict, dict]:
        a, b, s, th = ctx["a"], ctx["b"], ctx["s"], ctx["theta"]
        mcp = f'MCP: er_score_pair("{a}", "{b}")'
        if pid == "R1":
            nd, nr = ctx["n_diff"], ctx["n_rows"]
            return ({"en": "Real · messy records, same entity", "zh": "实测 · 错字风暴，仍是同一实体"},
                    {"en": f"{truth_label}: same entity. {nd} of {nr} fields differ "
                           f"(typos/missing), yet the fine-tuned model scores {s:.3f} ≥ θ "
                           f"and links them. Reproduce offline: {mcp}",
                     "zh": f"{truth_label}：同一实体。{nr} 个字段里 {nd} 个有差异（错字/缺失），"
                           f"微调模型仍给出 {s:.3f} ≥ θ，断言连接。离线复现：{mcp}"})
        if pid == "R2":
            return ({"en": "Real · look-alikes, two entities", "zh": "实测 · 长得像的两个实体"},
                    {"en": f"{truth_label}: different entities. This is the highest-scoring "
                           f"refused pair ({s:.3f} < θ {th:.3f}) — the strongest look-alike "
                           f"temptation still gets resisted. Reproduce: {mcp}",
                     "zh": f"{truth_label}：不同实体。这是全数据集分数最高的被拒对"
                           f"（{s:.3f} < θ {th:.3f}）——最强的“像”也没能骗过模型。"
                           f"复现：{mcp}"})
        if pid == "R3":
            return ({"en": "Real · closest call that linked", "zh": "实测 · 最惊险的连对"},
                    {"en": f"{truth_label}: same entity. The lowest-scoring correct link "
                           f"({s:.3f}, only {s - th:.3f} above θ) still clears the bar — "
                           f"no true pair sits within the margin. Reproduce: {mcp}",
                     "zh": f"{truth_label}：同一实体。分数最低的正确连对（{s:.3f}，仅超 θ "
                           f"{s - th:.3f}）仍稳过线——边距之内没有任何真对。复现：{mcp}"})
        if pid == "R4":
            ne = ctx["n_exact"]
            return ({"en": "Real · identical fields, refused", "zh": "实测 · 字段全同，仍拒连"},
                    {"en": f"{truth_label}: different entities. {ne} fields are exactly equal, "
                           f"the literal world would merge them; the fine-tuned model refuses "
                           f"({s:.3f} < θ). Reproduce: {mcp}",
                     "zh": f"{truth_label}：不同实体。{ne} 个字段逐字相同，字面世界早该并成"
                           f"一个实体；微调模型仍拒连（{s:.3f} < θ）。复现：{mcp}"})
        bs = ctx["b_score"]
        if pid == "R5":
            return ({"en": "Real · baseline flinched, tuned linked", "zh": "实测 · 基线不敢连，微调连上"},
                    {"en": f"{truth_label}: same entity. Literal-similarity proxy gives "
                           f"{bs:.3f} < θ_baseline and flinches; the fine-tuned model scores "
                           f"{s:.3f} ≥ θ and asserts (gap {s - bs:.3f}). Reproduce: {mcp}",
                     "zh": f"{truth_label}：同一实体。字面相似度代理只给 {bs:.3f} < 基线θ，"
                           f"不敢断言；微调模型 {s:.3f} ≥ θ 连上（分差 {s - bs:.3f}）。"
                           f"复现：{mcp}"})
        return ({"en": "Real · baseline merged, tuned refused", "zh": "实测 · 基线误连，微调拒连"},
                {"en": f"{truth_label}: different entities. The most confident false merge of "
                       f"the literal proxy ({bs:.3f} ≥ θ_baseline) is refused by the "
                       f"fine-tuned model ({s:.3f} < θ). Reproduce: {mcp}",
                 "zh": f"{truth_label}：不同实体。字面代理最自信的误连（{bs:.3f} ≥ 基线θ）"
                       f"被微调模型拒掉（{s:.3f} < θ）。复现：{mcp}"})
    return narrate


def _narrate_arena(truth_label: str):
    def narrate(cat: str, ctx: dict) -> tuple[dict, dict]:
        if cat == "big_entity":
            n, mean, links_b = ctx["n"], ctx["mean"], ctx["links_b"]
            return ({"en": f"Large entity: {n} records, one identity",
                     "zh": f"大实体：{n} 条记录一个身份"},
                    {"en": f"Largest entity in the dataset ({n} records). The query is its "
                           f"weakest member (mean sibling similarity {mean:.3f}). The "
                           f"fine-tuned model asserts all {n - 1} true siblings above θ; "
                           f"the literal baseline asserts {links_b}.",
                     "zh": f"数据集最大的实体（{n} 条记录）。查询取其中最弱的一员（兄弟均分 "
                           f"{mean:.3f}）。微调模型把全部 {n - 1} 条真身断言过 θ；字面基线"
                           f"只断言 {links_b} 条。"})
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
                    {"en": f"Entangled with {m} look-alike records (shared key values — "
                           f"different identities). Top-5 is full of temptations; the "
                           f"fine-tuned model refuses every shadow; the literal baseline "
                           f"asserts {fb} of them.",
                     "zh": f"与 {m} 条异实体记录构成影子关系（共享关键属性值，却不是同一"
                           f"身份）。top-5 里满是“像”的诱惑：微调模型全部拒连；字面基线"
                           f"断言了其中 {fb} 条。"})
        if cat == "false_link":
            bb, st, nm = ctx["bb"], ctx["st"], ctx["nm"]
            return ({"en": "Baseline's false merge", "zh": "基线误连"},
                    {"en": f"{truth_label}: different entities. The literal baseline scores "
                           f"{bb:.3f} ≥ θ and wrongly merges this record with “{nm}”; the "
                           f"fine-tuned model scores {st:.3f} < θ and refuses. A false merge "
                           f"invents an identity that never existed.",
                     "zh": f"{truth_label}：不同实体。字面基线给 {bb:.3f} ≥ θ，把本记录与"
                           f"“{nm}”错误归并；微调模型 {st:.3f} < θ 拒连。误连会凭空捏造一个"
                           f"不存在的身份。"})
        if cat == "missed_link":
            bb, st, gap = ctx["bb"], ctx["st"], ctx["gap"]
            return ({"en": "Baseline's missed link", "zh": "基线漏连"},
                    {"en": f"{truth_label}: same identity. The literal baseline gives only "
                           f"{bb:.3f} < θ and dares not link; the fine-tuned model scores "
                           f"{st:.3f} ≥ θ and asserts — a {gap:.3f} gap on the same pair.",
                     "zh": f"{truth_label}：同一身份。字面基线只给 {bb:.3f} < θ 不敢断言；"
                           f"微调模型 {st:.3f} ≥ θ 连上——同一对上 {gap:.3f} 的分差。"})
        nd, n, links_b = ctx["nd"], ctx["n"], ctx["links_b"]
        return ({"en": "Typo storm", "zh": "错字风暴"},
                {"en": f"{nd} field comparisons differ from its true siblings (typos / "
                       f"missing) — among the messiest records in the dataset. The "
                       f"fine-tuned model still links every sibling; the literal baseline "
                       f"{links_b}/{n - 1}.",
                 "zh": f"与真身兄弟相比有 {nd} 处字段差异（错字/缺失）——数据集最乱的记录"
                       f"之列。微调模型仍把兄弟全部连上；字面基线连上 {links_b}/{n - 1}。"})
    return narrate


# ---- 主流程 ----

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--records", type=Path, required=True, help="客户记录 CSV/JSONL")
    ap.add_argument("--fieldmap", type=Path, required=True, help="字段角色表")
    ap.add_argument("--scores", type=Path, default=None,
                    help="成对真实分数 {model,dim,pairs}（复算输入；与 --emb 二选一）")
    ap.add_argument("--emb", type=Path, default=None,
                    help="向量文件 {model,dim,vectors}（首次取样：算余弦并把溯源 "
                         "scores 落到向量旁 <同名>.scores.json）")
    ap.add_argument("--pairs", type=Path, default=None, help="格式 B 对标签文件")
    ap.add_argument("--shadow-pairs", type=Path, default=None,
                    help="显式影子对 JSON [[ridA,ridB],…]（默认按 neg_key 自动挖掘）")
    ap.add_argument("--shadow-max-bucket", type=int, default=8,
                    help="自动挖掘时单值桶成员上限（过热的值不画，防毛球）")
    ap.add_argument("--name-field", default=None, help="档案卡显示名字段（默认首个 embedding 字段）")
    ap.add_argument("--dataset-label", default="Client dataset", help="页面/溯源的数据集称谓")
    ap.add_argument("--truth-label", default="Client truth", help="真值称谓（如 'Client labels'）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, required=True, help="输出 cases JSON（open_cases 形状）")
    ap.add_argument("--demo-dir", type=Path, default=None,
                    help="一键组装完整演示页数据目录并构建 demo.html（合成轨+开源基准+本租户轨）")
    ap.add_argument("--mcp-dir", type=Path, default=None,
                    help="MCP 交付包（W4）：er_mcp_server.py + 本数据集 records/scores/"
                         "er_meta + er_mcp.env + START.md——开箱 env + 启动命令；"
                         "与 --demo-dir 同跑时页面 connect 视图命令指向本包")
    args = ap.parse_args()
    # demo-dir 块的子进程以 cwd=仓库根运行：相对路径会被解析到仓库树（静默写错
    # 地方、读错数据），一律先落绝对路径（失败不静默）
    if args.demo_dir is not None:
        args.demo_dir = args.demo_dir.resolve()
    if args.mcp_dir is not None:
        args.mcp_dir = args.mcp_dir.resolve()

    if bool(args.scores) == bool(args.emb):
        raise SystemExit("--scores 与 --emb 恰好给一个（复算走 scores，首次取样走 emb）")

    # ---- intake：字段表 + 记录 + 标签（校验全量收集，失败不静默）
    try:
        fields = load_fieldmap(args.fieldmap)
        cols, rows = load_records(args.records)
        labeled = load_pairs(args.pairs) if args.pairs else None
        validate(rows, fields, pairs=labeled, entity_mode=labeled is None)
    except IntakeError as e:
        print(f"intake 校验失败（{len(e.errors)} 条）：", file=sys.stderr)
        _fail(e.errors)

    rids = [rec["record_id"] for _, rec in rows]
    pos = {r: i for i, r in enumerate(rids)}
    emb_fields = [fs for fs in fields if fs.role == "embedding"]
    if not emb_fields:
        raise SystemExit("fieldmap 没有任何 embedding 字段")

    # ---- 真值（格式 A：entity_id；格式 B：label=1 连通分量）
    if labeled is None:
        ent_of = {rec["record_id"]: str(rec["entity_id"]) for _, rec in rows}
        fmt_truth = "格式 A：entity_id 列（同实体同号）"
    else:
        ent_of = _entities_format_b(rids, labeled)
        fmt_truth = "格式 B：label=1 对标签（正例连通分量为实体）"
    truth = {(a, b) for i, a in enumerate(rids) for b in rids[i + 1:]
             if ent_of[a] == ent_of[b]}
    if not truth:
        raise SystemExit("数据集没有多记录实体/正例对——无法定义真值对")

    # ---- 记录归一（显示字段=embedding 字段，规范化后；缺失存 None 与主轨一致；
    #      序列化文本与训练同口径）
    records = []
    for _, rec in rows:
        rid = rec["record_id"]
        disp = {}
        for fs in emb_fields:
            v = apply_normalize(_to_text(rec.get(fs.name)), fs.normalize)
            disp[fs.name] = v if v else None
        records.append({"rid": rid, "entity": ent_of[rid], "fields": disp,
                        "text": serialize_record(rec, fields), "raw": rec})

    # ---- 成对分数：向量算（写溯源文件）或直接读
    if args.emb is not None:
        emb = json.loads(args.emb.read_text(encoding="utf-8"))
        vecs = emb["vectors"]
        missing = [r for r in rids if r not in vecs]
        if missing:
            raise SystemExit(f"向量缺失 {len(missing)} 条，如 {missing[:3]}")
        pairs: list[tuple[str, str, float]] = []
        for i, a in enumerate(rids):
            for b in rids[i + 1:]:
                pairs.append((a, b, cosine(vecs[a], vecs[b])))
        pairs.sort(key=lambda p: (p[0], p[1]))
        sp = args.emb.with_suffix(".scores.json")   # 溯源落向量旁：--emb 单用不再依赖 --scores
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({
            "model": emb["model"], "dim": emb["dim"], "n_records": len(rids),
            "pairs": [[a, b, round(s, 6)] for a, b, s in pairs],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        model_name, dim = emb["model"], emb["dim"]
    else:
        sc = json.loads(args.scores.read_text(encoding="utf-8"))
        model_name, dim = sc["model"], sc["dim"]
        n_expected = len(rids) * (len(rids) - 1) // 2
        seen: set[tuple[str, str]] = set()
        pairs = []
        for a, b, s in sc["pairs"]:
            for r in (a, b):
                if r not in pos:
                    raise SystemExit(f"分数文件引用了记录文件之外的 rid：{r!r}"
                                     "（记录/分数不同版本？）")
            key = _canon(pos, a, b)
            if key in seen:
                raise SystemExit(f"分数文件重复对：{a} × {b}")
            seen.add(key)
            pairs.append((key[0], key[1], float(s)))
        pairs.sort(key=lambda p: (p[0], p[1]))
        if len(seen) != n_expected:
            raise SystemExit(f"分数文件覆盖不全：{len(seen)} 对 ≠ 全量 {n_expected} 对"
                             "（试玩台矩阵需要全量两两分数——重新取样补齐）")
        if sc.get("n_records") not in (None, len(rids)):
            raise SystemExit(f"分数文件 n_records={sc['n_records']} ≠ 记录数 {len(rids)}")

    # ---- 影子对：显式文件 or 自动挖掘（规则进 provenance）
    if args.shadow_pairs is not None:
        shadow = []
        for p in json.loads(args.shadow_pairs.read_text(encoding="utf-8")):
            a, b = p[0], p[1]
            for r in (a, b):
                if r not in pos:
                    raise SystemExit(f"影子对引用了记录文件之外的 rid：{r!r}")
            shadow.append(_canon(pos, a, b))
        shadow_rule = f"显式影子对文件 {args.shadow_pairs.name}（{len(shadow)} 对）"
    else:
        shadow = mine_shadow_pairs(records, ent_of, fields, args.shadow_max_bucket)
        nk_names = [fs.name for fs in emb_fields if fs.mines] or ["（无 neg_key 字段）"]
        shadow_rule = (f"自动挖掘：在任一 neg_key 字段（{'/'.join(nk_names)}）上共享规范化"
                       f"非空值且异实体的记录对；单值桶成员 > {args.shadow_max_bucket} 的"
                       f"过热值不算（{len(shadow)} 对）——与硬负例挖掘同源规则")

    # ---- 字段词典 / 基线权重 / 显示名 / 存根（全部从 fieldmap 生成）
    fields_spec = {fs.name: fs.label_dict() for fs in emb_fields}
    weights = {fs.name: (fs.baseline_weight if fs.baseline_weight is not None else 1.0)
               for fs in emb_fields}
    custom_w = [fs.name for fs in emb_fields if fs.baseline_weight is not None]
    weights_note = ("等权字面相似度，fieldmap baseline_weight 列可覆盖"
                    if not custom_w else f"权重 {fmt_w(weights)}（fieldmap 声明）")
    name_field = args.name_field or emb_fields[0].name
    if name_field not in fields_spec:
        raise SystemExit(f"--name-field {name_field!r} 不是 embedding 字段"
                         f"（可选：{list(fields_spec)}）")

    def name_of(recs: dict, rid: str) -> str:
        return recs[rid]["fields"].get(name_field) or rid

    def stub_of(rec) -> dict:
        return {"record_id": rec["rid"],
                "name": rec["fields"].get(name_field) or rec["rid"],
                "dob": rec["fields"].get("dob"),
                "source": args.dataset_label}

    provenance = {
        "dataset": args.dataset_label,
        "records_file": args.records.name,
        "scores_file": (args.scores.name if args.scores else f"{args.emb.name}→computed"),
        "theta_rule": "本数据集上 link-F1 最大化（平局：精度优先、再 θ 大者）",
        "missed_rule": "漏连=真对落在不同组件（组件级漏并，与图上红虚线 ⊘ 一一对应）；"
                       "直接断言层面漏掉但被链式合并进同一组件的真对不计——"
                       "对级漏连口径由记分牌 top-5 断言指标覆盖",
        "truth": f"{args.truth_label}（{fmt_truth}）",
        "shadow_rule": shadow_rule,
        "model_role": f"微调后模型 {model_name} 的真实向量余弦；基线无法部署（平台仅支持已训练模型），逐条基线分数不可得",
        "baseline_rule": f"代理基线=经典字面相似度 ER 打分器：字段 Levenshtein 加权"
                         f"（{weights_note}），单侧缺失=0.5 中性；θ 与微调同规则 F1 扫描。"
                         "非基模逐条分数——真实基线检索指标见记分牌（开源基准轨）",
        "arena_rule": "Arena 六查询按类别规则挑选（确定性排序键、对叙事盲、先于任何"
                      "文案）：F1 最大实体中兄弟均分最低成员 / F2 自身缺失字段最多 / "
                      "F3 影子对度数最高 / F4 基线 top-5 内最自信误断言 / F5 基线 "
                      "top-5 内漏断真链中双模型分差最大 / F6 对兄弟字段非全同累计"
                      "最多；依次取用并去重，类别池空则缺席并记于 arena.absent。"
                      "微调列=scores 文件真实余弦；基线列=字面代理（同上）",
        "metrics_rule": "检索评测口径与合成轨 simulate.py 一致：每条记录作查询、"
                        "检索其余取 top-5、hit=同真值身份；断言=top-5 内分数 ≥ 各模型"
                        "自己的 θ（各自由 F1 扫描选出）；方向性计数（每条真链路两端"
                        "各计一次，跨轨可比）；false_link_rate=影子纠缠查询中断言了"
                        "影子伙伴的占比；分桶优先级 missing>conflict>typo_storm(≥3 "
                        "fuzzy)>typo(1-2 fuzzy)>lookalike>clean（对同真值兄弟逐字段"
                        "可复算）；单记录实体查询无真身链路，recall/link 指标记 "
                        "null（页面渲染'—'），不算 0",
        "explain_rule": "实测卡极值规则（对叙事盲）：R1 连对真对中缺失→差异字段最多；"
                        "R2 被拒异实体对中分数最高；R3 连对真对中分数最低（贴线）；"
                        "R4 被拒对中 exact 字段最多；R5 基线漏连真对中双模型分差最大；"
                        "R6 基线误连异实体对中最自信。全部要求微调侧距 θ ≥0.01"
                        "（GPU 抖动 ~0.005 不翻判定）；R5/R6 的基线分数为字面代理，"
                        "只出现在 note 文本",
        "play_rule": "试玩台（页面内 playground）分数 = scores 文件全量两两对按 1e-4 "
                     "网格量化的真实余弦（u16 打包嵌入页面，排序 rid 上三角行主序"
                     "索引）；构建期逐对断言量化判定与全精度判定一致，不一致即 raise。"
                     "页内任意挑对打分与 MCP er_score_pair 离线模式同源同值",
    }

    payload = build_cases_payload(
        records=records, ent_of=ent_of, pairs=pairs, model_name=model_name,
        dim=dim, shadow_pairs=shadow, fields_spec=fields_spec, weights=weights,
        name_of=name_of, stub_of=stub_of, narrate_explain=_narrate_explain(args.truth_label),
        narrate_arena=_narrate_arena(args.truth_label), provenance=provenance,
        seed=args.seed,
    )
    n_ents = len(set(ent_of.values()))
    payload["play_fields"] = [fs.name for fs in emb_fields]

    # ---- 可选：MCP 交付包（先写包——page_labels 的注册命令要用它的绝对路径） ----
    mcp_cmd = mcp_cfg = None
    if args.mcp_dir is not None:
        _write_mcp_bundle(args.mcp_dir, records=records, pairs=pairs, shadow=shadow,
                          emb_fields=emb_fields, label=args.dataset_label,
                          truth_label=args.truth_label, model=model_name, dim=dim,
                          theta=payload["theta"], cases_name=args.out.name,
                          server_src=ROOT / "scripts" / "er_mcp_server.py")
        b = str(args.mcp_dir.resolve())
        mcp_cmd = (f"claude mcp add entity-er -- env ER_VILLAGE={b}/records.json "
                   f"ER_SCORES={b}/scores.json ER_META={b}/er_meta.json "
                   f"python3 {b}/er_mcp_server.py")
        mcp_cfg = json.dumps({"mcpServers": {"entity-er": {
            "command": "python3", "args": [f"{b}/er_mcp_server.py"],
            "env": {"ER_VILLAGE": f"{b}/records.json",
                    "ER_SCORES": f"{b}/scores.json",
                    "ER_META": f"{b}/er_meta.json"}},
        }}, indent=2)

    # connect 视图示例（确定性挑选：首记录显示名 / 首个断言连对 / 首记录错字变体）
    ex_name = str(records[0]["fields"].get(name_field) or rids[0])
    links = [(a, b) for a, b, s in pairs if s >= payload["theta"]]
    ex_pair = links[0] if links else (rids[0], rids[-1])
    _t0 = records[0]["text"]
    _nv = str(records[0]["fields"].get(name_field) or "")
    _typo = (_nv[:-1] + ("x" if (not _nv or _nv[-1] != "x") else "y")) if _nv else _nv
    ex_free = (_t0, _t0.replace(_nv, _typo, 1) if _nv else _t0)

    payload["page_labels"] = _page_labels(
        label=args.dataset_label, truth_label=args.truth_label,
        n=len(rids), n_ents=n_ents, n_shadows=len(shadow),
        theta=payload["theta"], theta_b=payload["baseline"]["theta"],
        n_pairs=len(rids) * (len(rids) - 1) // 2, weights_note=weights_note,
        tuned_f=payload["stats"]["false_links"],
        tuned_m=payload["stats"]["missed_pairs"],
        free_ph=records[0]["text"],
        play_field_names=[fs.name for fs in emb_fields],
        ex_name=ex_name, ex_pair=ex_pair, ex_free=ex_free,
        mcp_cmd=mcp_cmd, mcp_cfg=mcp_cfg,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                        encoding="utf-8")
    stats, stats_b = payload["stats"], payload["baseline"]["stats"]
    print(f"wrote {args.out}")
    print(f"  model={model_name} θ={payload['theta']:.4f} "
          f"records={len(rids)} entities={n_ents}")
    print(f"  truth={stats['truth_pairs']} asserted={stats['asserted']} tp={stats['tp']} "
          f"false={stats['false_links']} missed={stats['missed_pairs']} "
          f"components={stats['components']} (pure={stats['pure_components']})")
    print(f"  baseline(proxy) θ={payload['baseline']['theta']:.4f} "
          f"asserted={stats_b['asserted']} tp={stats_b['tp']} "
          f"false={stats_b['false_links']} missed={stats_b['missed_pairs']}")
    print(f"  shadows={len(shadow)} arena={len(payload['arena']['queries'])} "
          f"explain={[c['pair_id'] for c in payload['explain']['pairs']]}")

    # ---- 可选：一键组装完整演示页（合成轨 + 开源基准 + 本租户轨 → demo.html）
    if args.demo_dir is not None:
        data_dir = args.demo_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        def run(*cmd: str) -> None:
            subprocess.run([sys.executable, *cmd], check=True, cwd=ROOT)
        print("assembling demo data dir (synth + open-benchmark + tenant)…")
        run("scripts/make_mock_data.py", "--seed", "42", "--out", str(data_dir))
        run("scripts/update_open_benchmark.py", "--out", str(data_dir / "open_benchmark.json"))
        if args.out.resolve() != (data_dir / "open_cases.json").resolve():
            (data_dir / "open_cases.json").write_text(
                args.out.read_text(encoding="utf-8"), encoding="utf-8")
        out_html = args.demo_dir / "demo.html"
        run("scripts/build_demo.py", "--data-dir", str(data_dir), "--out", str(out_html))
        # 防静默写错位置：demo.html 必须比刚写入的数据新（秒级时钟粒度容忍）
        if out_html.stat().st_mtime < (data_dir / "open_cases.json").stat().st_mtime - 1:
            raise SystemExit(
                f"demo.html 未随本次数据更新（{out_html}）——构建疑似写错位置，"
                f"请检查 --demo-dir/--out 路径")
        print(f"tenant demo ready: {out_html}")


if __name__ == "__main__":
    main()
