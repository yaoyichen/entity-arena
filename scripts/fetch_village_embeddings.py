#!/usr/bin/env python3
"""调用已部署的 embeddings 服务，为案例村/租户记录取向量（纯标准库 urllib）。

两种输入（二选一）：
  --village data_open/village.json        Febrl3 案例村（记录自带 text）
  --records x.csv --fieldmap fm.csv       租户记录（intake 序列化，与训练同口径：
                                          fieldmap 声明序拼 'field: value | …'，
                                          unique-key/ignore 不进文本）
服务地址/令牌来自平台 Deployed Services 的 curl_command；令牌经环境变量
EMB_TOKEN 传入，不进命令行历史。
输出：JSON {model, dim, vectors: {rid: [float…]}}——只落 .tmp（向量是中间产物，
入库的是派生的成对分数，见 build_open_cases.py / build_tenant_cases.py --emb）。

口径：全部记录用 input_type=document 编码一次（案例村/租户集都是"记录两两比对"，
不是 query→doc 检索；两模型若都可用则同口径，公平）。
用法：
  EMB_TOKEN=... python3 scripts/fetch_village_embeddings.py \
    --url https://xxx.openapi-inspire.inf.tech --model febrl3-er-gemma \
    --out .tmp/village_emb_tuned.json
  EMB_TOKEN=... python3 scripts/fetch_village_embeddings.py \
    --records client.csv --fieldmap fieldmap.csv \
    --url https://xxx.openapi-inspire.inf.tech --model client-er-model \
    --out .tmp/client_emb.json
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
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.embed_client import ssl_ctx  # noqa: E402  # 单源：TLS1.2 钳制
from entity_arena.intake import serialize_record  # noqa: E402

BATCH = 32
RETRIES = 3


def _tenant_records(records_path: Path, fieldmap_path: Path) -> list[dict]:
    """租户记录 → [{rid, text}]（intake 序列化，与训练/页面同一份口径）。"""
    from entity_arena.intake import load_fieldmap, load_records
    fields = load_fieldmap(fieldmap_path)
    _, rows = load_records(records_path)
    out = []
    for _, rec in rows:
        rid = (rec.get("record_id") or "").strip() if isinstance(rec.get("record_id"), str) \
            else rec.get("record_id")
        if not rid:
            raise SystemExit(f"记录缺 record_id：{list(rec)[:6]}…（先过 make_client_dataset.py 校验）")
        out.append({"rid": str(rid), "text": serialize_record(rec, fields)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--village", type=Path, default=None)
    ap.add_argument("--records", type=Path, default=None, help="租户记录 CSV/JSONL（与 --village 二选一）")
    ap.add_argument("--fieldmap", type=Path, default=None, help="字段角色表（--records 时必给）")
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--input-type", default="document", choices=["document", "query"])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if bool(args.village) == bool(args.records):
        raise SystemExit("--village 与 --records 恰好给一个")
    if args.records and not args.fieldmap:
        raise SystemExit("--records 需要 --fieldmap（序列化与训练同口径）")

    token = os.environ.get("EMB_TOKEN")
    if not token:
        raise SystemExit("缺 EMB_TOKEN 环境变量（平台 Deployed Services 的 Bearer 令牌）")

    if args.village is not None:
        village = json.loads(args.village.read_text(encoding="utf-8"))
        records = village["records"]
    else:
        records = _tenant_records(args.records, args.fieldmap)
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
                with urllib.request.urlopen(req, timeout=300, context=ssl_ctx()) as resp:
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
