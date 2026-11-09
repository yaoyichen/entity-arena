"""部署服务 /v1/embeddings 客户端（现场打分器与 MCP server 共用）。

纯标准库。两个已知坑都封在这里（AGENTS.md 部署要点）：
- 平台网关掐 OpenSSL 3.x 默认 ClientHello（后量子密钥共享）→ 强制 TLS1.2，别改回去；
- 服务偶发抖动 → 带重试（3 次，退避 3s/6s）。
"""

from __future__ import annotations

import json
import math
import ssl
import sys
import time
import urllib.request

DEFAULT_URL = "https://9dogmocj5hg5cpbgmkmcehcg5qkpcdqm.openapi-inspire.inf.tech"
DEFAULT_MODEL = "febrl3-er-gemma"
RETRIES = 3
TIMEOUT_S = 120


class EmbedError(RuntimeError):
    """连续 RETRIES 次失败（服务不可达 / 网关掐握手 / 返回体异常）。"""


def ssl_ctx() -> ssl.SSLContext:
    """强制 TLS1.2：平台网关掐 OpenSSL 3.x 默认 ClientHello。"""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def embed(url: str, model: str, token: str, texts: list[str]) -> list[list[float]]:
    """调 /v1/embeddings 取向量（input_type=document）；失败重试，最终抛 EmbedError。"""
    payload = {"model": model, "input": texts, "input_type": "document",
               "encoding_format": "float"}
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST")
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=ssl_ctx()) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return [item["embedding"] for item in sorted(body["data"], key=lambda d: d["index"])]
        except (urllib.error.URLError, ssl.SSLError, OSError, KeyError, ValueError) as e:
            last = e
            print(f"  attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            if attempt < RETRIES:
                time.sleep(3 * attempt)
    raise EmbedError(f"embeddings 请求连续 {RETRIES} 次失败（服务还在吗？）：{last}")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)
