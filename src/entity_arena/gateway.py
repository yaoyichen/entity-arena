"""er-gateway 核心：多租户 ER 打分网关（MaaS API 原型，纯标准库）。

设计要点（docs/saas-maas-plan.md W3）：
- **θ 永不手调**：注册表里没有 theta 键——每租户必须给 `cases`（build_tenant_cases
  产物），θ 与模型名从该产物读，且注册表 model 与产物 model 不一致直接报错
  （防半新半旧配置静默漂移）。页面 / 报告 / 网关三方同源。
- **在线序列化与训练同一份**：结构化输入（字段→值 dict）按该租户 fieldmap 声明序
  调 intake.serialize_record 拼 `field: value | …`——就是训练/取样用过的那个函数，
  不是第二份实现。未知字段直接 400 列出允许字段（不静默丢弃）。
- **稳判口径**：|score − θ| ≥ 0.01 才算 stable（GPU 抖动 ±6e-4 的十倍余量，
  与 Explain 卡/在线打分既有约定一致）；resolve 额外回报 borderline 对清单。
- **容量护栏**（bench_embeddings.json 锚点：16 并发触上游 429）：对上游 embed
  调用加非阻塞信号量，超限 429 人话重试提示——占用的是"上游在途请求数"，
  不限制本地 HTTP 线程。
- 令牌不进注册表：全局三级解析（--token > EMB_TOKEN > ~/.config/entity-arena/
  emb_token），与 play_live_server 同一套约定。

HTTP 路由与错误码在 scripts/er_gateway.py；本模块只做可注入测试的逻辑层
（embed_fn 注入 stub，测试不碰网络——沿用 test_play_live_server 模式）。
"""

from __future__ import annotations

import hmac
import json
import threading
from dataclasses import dataclass, field as dc_field
from pathlib import Path

from entity_arena.cases_core import components
from entity_arena.embed_client import EmbedError, cosine, embed
from entity_arena.intake import FieldSpec, load_fieldmap, serialize_record

STABLE_MARGIN = 0.01          # 判定稳态边界（θ 两侧），与页面/MCP/Explain 卡同口径
EMBED_BATCH = 32              # 与 fetch_village_embeddings 同批量口径
RESOLVE_MAX = 256             # resolve 单批上限（n(n-1)/2 对；更大请分批或用 embed）
BORDERLINE_LIST_MAX = 50      # borderline 对清单回报上限（防巨响应）


class GatewayError(Exception):
    """人话错误：HTTP 层按 code 转 JSON {error}。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class RegistryError(ValueError):
    """注册表本身不合法（缺文件/缺键/θ 溯源失败）——启动期错误，逐条收集。"""


@dataclass
class Tenant:
    name: str
    label: str
    endpoint: str
    model: str
    theta: float
    fields: list[FieldSpec]
    cases_path: Path
    page: Path | None = None
    api_key: str | None = None
    theta_src: str = ""       # θ 溯源描述（启动打印/诊断用）

    @property
    def emb_fields(self) -> list[str]:
        return [fs.name for fs in self.fields if fs.role == "embedding"]


def load_registry(path: Path) -> tuple[dict[str, Tenant], str | None]:
    """注册表 JSON → ({name: Tenant}, default_tenant)。

    结构（仓库外文件，示例见 scripts/er_gateway.py 文档串）：
      {"default_tenant": "acme", "tenants": {name: {label?, endpoint, model,
        cases, fieldmap, page?, api_key?}}}
    逐租户校验、全量收集后一次报错（失败不静默）；θ 只认 cases 产物。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RegistryError(f"注册表不存在：{path}\n"
                            "  （示例结构与字段说明见 scripts/er_gateway.py --help）") from None
    except ValueError as e:
        raise RegistryError(f"注册表不是合法 JSON：{path}：{e}") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("tenants"), dict) \
            or not raw["tenants"]:
        raise RegistryError(f"注册表缺 tenants 对象：{path}")

    errors: list[str] = []
    tenants: dict[str, Tenant] = {}
    for name, cfg in raw["tenants"].items():
        try:
            tenants[name] = _load_tenant(name, cfg)
        except RegistryError as e:
            errors.append(str(e))
    if errors:
        raise RegistryError("\n".join(errors))

    default = raw.get("default_tenant")
    if default is not None and default not in tenants:
        raise RegistryError(f"default_tenant「{default}」不在 tenants 里"
                            f"（可用：{sorted(tenants)}）")
    return tenants, default


