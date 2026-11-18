#!/usr/bin/env python3
"""entity-er MCP server：案例村打分器 → 任意 MCP 客户端（纯标准库，stdio JSON-RPC）。

三个工具：
  er_score_pair(a, b)  两条记录（案例村 rid 或自由文本）→ 真实余弦 + θ 判定 + 字段对照 + 真值核验
  er_find_records(q)   案例村检索（姓名/生日/SSN/地址子串；给 er_score_pair 挑 rid）
  er_about()           口径、配置与两种模式状态（诚实声明）

两种打分模式：
  离线（零配置，默认可用）：rid 对 → data_open/village_scores.json 预取样真实分数
    （228 条记录两两全量 25,878 对；部署模型输出确定，离线 = 实时）
  在线（自由文本/混搭）：环境变量 EMB_TOKEN → 部署服务 /v1/embeddings 实时打分

注册（Claude Code）：
  claude mcp add entity-er -- python3 scripts/er_mcp_server.py
（仓库根 .mcp.json 已带同款配置，用 Claude Code 打开本项目批准即可。）
详细文档：docs/mcp.md；演示页「接入」视图：build/demo.html?view=connect

环境变量：
  EMB_TOKEN     平台 Bearer 令牌（在线模式必需；离线模式不需要）
  ER_EMB_URL    部署服务地址（默认为当前部署，换服务时覆盖）
  ER_EMB_MODEL  模型名（默认 febrl3-er-gemma；ER_META 在场时默认取 meta.model）
  ER_VILLAGE    案例村数据路径（默认 data_open/village.json）
  ER_SCORES     预取样分数矩阵路径（默认 data_open/village_scores.json）
  ER_THETA      覆盖判定线（默认 ER_META.θ > data/open_cases.json 的 θ，避免两处漂移）
  ER_META       租户元数据 JSON（build_tenant_cases --mcp-dir 产出的 er_meta.json）：
                在场时标签/字段表/自由文本示例/真值称谓全部按租户口径（W4 交付打包），
                缺席时行为与本文件历史版本逐字节一致（Febrl 默认，测试锁定）
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:  # 仓库布局：src/entity_arena/embed_client.py
    from entity_arena.embed_client import (  # noqa: E402
        DEFAULT_MODEL, DEFAULT_URL, EmbedError, cosine, embed, load_theta,
    )
except ImportError:  # 交付包布局（W4）：同目录带 embed_client.py 原样拷贝
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from embed_client import (  # noqa: E402
        DEFAULT_MODEL, DEFAULT_URL, EmbedError, cosine, embed, load_theta,
    )

SERVER_NAME = "entity-er"
SERVER_VERSION = "1.1.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2025-01-28", "2024-11-05")

VILLAGE_PATH = Path(os.environ.get("ER_VILLAGE", ROOT / "data_open" / "village.json"))
SCORES_PATH = Path(os.environ.get("ER_SCORES", ROOT / "data_open" / "village_scores.json"))
META_PATH = Path(os.environ["ER_META"]).resolve() if os.environ.get("ER_META") else None
EMB_URL = os.environ.get("ER_EMB_URL", DEFAULT_URL)
EMB_TOKEN = os.environ.get("EMB_TOKEN")  # 只在进程内使用，绝不回显


def _read_meta() -> dict:
    if META_PATH is None:
        return {}
    try:
        raw = json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"ER_META 指向的租户元数据读不了：{META_PATH}：{e}\n"
                         "  （该文件由 build_tenant_cases.py --mcp-dir 产出）") from None
    if not isinstance(raw, dict):
        raise SystemExit(f"ER_META 必须是 JSON 对象：{META_PATH}")
    return raw


_META: dict | None = None


def _meta() -> dict:
    global _META
    if _META is None:
        _META = _read_meta()
    return _META


def _label_word() -> str:
    """句子里的名词：Febrl 默认 village，租户 dataset（历史输出逐字节保持）。"""
    return "dataset" if _meta() else "village"


def _truth_label(long: bool = False) -> str:
    d = "Febrl3 ground truth" if long else "Febrl3 truth"
    return str(_meta().get("truth_label") or d)


def _data_note(default: str) -> str:
    return str(_meta().get("data_note") or default)


def _theta_src(short: bool = False) -> str:
    """θ 来源短语：score_pair 用全称（"village link-F1 optimal"），about 用短称
    （"link-F1 optimal on this village"）——两处默认值都保持历史字节不变。"""
    return str(_meta().get("theta_src") or
               ("link-F1 optimal" if short else "village link-F1 optimal"))


EMB_MODEL = os.environ.get("ER_EMB_MODEL") or str(_meta().get("model") or DEFAULT_MODEL)

PARSE_ERROR, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32601, -32602, -32603


class ToolError(Exception):
    """工具级失败 → isError 结果（协议层仍 200，让 agent 读到人话原因）。"""


def _theta() -> float:
    if os.environ.get("ER_THETA"):
        return float(os.environ["ER_THETA"])
    m = _meta()
    if m.get("theta") is not None:
        return float(m["theta"])  # 租户交付：θ 与 cases 产物同源（永不手调）
    # 与 live_pair_score / play_live_server 同源（单源 embed_client.load_theta）：
    # 优先用最新构建产物里的 θ，缺席/坏损兜底 THETA_FALLBACK
    return load_theta(ROOT / "data" / "open_cases.json")


# ---------- 数据（懒加载，启动握手不读 1MB 文件） ----------
_village_cache: dict | None = None
_scores_cache: dict | None = None


def _village() -> dict:
    global _village_cache
    if _village_cache is None:
        payload = json.loads(VILLAGE_PATH.read_text(encoding="utf-8"))
        by_rid = {r["rid"]: r for r in payload["records"]}
        by_entity: dict[str, list[dict]] = {}
        for r in payload["records"]:
            by_entity.setdefault(str(r["entity"]), []).append(r)
        _village_cache = {
            "records": payload["records"],
            "by_rid": by_rid,
            "by_entity": by_entity,
            "shadow_pairs": len(payload.get("shadow_pairs", [])),
        }
    return _village_cache


def _scores() -> dict:
    global _scores_cache
    if _scores_cache is None:
        pairs = json.loads(SCORES_PATH.read_text(encoding="utf-8"))["pairs"]
        _scores_cache = {}
        for a, b, s in pairs:
            _scores_cache[(a, b)] = s
            _scores_cache[(b, a)] = s
    return _scores_cache


# ---------- 工具实现 ----------
def _resolve(arg) -> tuple[str, object]:
    """单侧参数解析：rid → ("rid", record)；否则自由文本 → ("text", arg)。"""
    if not isinstance(arg, str) or not arg.strip():
        raise ToolError(f"a/b must be non-empty strings ({_label_word()} rid or free text)")
    v = _village()
    if arg in v["by_rid"]:
        return "rid", v["by_rid"][arg]
    m = re.fullmatch(r"E?(\d+)", arg.strip())
    if m and m.group(1) in v["by_entity"]:
        members = [r["rid"] for r in v["by_entity"][m.group(1)]]
        raise ToolError(
            f"'{arg.strip()}' is an entity id with {len(members)} records, not a record rid. "
            f"Pass two rids to er_score_pair — e.g. a='{members[0]}', b='{members[1]}' "
            f"(all members: {', '.join(members)}).")
    return "text", arg


def _field_diff(ra: dict, rb: dict) -> str:
    """字段对照表（W4 按 field-role 泛化）：租户在场时按 er_meta.fields 声明序
    排行并用其标签展示；Febrl 默认路径与历史输出逐字节一致（测试锁定）。"""
    lines = ["fields (← marks a difference):"]
    fa, fb = ra["fields"], rb["fields"]
    meta_fields = _meta().get("fields") or []
    if not meta_fields:
        for k in ra["fields"]:
            va, vb = fa.get(k), fb.get(k)
            mark = "  " if va == vb else "←"
            lines.append(f"  {mark} {k:<9} {va or '(missing)':<34} vs  {vb or '(missing)'}")
        return "\n".join(lines)
    order = [f for f, _ in meta_fields if f in fa or f in fb]
    order += [k for k in fa if k not in order]  # 未入 meta 的字段照排，不静默丢
    label_of = dict(meta_fields)
    for k in order:
        va, vb = fa.get(k), fb.get(k)
        mark = "  " if va == vb else "←"
        shown = f"{label_of.get(k, k)} [{k}]" if label_of.get(k, k) != k else k
        lines.append(f"  {mark} {shown:<18} {va or '(missing)':<34} vs  {vb or '(missing)'}")
    return "\n".join(lines)


def _search_hint() -> str:
    meta_fields = _meta().get("fields") or []
    if not meta_fields:
        return "name/dob/ssn/address/locality"
    return "/".join(f for f, _ in meta_fields)


def _preview_fields() -> tuple[str, ...]:
    meta_fields = _meta().get("fields") or []
    return tuple(f for f, _ in meta_fields) or ("name", "dob", "ssn")


def tool_score_pair(a, b) -> str:
    v = _village()
    ka, va = _resolve(a)
    kb, vb = _resolve(b)
    theta = _theta()

    if ka == "rid" and kb == "rid" and SCORES_PATH.exists():
        key = (va["rid"], vb["rid"])
        matrix = _scores()
        if key not in matrix:
            raise ToolError(f"pair {va['rid']} × {vb['rid']} not in the pre-sampled matrix "
                            f"(unexpected — matrix should be complete). Set EMB_TOKEN to score live.")
        score = matrix[key]
        mode = "pre-sampled real matrix (offline; deterministic — equals the live service output)"
        same = va["entity"] == vb["entity"]
        correct = (score >= theta) == same
        head = (f"A: {va['rid']} (true entity {va['entity']})\n"
                f"B: {vb['rid']} (true entity {vb['entity']})\n"
                f"{_truth_label()}: {'SAME identity' if same else 'DIFFERENT identities'} — "
                f"assertion {'CORRECT ✓' if correct else 'WRONG ✗'}\n")
        detail = _field_diff(va, vb)
    else:
        if not EMB_TOKEN:
            raise ToolError(
                "free-text scoring needs the live service: set the EMB_TOKEN environment "
                f"variable (platform bearer token). Zero-config alternative: pass two "
                f"{_label_word()} rids — those score offline from the pre-sampled real matrix "
                "(find rids with er_find_records).")
        texts = [va["text"] if ka == "rid" else va, vb["text"] if kb == "rid" else vb]
        try:
            vec = embed(EMB_URL, EMB_MODEL, EMB_TOKEN, texts)
        except EmbedError as e:
            raise ToolError(f"live service call failed: {e} "
                            f"({_label_word()}-rid pairs keep working offline)")
        score = cosine(vec[0], vec[1])
        mode = f"live service call (model {EMB_MODEL})"
        head = (f"A: {ka == 'rid' and va['rid'] or (va[:90] + ('…' if len(va) > 90 else ''))}\n"
                f"B: {kb == 'rid' and vb['rid'] or (vb[:90] + ('…' if len(vb) > 90 else ''))}\n")
        detail = "(free text — no ground truth / field table for this mode)"

    linked = score >= theta
    margin = score - theta
    verdict = ("LINKED — asserted same identity "
               f"(+{margin:.4f} above θ)" if linked else
               f"REFUSED — not asserted ({margin:+.4f} vs θ)")
    return (
        f"cosine similarity = {score:.4f}\n"
        f"decision line θ    = {theta:.4f} ({_theta_src()})\n"
        f"VERDICT: {verdict}\n\n"
        f"{head}\n{detail}\n\n"
        f"score mode: {mode}\n"
        f"provenance: real fine-tuned-model outputs; θ picked by link-F1 scan on the "
        f"{len(v['records'])}-record {_label_word()} (selection blind to scores); verdicts are "
        f"model assertions checked against {_truth_label()} "
        f"({_data_note('public benchmark, no real PII')}).")


def tool_find_records(q, limit) -> str:
    v = _village()
    limit = 8 if limit is None else max(1, min(int(limit), 30))
    if q is None or str(q).strip() == "":
        picks, seen = [], set()
        for r in v["records"]:  # 确定性抽样：每实体首条，稳定顺序
            if r["entity"] not in seen:
                seen.add(r["entity"])
                picks.append(r)
            if len(picks) >= limit:
                break
        head = (f"no query given — showing the first record of each of {len(picks)} entities "
                f"({_label_word()}: {len(v['records'])} records / {len(v['by_entity'])} "
                f"identities). Pass q=<substring> to search {_search_hint()} or rid.")
    else:
        ql = str(q).strip().lower()
        picks = [r for r in v["records"]
                 if ql in r["text"].lower() or ql in r["rid"].lower()
                 or ql == str(r["entity"])]
        if not picks:
            raise ToolError(f"no {_label_word()} record matches '{q}' "
                            f"(searchable: {_search_hint()}, rid, entity id).")
        head = f"{len(picks)} match(es) for '{q}' (showing up to {limit}):"
    lines = [f"- {r['rid']} · entity {r['entity']} · " +
             " | ".join(f"{k}: {r['fields'][k]}" for k in _preview_fields()
                        if r["fields"].get(k))
             for r in picks[:limit]]
    return (head + "\n" + "\n".join(lines) +
            "\n→ score two of them: er_score_pair(a='<rid>', b='<rid>') — works offline")


def tool_about() -> str:
    v = _village()
    m = _meta()
    offline = SCORES_PATH.exists()
    n_pairs = len(json.loads(SCORES_PATH.read_text(encoding="utf-8"))["pairs"]) if offline else 0
    docs_ref = str(m.get("docs_ref") or "docs/mcp.md")
    return (
        f"entity-er MCP server v{SERVER_VERSION} — "
        f"{m.get('label') or 'Febrl3 case-village'} entity-resolution scorer\n"
        f"\n"
        f"scorer    : fine-tuned embedding model “{EMB_MODEL}” (deployed on the training "
        f"platform; live calls go to its /v1/embeddings)\n"
        f"{f'{_label_word():<10}'}: {len(v['records'])} records / {len(v['by_entity'])} "
        f"identities / {v['shadow_pairs']} look-alike shadow pairs "
        f"({_data_note('Febrl3 test split, public benchmark, no real PII')})\n"
        f"θ         : {_theta():.4f} — {_theta_src(short=True)} on this {_label_word()}; entity "
        f"selection was blind to scores (rules frozen in provenance)\n"
        f"modes     : offline rid-pair scoring {'AVAILABLE (' + str(n_pairs) + ' pre-sampled real pairs)' if offline else 'UNAVAILABLE (scores file missing)'}; "
        f"live free-text scoring {'AVAILABLE (EMB_TOKEN set)' if EMB_TOKEN else 'OFF (set EMB_TOKEN to enable)'}\n"
        f"\n"
        f"tools     : er_score_pair(a, b) — real cosine + θ verdict + field diff + truth check\n"
        f"            er_find_records(q?, limit?) — search the {_label_word()}, get rids\n"
        f"            er_about — this card\n"
        f"\n"
        f"honesty   : every score is the model's real output (offline = pre-sampled "
        f"deterministic matrix, equal to live output; online = deployed service). A verdict is "
        f"the model's assertion, not a guarantee; θ was tuned on this {_label_word()} — rescan "
        f"before trusting it on other data. {docs_ref} has the full usage guide.")


def _tools() -> list[dict]:
    """工具清单（W4 起按 ER_META 动态生成）：Febrl 默认描述与历史版本逐字一致，
    租户在场时示例 rid/字段/称谓换成本数据集口径。"""
    m = _meta()
    rid_eg = str(m.get("example_rid") or "rec-476-dup-0")
    style = str(m.get("free_text_example") or "name: … | DOB: … | SSN: …")
    v = _village()
    return [
        {
            "name": "er_score_pair",
            "description": (
                "Score two records for entity resolution: real cosine similarity from the "
                "fine-tuned model, θ verdict (LINKED/REFUSED), field-by-field diff, and a check "
                f"against {_truth_label(long=True)}. Each side accepts a {m.get('label') or 'case-village'} "
                f"rid (offline, zero-config, e.g. '{rid_eg}') or free text in the "
                f"{'serialized' if m else 'village'} style "
                f"'{style}' (live call, needs EMB_TOKEN). Mixing rid × free text "
                "is allowed."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a": {"type": "string",
                          "description": f"record A: {_label_word()} rid or free text"},
                    "b": {"type": "string",
                          "description": f"record B: {_label_word()} rid or free text"},
                },
                "required": ["a", "b"],
            },
        },
        {
            "name": "er_find_records",
            "description": (
                f"Search the {m.get('label') or 'Febrl3 case village'} "
                f"({len(v['records'])} records / {len(v['by_entity'])} identities) by substring over "
                f"{_search_hint()}, or by rid / entity id. Returns rids to "
                "feed er_score_pair. Without q, returns a deterministic sample across identities."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": {"type": "string",
                          "description": "substring to search (optional)"},
                    "limit": {"type": "integer",
                              "description": "max results, default 8 (capped at 30)"},
                },
            },
        },
        {
            "name": "er_about",
            "description": (
                "Provenance & config: which model, how θ was chosen (link-F1 scan, selection "
                f"blind to scores), {_label_word()} stats, offline/live mode status, and the honesty "
                "statement. Read this before quoting numbers."),
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


# ---------- stdio JSON-RPC（MCP） ----------
def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(rid, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _error(rid, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _text_result(text: str, is_error: bool = False) -> dict:
    out = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["isError"] = True
    return out


def _handle_call(name, arguments) -> dict:
    arguments = arguments or {}
    if name == "er_score_pair":
        return _text_result(tool_score_pair(arguments.get("a"), arguments.get("b")))
    if name == "er_find_records":
        return _text_result(tool_find_records(arguments.get("q"), arguments.get("limit")))
    if name == "er_about":
        return _text_result(tool_about())
    raise KeyError(name)


def _dispatch(msg: dict) -> None:
    if "id" not in msg:  # notification（initialized / cancelled 等）：不回包
        return
    method, rid = msg.get("method"), msg["id"]
    if method == "initialize":
        want = msg.get("params", {}).get("protocolVersion")
        version = want if want in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[-1]
        _result(rid, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method == "tools/list":
        _result(rid, {"tools": _tools()})
    elif method == "tools/call":
        name = (msg.get("params") or {}).get("name")
        try:
            _result(rid, _handle_call(name, (msg.get("params") or {}).get("arguments")))
        except KeyError:
            _error(rid, INVALID_PARAMS, f"unknown tool: {name}")
        except ToolError as e:
            _result(rid, _text_result(str(e), is_error=True))
        except Exception as e:  # 工具内部崩了也不许弄死协议层
            _result(rid, _text_result(
                f"internal tool error: {type(e).__name__}: {e}", is_error=True))
    elif method == "ping":
        _result(rid, {})
    else:
        _error(rid, METHOD_NOT_FOUND, f"method not found: {method}")


def main() -> None:
    print(f"[{SERVER_NAME}] stdio MCP server v{SERVER_VERSION} | "
          f"offline matrix: {'yes' if SCORES_PATH.exists() else 'MISSING'} | "
          f"EMB_TOKEN: {'set' if EMB_TOKEN else 'not set (rid-pair offline mode only)'} | "
          f"{_label_word()}: {VILLAGE_PATH}"
          + (f" | meta: {META_PATH}" if META_PATH else ""), file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, PARSE_ERROR, "invalid JSON")
            continue
        if not isinstance(msg, dict):
            _error(None, INVALID_PARAMS, "message must be an object")
            continue
        _dispatch(msg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
