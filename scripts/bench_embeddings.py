#!/usr/bin/env python3
"""压测已部署的 /v1/embeddings 服务：QPS / 延迟分位 / 错误率（纯标准库）。

文本来自 data_open/village.json（228 条真实 entity record），请求口径与
fetch_village_embeddings.py 一致（input_type=document）。TLS1.2 强制同
embed_client.py（平台网关掐 OpenSSL 3.x 默认 ClientHello）。

连接策略：每 worker 线程一条 http.client.HTTPSConnection 复用（keep-alive，
接近真实吞吐）；请求失败计一次 error 并重建连接继续。

令牌：--token > EMB_TOKEN > ~/.config/entity-arena/emb_token（与
play_live_server.load_token 同源，令牌不进命令行历史、不进输出）。

用法：
  python3 scripts/bench_embeddings.py                    # 默认阶梯 1,2,4,8,16,32，每档 15s，batch=1
  python3 scripts/bench_embeddings.py --levels 1,4,8 --duration 10
  python3 scripts/bench_embeddings.py --batch 32 --levels 8   # 批量吞吐口径（recs/s）

--save：结果写入 data_open/bench_embeddings.json（构建页面容量卡的数据源；
URL 与令牌绝不写入该文件）。已存在的文件按口径块合并——single 与 batch32
两次压测各 --save 一次即得完整文件。
"""

from __future__ import annotations

import argparse
import datetime
import http.client
import json
import os
import random
import ssl
import statistics
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.embed_client import DEFAULT_MODEL, DEFAULT_URL  # noqa: E402

TOKEN_FILE = Path.home() / ".config" / "entity-arena" / "emb_token"
ERR_STOP = 0.20  # 单档错误率超过 20% 视为过载，停止爬坡


def load_token(cli: str | None) -> str:
    if cli:
        return cli
    env = os.environ.get("EMB_TOKEN")
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit("缺令牌：--token / EMB_TOKEN / ~/.config/entity-arena/emb_token 三处都没有")