def _load_tenant(name: str, cfg: dict) -> Tenant:
    if not isinstance(cfg, dict):
        raise RegistryError(f"租户 {name}：配置必须是对象")
    missing = [k for k in ("endpoint", "model", "cases", "fieldmap") if not cfg.get(k)]
    if missing:
        raise RegistryError(f"租户 {name}：缺必备键 {missing}"
                            "（endpoint/model/cases/fieldmap）")
    cases_path = Path(cfg["cases"])
    try:
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        theta = float(cases["theta"])
        cases_model = cases["model"]
    except (OSError, ValueError, KeyError) as e:
        raise RegistryError(
            f"租户 {name}：cases 产物读不出 θ/model（{cases_path}）：{e}\n"
            "  θ 与页面/报告同源的关键——请用 build_tenant_cases.py 的产物路径") from None
    if cases_model != cfg["model"]:
        raise RegistryError(
            f"租户 {name}：注册表 model={cfg['model']!r} 与 cases 产物 model="
            f"{cases_model!r} 不一致——半新半旧配置，先重跑构建或改注册表") from None
    try:
        fields = load_fieldmap(Path(cfg["fieldmap"]))
    except Exception as e:  # intake 的报错已带行号人话，包一层租户名
        raise RegistryError(f"租户 {name}：fieldmap 加载失败：{e}") from None
    if not any(fs.role == "embedding" for fs in fields):
        raise RegistryError(f"租户 {name}：fieldmap 里没有任何 embedding 字段")
    page = Path(cfg["page"]) if cfg.get("page") else None
    if page is not None and not page.exists():
        raise RegistryError(f"租户 {name}：page 不存在：{page}")
    return Tenant(
        name=name, label=cfg.get("label") or name, endpoint=cfg["endpoint"],
        model=cfg["model"], theta=theta, fields=fields, cases_path=cases_path,
        page=page, api_key=cfg.get("api_key"),
        theta_src=f"{cases_path}（build_tenant_cases 产物）",
    )


