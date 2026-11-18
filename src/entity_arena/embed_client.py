"""部署服务 /v1/embeddings 客户端（现场打分器与 MCP server 共用）。

纯标准库。两个已知坑都封在这里（AGENTS.md 部署要点）：
- 平台网关掐 OpenSSL 3.x 默认 ClientHello（后量子密钥共享）→ 强制 TLS1.2，别改回去；
- 服务偶发抖动 → 带重试（3 次，退避 3s/6s）。

本模块是 MCP 交付包里唯一的共享拷贝（build_tenant_cases --mcp-dir 原样带走），
所以三样"必须全仓单源"的东西都住在这里：ssl_ctx（TLS1.2 钳制）、cosine
（余弦，零向量返 0.0）、load_theta（open_cases.json θ 读取 + 0.9208 兜底）
——别在任何脚本里再写一份。
"""

from __future__ import annotations

import json
import math
import ssl
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://9dogmocj5hg5cpbgmkmcehcg5qkpcdqm.openapi-inspire.inf.tech"
DEFAULT_MODEL = "febrl3-er-gemma"
RETRIES = 3
TIMEOUT_S = 120
THETA_FALLBACK = 0.9208  # 案例村 link-F1 最优（build_open_cases.py 扫描得出的兜底值）


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
    """余弦相似度；零向量（不可能出现在真实 embedding 里）守卫返 0.0，
    与 cases_core 原实现同口径——单源住这里（交付包可独立运行）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def load_theta(cases_json: Path | str) -> float:
    """读构建产物 open_cases.json 的 θ（live_pair_score / play_live_server /
    er_mcp_server 三处共用同一兜底）；文件缺席/坏 JSON/缺 theta 键 →
    THETA_FALLBACK——离线或产物未构建时打分判定线依然可用。"""
    try:
        return float(json.loads(
            Path(cases_json).read_text(encoding="utf-8"))["theta"])
    except Exception:
        return THETA_FALLBACK
