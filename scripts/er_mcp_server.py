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
  ER_EMB_MODEL  模型名（默认 febrl3-er-gemma）
  ER_VILLAGE    案例村数据路径（默认 data_open/village.json）
  ER_SCORES     预取样分数矩阵路径（默认 data_open/village_scores.json）
  ER_THETA      覆盖判定线（默认取 data/open_cases.json 的 θ，避免两处漂移）
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.embed_client import (  # noqa: E402
    DEFAULT_MODEL, DEFAULT_URL, EmbedError, cosine, embed,
)

SERVER_NAME = "entity-er"
SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2025-01-28", "2024-11-05")

VILLAGE_PATH = Path(os.environ.get("ER_VILLAGE", ROOT / "data_open" / "village.json"))
SCORES_PATH = Path(os.environ.get("ER_SCORES", ROOT / "data_open" / "village_scores.json"))
EMB_URL = os.environ.get("ER_EMB_URL", DEFAULT_URL)
EMB_MODEL = os.environ.get("ER_EMB_MODEL", DEFAULT_MODEL)
EMB_TOKEN = os.environ.get("EMB_TOKEN")  # 只在进程内使用，绝不回显

PARSE_ERROR, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32601, -32602, -32603


class ToolError(Exception):
    """工具级失败 → isError 结果（协议层仍 200，让 agent 读到人话原因）。"""


def _theta() -> float:
    if os.environ.get("ER_THETA"):
        return float(os.environ["ER_THETA"])
    try:  # 与 live_pair_score 同源：优先用最新构建产物里的 θ，避免两处维护漂移
        return float(json.loads(
            (ROOT / "data" / "open_cases.json").read_text(encoding="utf-8"))["theta"])
    except Exception:
        return 0.9208  # 案例村 link-F1 最优（build_open_cases.py 扫描得出的兜底值）


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
        raise ToolError("a/b must be non-empty strings (village rid or free text)")
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
    lines = ["fields (← marks a difference):"]
    for k in ra["fields"]:
        va, vb = ra["fields"].get(k), rb["fields"].get(k)
        mark = "  " if va == vb else "←"
        lines.append(f"  {mark} {k:<9} {va or '(missing)':<34} vs  {vb or '(missing)'}")
    return "\n".join(lines)


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
                f"Febrl3 truth: {'SAME identity' if same else 'DIFFERENT identities'} — "
                f"assertion {'CORRECT ✓' if correct else 'WRONG ✗'}\n")
        detail = _field_diff(va, vb)
    else:
        if not EMB_TOKEN:
            raise ToolError(
                "free-text scoring needs the live service: set the EMB_TOKEN environment "
                "variable (platform bearer token). Zero-config alternative: pass two village "
                "rids — those score offline from the pre-sampled real matrix "
                "(find rids with er_find_records).")
        texts = [va["text"] if ka == "rid" else va, vb["text"] if kb == "rid" else vb]
        try:
            vec = embed(EMB_URL, EMB_MODEL, EMB_TOKEN, texts)
        except EmbedError as e:
            raise ToolError(f"live service call failed: {e} "
                            f"(village-rid pairs keep working offline)")
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
        f"decision line θ    = {theta:.4f} (village link-F1 optimal)\n"
        f"VERDICT: {verdict}\n\n"
        f"{head}\n{detail}\n\n"
        f"score mode: {mode}\n"
        f"provenance: real fine-tuned-model outputs; θ picked by link-F1 scan on the "
        f"{len(v['records'])}-record village (selection blind to scores); verdicts are model "
        f"assertions checked against Febrl3 truth (public benchmark, no real PII).")


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
                f"(village: {len(v['records'])} records / {len(v['by_entity'])} identities). "
                f"Pass q=<substring> to search name/dob/ssn/address/locality or rid.")
    else:
        ql = str(q).strip().lower()
        picks = [r for r in v["records"]
                 if ql in r["text"].lower() or ql in r["rid"].lower()
                 or ql == str(r["entity"])]
        if not picks:
            raise ToolError(f"no village record matches '{q}' "
                            f"(searchable: name/dob/ssn/address/locality, rid, entity id).")
        head = f"{len(picks)} match(es) for '{q}' (showing up to {limit}):"
    lines = [f"- {r['rid']} · entity {r['entity']} · " +
             " | ".join(f"{k}: {r['fields'][k]}" for k in ("name", "dob", "ssn")
                        if r["fields"].get(k))
             for r in picks[:limit]]
    return (head + "\n" + "\n".join(lines) +
            "\n→ score two of them: er_score_pair(a='<rid>', b='<rid>') — works offline")


def tool_about() -> str:
    v = _village()
    offline = SCORES_PATH.exists()
    n_pairs = len(json.loads(SCORES_PATH.read_text(encoding="utf-8"))["pairs"]) if offline else 0
    return (
        f"entity-er MCP server v{SERVER_VERSION} — Febrl3 case-village entity-resolution scorer\n"
        f"\n"
        f"scorer    : fine-tuned embedding model “{EMB_MODEL}” (deployed on the training "
        f"platform; live calls go to its /v1/embeddings)\n"
        f"village   : {len(v['records'])} records / {len(v['by_entity'])} identities / "
        f"{v['shadow_pairs']} look-alike shadow pairs (Febrl3 test split, public benchmark, "
        f"no real PII)\n"
        f"θ         : {_theta():.4f} — link-F1 optimal on this village; entity selection was "
        f"blind to scores (rules frozen in provenance)\n"
        f"modes     : offline rid-pair scoring {'AVAILABLE (' + str(n_pairs) + ' pre-sampled real pairs)' if offline else 'UNAVAILABLE (scores file missing)'}; "
        f"live free-text scoring {'AVAILABLE (EMB_TOKEN set)' if EMB_TOKEN else 'OFF (set EMB_TOKEN to enable)'}\n"
        f"\n"
        f"tools     : er_score_pair(a, b) — real cosine + θ verdict + field diff + truth check\n"
        f"            er_find_records(q?, limit?) — search the village, get rids\n"
        f"            er_about — this card\n"
        f"\n"
        f"honesty   : every score is the model's real output (offline = pre-sampled "
        f"deterministic matrix, equal to live output; online = deployed service). A verdict is "
        f"the model's assertion, not a guarantee; θ was tuned on this village — rescan before "
        f"trusting it on other data. docs/mcp.md has the full usage guide.")


TOOLS = [
    {
        "name": "er_score_pair",
        "description": (
            "Score two records for entity resolution: real cosine similarity from the "
            "fine-tuned model, θ verdict (LINKED/REFUSED), field-by-field diff, and a check "
            "against Febrl3 ground truth. Each side accepts a case-village rid (offline, "
            "zero-config, e.g. 'rec-476-dup-0') or free text in the village style "
            "'name: … | DOB: … | SSN: …' (live call, needs EMB_TOKEN). Mixing rid × free text "
            "is allowed."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "string",
                      "description": "record A: village rid or free text"},
                "b": {"type": "string",
                      "description": "record B: village rid or free text"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "er_find_records",
        "description": (
            "Search the Febrl3 case village (228 records / 78 identities) by substring over "
            "name / dob / ssn / address / locality, or by rid / entity id. Returns rids to "
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
            "blind to scores), village stats, offline/live mode status, and the honesty "
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
        _result(rid, {"tools": TOOLS})
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
          f"village: {VILLAGE_PATH}", file=sys.stderr)
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
