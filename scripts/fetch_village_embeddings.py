#!/usr/bin/env python3
"""调用已部署的 embeddings 服务，为案例村记录取向量（纯标准库 urllib）。

输入：data_open/village.json（记录文本）+ 服务地址/令牌（来自平台 Deployed Services
的 curl_command；令牌经环境变量 EMB_TOKEN 传入，不进命令行历史）。
输出：JSON {model, dim, vectors: {rid: [float…]}}——只落 .tmp（向量是中间产物，
入库的是派生的成对分数 data_open/village_scores.json，见 build_open_cases.py）。

口径：全部记录用 input_type=document 编码一次（案例村是"记录两两比对"，不是
query→doc 检索；两模型若都可用则同口径，公平）。
用法：
  EMB_TOKEN=... python3 scripts/fetch_village_embeddings.py \
    --url https://xxx.openapi-inspire.inf.tech --model febrl3-er-gemma \
    --out .tmp/village_emb_tuned.json
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATCH = 32
RETRIES = 3


def _ssl_ctx() -> ssl.SSLContext:
    """强制 TLS1.2：平台网关会掐 OpenSSL 3.x 默认 ClientHello（含后量子
    密钥共享，握手即 UNEXPECTED_EOF）；curl/LibreSSL 与 TLS1.2 均正常。"""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--village", type=Path, default=ROOT / "data_open" / "village.json")
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--input-type", default="document", choices=["document", "query"])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    token = os.environ.get("EMB_TOKEN")
    if not token:
        raise SystemExit("缺 EMB_TOKEN 环境变量（平台 Deployed Services 的 Bearer 令牌）")

    village = json.loads(args.village.read_text(encoding="utf-8"))
    records = village["records"]
    vectors: dict[str, list[float]] = {}
    dim = None

    for start in range(0, len(records), BATCH):
        chunk = records[start:start + BATCH]
        payload = {
            "model": args.model,
            "input": [r["text"] for r in chunk],
            "input_type": args.input_type,
            "encoding_format": "float",
        }
        req = urllib.request.Request(
            args.url.rstrip("/") + "/v1/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        body = None
        for attempt in range(1, RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=300, context=_ssl_ctx()) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except (urllib.error.URLError, ssl.SSLError, OSError) as e:
                print(f"  batch {start // BATCH + 1}: attempt {attempt}/{RETRIES} failed: {e}",
                      file=sys.stderr)
                if attempt == RETRIES:
                    raise SystemExit(f" embeddings 请求连续 {RETRIES} 次失败")
                time.sleep(5 * attempt)
        if "data" not in body:
            raise SystemExit(f"API 响应异常：{json.dumps(body)[:300]}")
        for item in body["data"]:
            idx = item["index"]
            emb = item["embedding"]
            if dim is None:
                dim = len(emb)
            if len(emb) != dim:
                raise SystemExit(f"维度不一致：{len(emb)} != {dim}")
            vectors[chunk[idx]["rid"]] = emb
        print(f"  batch {start // BATCH + 1}: {len(chunk)} recs ok (dim={dim})", file=sys.stderr)

    missing = [r["rid"] for r in records if r["rid"] not in vectors]
    if missing:
        raise SystemExit(f"缺失向量：{missing[:5]} … 共 {len(missing)}")

    args.out.write_text(
        json.dumps({"model": args.model, "dim": dim, "vectors": vectors}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {args.out}: {len(vectors)} vectors, dim={dim}")


if __name__ == "__main__":
    main()