@dataclass
class Gateway:
    """逻辑层：租户表 + 打分/向量化/归并 + 容量护栏。embed_fn 可注入。"""

    tenants: dict[str, Tenant]
    token: str | None
    default_tenant: str | None = None
    max_concurrent: int = 8
    embed_fn: object = dc_field(default=embed)   # (endpoint, model, token, texts) -> vectors

    def __post_init__(self):
        self._sem = threading.Semaphore(int(self.max_concurrent))

    # ---- 基础 ----
    def tenant(self, name: str | None) -> Tenant:
        if name is None:
            if not self.default_tenant:
                raise GatewayError(404, "未指定租户且注册表没有 default_tenant"
                                          "（body 里带 \"tenant\" 或注册表加 default_tenant）")
            name = self.default_tenant
        t = self.tenants.get(name)
        if t is None:
            raise GatewayError(404, f"未知租户「{name}」（可用：{sorted(self.tenants)}）")
        return t

    def check_key(self, t: Tenant, provided: str | None) -> None:
        if not t.api_key:
            raise GatewayError(403, f"租户「{t.name}」未配置 api_key（注册表加 api_key 字段）")
        # 恒时比较：api_key 比对不走 ==（短路比较泄漏时序信息，key 是长期凭据）
        if provided is None or not hmac.compare_digest(provided.encode(), t.api_key.encode()):
            raise GatewayError(401, f"租户「{t.name}」的 api_key 不对（Authorization: Bearer <key>）")

    def _embed(self, t: Tenant, texts: list[str]) -> list[list[float]]:
        if not self.token:
            raise GatewayError(503, "没有 embeddings 令牌：写入 ~/.config/entity-arena/"
                                      "emb_token（chmod 600）或传 --token / EMB_TOKEN")
        if not self._sem.acquire(blocking=False):
            raise GatewayError(429, "上游并发已满（容量锚点：16 并发触平台 429）——"
                                      "请稍候重试，或改批量（batch=32 吞吐更高）")
        try:
            return self.embed_fn(t.endpoint, t.model, self.token, texts)
        except EmbedError as e:
            raise GatewayError(502, f"上游 embeddings 服务失败：{e}") from None
        finally:
            self._sem.release()

    # ---- 序列化（与训练同一份口径） ----
    @staticmethod
    def serialize_input(t: Tenant, rec: dict) -> str:
        if not isinstance(rec, dict):
            raise GatewayError(400, "结构化模式下 a/b 必须是 {字段: 值} 对象"
                                      "（raw 文本请直接给字符串）")
        allowed = set(t.emb_fields) | {"record_id"}
        unknown = sorted(k for k in rec if k not in allowed)
        if unknown:
            raise GatewayError(400, f"未知字段 {unknown}——该租户的 embedding 字段是 "
                                      f"{t.emb_fields}（fieldmap: "
                                      f"{[fs.name for fs in t.fields]}）")
        for k, v in rec.items():
            if not isinstance(v, (str, int, float, bool, type(None))):
                raise GatewayError(400, f"字段 {k} 的值必须是标量（str/数字/bool/null），"
                                          f"拿到 {type(v).__name__}")
        return serialize_record(rec, t.fields)

    @staticmethod
    def _as_text(t: Tenant, x: object, which: str) -> str:
        if isinstance(x, str):
            if not x.strip():
                raise GatewayError(400, f"{which} 不能为空")
            return x
        return Gateway.serialize_input(t, x)

    # ---- 三个 API ----
    def score(self, tenant_name: str | None, a: object, b: object) -> dict:
        t = self.tenant(tenant_name)
        mode = "structured" if isinstance(a, dict) or isinstance(b, dict) else "raw"
        ta = self._as_text(t, a, "a")
        tb = self._as_text(t, b, "b")
        vecs = self._embed(t, [ta, tb])
        s = cosine(vecs[0], vecs[1])
        margin = s - t.theta
        return {
            "score": round(s, 6), "theta": t.theta, "model": t.model, "mode": mode,
            "verdict": "link" if s >= t.theta else "refuse",
            "stable": abs(margin) >= STABLE_MARGIN,
            "margin": round(margin, 6), "dim": len(vecs[0]),
        }

    def embed_records(self, tenant_name: str | None, inputs: list) -> dict:
        t = self.tenant(tenant_name)
        if not isinstance(inputs, list) or not inputs:
            raise GatewayError(400, "inputs 必须是非空数组（结构化对象或已序列化文本）")
        texts = [self._as_text(t, x, f"inputs[{i}]") for i, x in enumerate(inputs)]
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            out.extend(self._embed(t, texts[i:i + EMBED_BATCH]))
        dim = len(out[0])
        if any(len(v) != dim for v in out):
            raise GatewayError(502, "上游返回维度不一致")
        return {"model": t.model, "dim": dim, "count": len(out), "embeddings": out}

    def resolve(self, tenant_name: str | None, records: list) -> dict:
        t = self.tenant(tenant_name)
        if not isinstance(records, list) or not records:
            raise GatewayError(400, "records 必须是非空数组（每条 {record_id?, 字段…}）")
        if len(records) > RESOLVE_MAX:
            raise GatewayError(400, f"单批最多 {RESOLVE_MAX} 条（{len(records)} 条会算 "
                                      f"{len(records) * (len(records) - 1) // 2} 对）——"
                                      "请分批，或用 /v1/er/embed 自行归并")
        ids: list[str] = []
        for i, rec in enumerate(records):
            rid = rec.get("record_id") if isinstance(rec, dict) else None
            rid = str(rid).strip() if rid is not None else f"r{i}"
            if rid in ids:
                raise GatewayError(400, f"record_id 重复：{rid}")
            ids.append(rid)
        texts = [self._as_text(t, rec, f"records[{i}]") for i, rec in enumerate(records)]
        vecs: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            vecs.extend(self._embed(t, texts[i:i + EMBED_BATCH]))
        links: list[tuple[str, str]] = []
        borderline: list[dict] = []
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                s = cosine(vecs[i], vecs[j])
                if s >= t.theta:
                    links.append((ids[i], ids[j]))
                if abs(s - t.theta) < STABLE_MARGIN:
                    borderline.append({"a": ids[i], "b": ids[j], "score": round(s, 6)})
        comps = components(ids, links)
        comp_of = {rid: k for k, c in enumerate(comps) for rid in c}
        borderline.sort(key=lambda p: -p["score"])
        return {
            "model": t.model, "theta": t.theta, "n_records": len(ids),
            "n_components": len(comps), "comp_of": comp_of,
            "components": comps, "n_links": len(links),
            "borderline_pairs": borderline[:BORDERLINE_LIST_MAX],
            "n_borderline_total": len(borderline),
            "stable_rule": f"|score − θ| ≥ {STABLE_MARGIN} 才算稳判（borderline 清单供人工复核）",
        }
