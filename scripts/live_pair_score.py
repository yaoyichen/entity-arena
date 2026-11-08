#!/usr/bin/env python3
"""现场演示：两条记录 → 部署在平台上的微调模型 → 实时余弦分数 + θ 判定。

三种用法（现场按需选）：
  1) 实体对（案例村里挑好的戏剧性样本，字段差异自动打印）：
       EMB_TOKEN=... python3 scripts/live_pair_score.py --pair 476
  2) 指定两条记录：
       EMB_TOKEN=... python3 scripts/live_pair_score.py --rids rec-a,rec-b
  3) 客户出题（自由文本，与案例村同构 "name: … / dob: …" 行式）：
       EMB_TOKEN=... python3 scripts/live_pair_score.py \
         --text-a "name: spencer green ..." --text-b "name: spencxr royans ..."

口径（诚实声明，现场也这么讲）：
  - 分数是部署服务 /v1/embeddings 的真实输出，余弦相似度，input_type=document；
  - θ=0.9328 是在 102 条记录的案例村上按 link-F1 最大化选出的判定线
    （选村对分数盲、规则写死在 provenance，非挑出来的）；
  - 判定只代表"微调模型在这对记录上的断言"，与 Febrl3 真值对照即得对错。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VILLAGE = ROOT / "data_open" / "village.json"
DEFAULT_URL = "https://9dogmocj5hg5cpbgmkmcehcg5qkpcdqm.openapi-inspire.inf.tech"
DEFAULT_MODEL = "febrl3-er-gemma"
THETA = 0.9208  # 案例村 link-F1 最优判定线（build_open_cases.py 扫描得出）
try:  # 优先用最新构建产物里的 θ，避免两处维护漂移
    THETA = float(json.loads((ROOT / "data" / "open_cases.json").read_text())["theta"])
except Exception:
    pass
RETRIES = 3


def _ssl_ctx() -> ssl.SSLContext:
    """强制 TLS1.2：平台网关掐 OpenSSL 3.x 默认 ClientHello（见 fetch_village_embeddings.py）。"""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def embed(url: str, model: str, token: str, texts: list[str]) -> list[list[float]]:
    payload = {"model": model, "input": texts, "input_type": "document",
               "encoding_format": "float"}
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST")
    body = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx()) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except (urllib.error.URLError, ssl.SSLError, OSError) as e:
            print(f"  attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            if attempt == RETRIES:
                raise SystemExit(f"embeddings 请求连续 {RETRIES} 次失败（服务还在吗？）")
            time.sleep(3 * attempt)
    return [item["embedding"] for item in sorted(body["data"], key=lambda d: d["index"])]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)


def show_pair(rec_a: dict, rec_b: dict) -> None:
    fa, fb = rec_a["fields"], rec_b["fields"]
    print(f"  记录A  {rec_a['rid']}  真值实体 E{rec_a['entity']}")
    print(f"  记录B  {rec_b['rid']}  真值实体 E{rec_b['entity']}")
    same_ent = rec_a["entity"] == rec_b["entity"]
    print(f"  Febrl3 真值：{'同一实体（应连）' if same_ent else '不同实体（不应连）'}")
    print("  字段对照（← 有差异）：")
    for k in fa:
        mark = "  " if fa.get(k) == fb.get(k) else "←"
        va = fa.get(k) or "（缺失）"
        vb = fb.get(k) or "（缺失）"
        print(f"    {mark} {k:<8} {va}  vs  {vb}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--theta", type=float, default=THETA)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pair", help="案例村实体号，如 476（该实体取差异最大的两条记录）")
    src.add_argument("--rids", help="案例村两条记录 rid，逗号分隔")
    src.add_argument("--text-a", help="自由文本 A（与 --text-b 搭配）")
    ap.add_argument("--text-b")
    ap.add_argument("--village", type=Path, default=VILLAGE)
    args = ap.parse_args()

    token = os.environ.get("EMB_TOKEN")
    if not token:
        raise SystemExit("缺 EMB_TOKEN 环境变量（平台 Deployed Services 的 Bearer 令牌）")

    if args.pair or args.rids:
        village = json.loads(args.village.read_text(encoding="utf-8"))
        recs = {r["rid"]: r for r in village["records"]}
        if args.pair:
            group = [r for r in village["records"] if str(r["entity"]) == args.pair]
            if len(group) < 2:
                raise SystemExit(f"实体 {args.pair} 在案例村中不足两条记录（现有 {len(group)} 条）")
            # 取差异字段最多的两条（现场效果最强），差异相同取 rid 序稳定的头两条
            def n_diff(a: dict, b: dict) -> int:
                return sum(1 for k in a["fields"]
                           if a["fields"].get(k) != b["fields"].get(k))
            best, best_n = None, -1
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    n = n_diff(group[i], group[j])
                    if n > best_n:
                        best, best_n = (group[i], group[j]), n
            rec_a, rec_b = best
        else:
            ids = [x.strip() for x in args.rids.split(",")]
            missing = [x for x in ids if x not in recs]
            if missing:
                raise SystemExit(f"rid 不在案例村：{missing}")
            rec_a, rec_b = recs[ids[0]], recs[ids[1]]
        show_pair(rec_a, rec_b)
        texts = [rec_a["text"], rec_b["text"]]
    else:
        if not args.text_b:
            raise SystemExit("--text-a 需要与 --text-b 搭配使用")
        print("  自由文本模式（客户出题）：")
        print(f"    A: {args.text_a}")
        print(f"    B: {args.text_b}")
        texts = [args.text_a, args.text_b]

    print(f"\n  调用部署服务 {args.model} 取向量（实时）…")
    va, vb = embed(args.url, args.model, token, texts)
    score = cosine(va, vb)
    linked = score >= args.theta
    margin = score - args.theta
    print(f"\n  余弦相似度 = {score:.4f}")
    print(f"  θ 判定线    = {args.theta:.4f}（案例村 link-F1 最优）")
    if linked:
        print(f"  ⇒ 断言同实体（LINKED，超出 θ {margin:+.4f}）")
    else:
        print(f"  ⇒ 拒绝断言（REFUSED，距 θ 还差 {margin:+.4f}）")
    print("  口径：真实服务输出；θ 由案例村 F1 扫描选定，选村对分数盲。")


if __name__ == "__main__":
    main()