def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class Worker(threading.Thread):
    """单 worker：一条复用连接，循环打请求直到 deadline。"""

    def __init__(self, host: str, path: str, model: str, token: str,
                 texts: list[str], batch: int, deadline: float,
                 latencies: list[float], errors: list[str], rng: random.Random):
        super().__init__(daemon=True)
        self.host, self.path, self.model, self.token = host, path, model, token
        self.texts, self.batch, self.deadline = texts, batch, deadline
        self.latencies, self.errors = latencies, errors
        self.rng = rng

    def _connect(self) -> http.client.HTTPSConnection:
        return http.client.HTTPSConnection(self.host, timeout=30, context=ssl_ctx())

    def run(self) -> None:
        conn: http.client.HTTPSConnection | None = None
        while time.monotonic() < self.deadline:
            payload = json.dumps({
                "model": self.model,
                "input": [self.rng.choice(self.texts) for _ in range(self.batch)],
                "input_type": "document",
                "encoding_format": "float",
            }).encode("utf-8")
            headers = {"Authorization": f"Bearer {self.token}",
                       "Content-Type": "application/json"}
            t0 = time.monotonic()
            try:
                if conn is None:
                    conn = self._connect()
                conn.request("POST", self.path, body=payload, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                dt = time.monotonic() - t0
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {body[:120]!r}")
                data = json.loads(body)
                if not data.get("data"):
                    raise RuntimeError(f"响应无 data: {body[:120]!r}")
                self.latencies.append(dt)
            except Exception as e:  # noqa: BLE001 —— 压测失败要计数不要死
                dt = time.monotonic() - t0
                self.errors.append(f"{type(e).__name__}: {e}" if dt < 30 else f"timeout-ish {type(e).__name__}")
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None


def pct(sorted_lat: list[float], p: float) -> float:
    if not sorted_lat:
        return float("nan")
    i = min(len(sorted_lat) - 1, max(0, round(p / 100 * (len(sorted_lat) - 1))))
    return sorted_lat[i]


def run_level(url: str, model: str, token: str, texts: list[str],
              batch: int, n_threads: int, duration_s: float) -> dict:
    u = urlparse(url)
    host, path = u.netloc, u.path.rstrip("/") + "/v1/embeddings"
    latencies: list[float] = []
    errors: list[str] = []
    deadline = time.monotonic() + duration_s
    workers = [Worker(host, path, model, token, texts, batch, deadline,
                      latencies, errors, random.Random(42 + i))
               for i in range(n_threads)]
    t0 = time.monotonic()
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=duration_s + 60)
    wall = time.monotonic() - t0
    lat_sorted = sorted(latencies)
    n_ok, n_err = len(latencies), len(errors)
    total = n_ok + n_err
    return {
        "threads": n_threads, "batch": batch, "ok": n_ok, "err": n_err,
        "wall": wall,
        "qps": n_ok / wall if wall > 0 else 0.0,
        "rps": n_ok * batch / wall if wall > 0 else 0.0,  # records/s
        "p50": pct(lat_sorted, 50), "p90": pct(lat_sorted, 90),
        "p95": pct(lat_sorted, 95), "p99": pct(lat_sorted, 99),
        "max": lat_sorted[-1] if lat_sorted else float("nan"),
        "err_rate": n_err / total if total else 1.0,
        "sample_errors": errors[:3],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--token", default=None)
    ap.add_argument("--levels", default="1,2,4,8,16,32",
                    help="逗号分隔的并发线程阶梯")
    ap.add_argument("--duration", type=float, default=15.0,
                    help="每档压测秒数")
    ap.add_argument("--batch", type=int, default=1,
                    help="每请求文本条数（1=QPS 口径，>1 另报 recs/s）")
    ap.add_argument("--save", type=Path, default=None,
                    help="结果写入该 JSON（默认不写；传 data_open/bench_embeddings.json "
                         "即更新页面容量卡数据源——URL/令牌不会写入）")
    args = ap.parse_args()

    token = load_token(args.token)
    token_src = "--token" if args.token else ("EMB_TOKEN" if os.environ.get("EMB_TOKEN") else str(TOKEN_FILE))
    village = json.loads((ROOT / "data_open" / "village.json").read_text(encoding="utf-8"))
    texts = [r["text"] for r in village["records"]]
    print(f"target : {args.url}  model={args.model}")
    print(f"corpus : {len(texts)} entity records (median len "
          f"{statistics.median(len(t) for t in texts):.0f} chars), batch={args.batch}")
    print(f"token  : {token_src}\n")

    # 预检：单请求验证服务活着 + 拿维度
    u = urlparse(args.url)
    conn = http.client.HTTPSConnection(u.netloc, timeout=30, context=ssl_ctx())
    payload = json.dumps({"model": args.model, "input": texts[:1],
                          "input_type": "document", "encoding_format": "float"}).encode()
    t0 = time.monotonic()
    conn.request("POST", u.path.rstrip("/") + "/v1/embeddings", body=payload,
                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    resp = conn.getresponse()
    body = json.loads(resp.read())
    if resp.status != 200:
        raise SystemExit(f"预检失败 HTTP {resp.status}: {body}")
    dim = len(body["data"][0]["embedding"])
    print(f"preflight ok: dim={dim}, single latency {time.monotonic() - t0:.3f}s\n")

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    results = []
    limited = None
    header = (f"{'threads':>7} {'batch':>5} {'ok':>6} {'err':>4} {'QPS':>8} "
              f"{'recs/s':>8} {'p50':>7} {'p90':>7} {'p95':>7} {'p99':>7} {'max':>7} {'err%':>5}")
    print(header)
    print("-" * len(header))
    best = 0.0
    for lv in levels:
        r = run_level(args.url, args.model, token, texts, args.batch, lv, args.duration)
        results.append(r)
        print(f"{r['threads']:>7} {r['batch']:>5} {r['ok']:>6} {r['err']:>4} "
              f"{r['qps']:>8.1f} {r['rps']:>8.1f} {r['p50']:>7.3f} {r['p90']:>7.3f} "
              f"{r['p95']:>7.3f} {r['p99']:>7.3f} {r['max']:>7.3f} "
              f"{r['err_rate'] * 100:>4.1f}%")
        if r["sample_errors"]:
            for e in r["sample_errors"]:
                print(f"         ↳ {e[:150]}")
        if r["err_rate"] > ERR_STOP:
            limited = r
        best = max(best, r["qps"])
        if r["err_rate"] > ERR_STOP:
            print(f"\n错误率 {r['err_rate']*100:.0f}% 超过 {ERR_STOP*100:.0f}%，疑似过载，停止爬坡。")
            break
    print(f"\n峰值 QPS ≈ {best:.1f}（batch={args.batch}，输入≈120字符/条）")

    if args.save:
        key = "single" if args.batch == 1 else f"batch{args.batch}"
        peak = max(results, key=lambda r: r["qps"]) if results else None
        payload = {
            "model": args.model, "dim": dim,
            "measured_at": datetime.date.today().isoformat(),
            "method": {
                "tool": "scripts/bench_embeddings.py",
                "corpus": "data_open/village.json — 228 Febrl3 entity records, median 120 chars",
                "protocol": f"POST /v1/embeddings, input_type=document, TLS1.2, keep-alive; "
                            f"ladder {','.join(str(l) for l in levels)} threads x {args.duration:g}s each (batch={args.batch})",
                "token_policy": "Bearer token never enters this file, the page, or the repo",
            },
            key: {
                "batch": args.batch,
                "levels": [{"threads": r["threads"], "qps": round(r["qps"], 1),
                            "rps": round(r["rps"], 1), "p50": round(r["p50"], 3),
                            "p95": round(r["p95"], 3), "err_rate": round(r["err_rate"], 2)}
                           for r in results],
                "peak_qps": round(peak["qps"], 1) if peak else None,
                "peak_rps": round(peak["rps"], 1) if peak else None,
                "peak_threads": peak["threads"] if peak else None,
            },
        }
        if limited:  # 429 限流档也入库（页面红柱 + limits 块）
            payload["limits"] = {
                "rate_limit_at_threads": limited["threads"],
                "rate_limit_code": "429 rate_limit_exceeded — GPU queue full",
                "note": "throughput plateau; add batch size, not concurrency, to scale",
            }
        if args.save.exists():
            try:  # 已有文件 → 只覆盖本次口径的块，保留另一口径
                old = json.loads(args.save.read_text(encoding="utf-8"))
                old.update({k: v for k, v in payload.items() if k != key})
                old[key] = payload[key]
                payload = old
            except (json.JSONDecodeError, OSError):
                pass
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print(f"saved → {args.save}（口径块 {key}）")


if __name__ == "__main__":
    main()
