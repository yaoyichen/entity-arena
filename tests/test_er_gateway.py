"""W3 er-gateway 行为锁定：多租户打分网关（逻辑层 + HTTP 层）。

不碰网络：embed_fn 注入 stub（沿用 test_play_live_server 模式），HTTP 层用
真实 ThreadingHTTPServer + urllib 过一遍。锁定（对应 W3 验收三条）：
① 单进程双租户：θ/字段序互不串（θ 从各自 cases 产物读，注册表 model 与
   产物不一致启动即报错）；② 在线打分与该租户入库分数逐对同值（stub 向量
   按目标余弦构造——锁的是"序列化→embed→余弦→θ 判定"管线不引入畸变，
   全量 ±6e-4 一致性由真实服务与取样管线保证）；③ 三条错误路径人话返回：
   无令牌 503 / 未知租户 404 / 超并发 429。
另锁：结构化输入按 fieldmap 序列化（与训练同口径）、未知字段 400 列白名单、
api_key 鉴权（/v1/* 要、演示页同源路径不要）、resolve 归并 + borderline 清单。
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from entity_arena.embed_client import EmbedError  # noqa: E402
from entity_arena.gateway import Gateway, GatewayError, RegistryError, load_registry  # noqa: E402
import test_tenant_cases as tt  # noqa: E402  （复用 W2 fixture 构造器，单一事实来源）

_spec = importlib.util.spec_from_file_location(
    "er_gateway", ROOT / "scripts" / "er_gateway.py")
egw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(egw)

TOKEN = "test-token"


# ---- 租户 B fixture：字段/θ 都与 A 不同（first+zip 两字段，θ=0.88） ----
B_ROWS = [
    # rid, entity, first, zip —— 同实体 0.93（F3=0.88 漏连），同名跨实体 0.87
    ("s01", "F1", "ann lee", "10001"),
    ("s02", "F1", "ann le", "10001"),
    ("s03", "F2", "bo chen", "20002"),
    ("s04", "F2", "bo chenn", "20002"),
    ("s05", "F3", "cy diaz", "30003"),
    ("s06", "F3", "cy dyaz", "30003"),
    ("s07", "G1", "ann lee", "90009"),   # 与 s01 同名异实体（影子）
    ("s08", "G2", "bo chen", "80008"),
]


def _fixture_b(path: Path) -> None:
    import csv
    with open(path / "records_b.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "entity_id", "first", "zip"])
        w.writerows(B_ROWS)
    (path / "fieldmap_b.csv").write_text(
        "field,role,normalize,neg_key\nfirst,embedding,,1\nzip,embedding,,1\n",
        encoding="utf-8")
    ent = {r[0]: r[1] for r in B_ROWS}
    first = {r[0]: r[2] for r in B_ROWS}
    special_same = {frozenset(("s05", "s06")): 0.88}
    special_cross = {frozenset(("s01", "s07")): 0.87}
    rids = [r[0] for r in B_ROWS]
    pairs = []
    for i, a in enumerate(rids):
        for b in rids[i + 1:]:
            key = frozenset((a, b))
            if ent[a] == ent[b]:
                s = special_same.get(key, 0.93)
            elif first[a] == first[b]:
                s = special_cross.get(key, 0.87)
            else:
                s = round(0.30 + (i % 5) * 0.01, 4)
            pairs.append([a, b, s])
    (path / "scores_b.json").write_text(
        json.dumps({"model": "beta-er-model", "dim": 8, "pairs": pairs}),
        encoding="utf-8")


class FakeEmbed:
    """stub：按注册表返回向量（未注册文本直接报错——序列化漂移会大声失败）。

    向量按目标余弦构造：v_a=(1,0)，v_b=(c,√(1-c²))——cos(v_a,v_b)=c 精确成立。
    """

    def __init__(self, sleep_s: float = 0.0):
        self.vecs: dict[str, list[float]] = {}
        self.calls: list[tuple[str, str, list[str]]] = []
        self.sleep_s = sleep_s

    def pair(self, base_text: str, other_text: str, cos_target: float) -> None:
        assert -1.0 <= cos_target <= 1.0
        self.vecs[base_text] = [1.0, 0.0]
        self.vecs[other_text] = [cos_target, math.sqrt(max(0.0, 1 - cos_target ** 2))]

    def lone(self, text: str) -> None:
        self.vecs[text] = [1.0, 0.0]

    def __call__(self, endpoint: str, model: str, token: str, texts: list[str]):
        if token != TOKEN:
            raise EmbedError(f"stub 收到坏令牌：{token!r}")
        if self.sleep_s:
            time.sleep(self.sleep_s)
        missing = [t for t in texts if t not in self.vecs]
        if missing:
            raise EmbedError(f"stub 没注册这些文本的向量（序列化漂移？）：{missing}")
        self.calls.append((endpoint, model, list(texts)))
        return [self.vecs[t] for t in texts]


def _post(url: str, payload, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class GatewayBase(unittest.TestCase):
    """双租户 fixture：A=标准 W2 fixture（name+city，θ=0.965），
    B=first+zip（θ=0.88）——同一进程内 θ/字段序互不串的前提。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-gw-"))
        tt._fixture_dir(cls.tmp)
        tt._fixture_scores(cls.tmp)
        _fixture_b(cls.tmp)
        tt.run("scripts/build_tenant_cases.py",
               "--records", str(cls.tmp / "records.csv"),
               "--fieldmap", str(cls.tmp / "fieldmap.csv"),
               "--scores", str(cls.tmp / "scores.json"),
               "--dataset-label", "Alpha records", "--truth-label", "Alpha labels",
               "--out", str(cls.tmp / "cases_a.json"))
        tt.run("scripts/build_tenant_cases.py",
               "--records", str(cls.tmp / "records_b.csv"),
               "--fieldmap", str(cls.tmp / "fieldmap_b.csv"),
               "--scores", str(cls.tmp / "scores_b.json"),
               "--dataset-label", "Beta records", "--truth-label", "Beta labels",
               "--out", str(cls.tmp / "cases_b.json"))
        (cls.tmp / "page_a.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>alpha demo</title>", encoding="utf-8")
        (cls.tmp / "page_b.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>beta demo</title>", encoding="utf-8")
        cls.registry_path = cls.tmp / "er_tenants.json"
        cls.registry_path.write_text(json.dumps({
            "default_tenant": "alpha",
            "tenants": {
                "alpha": {
                    "label": "Alpha records", "endpoint": "https://alpha.invalid",
                    "model": "fixture-er-model", "cases": str(cls.tmp / "cases_a.json"),
                    "fieldmap": str(cls.tmp / "fieldmap.csv"),
                    "page": str(cls.tmp / "page_a.html"), "api_key": "key-alpha"},
                "beta": {
                    "label": "Beta records", "endpoint": "https://beta.invalid",
                    "model": "beta-er-model", "cases": str(cls.tmp / "cases_b.json"),
                    "fieldmap": str(cls.tmp / "fieldmap_b.csv"),
                    "page": str(cls.tmp / "page_b.html"), "api_key": "key-beta"},
            }}, ensure_ascii=False), encoding="utf-8")
        cls.cases_a = json.loads((cls.tmp / "cases_a.json").read_text(encoding="utf-8"))
        cls.cases_b = json.loads((cls.tmp / "cases_b.json").read_text(encoding="utf-8"))
        cls.scores_a = json.loads((cls.tmp / "scores.json").read_text(encoding="utf-8"))

    @classmethod
    def make_gw(cls, embed=None, **kw) -> Gateway:
        tenants, default = load_registry(cls.registry_path)
        return Gateway(tenants=tenants, token=TOKEN, default_tenant=default,
                       embed_fn=embed if embed is not None else FakeEmbed(), **kw)


class TestRegistry(GatewayBase):
    def test_theta_from_cases_not_handwritten(self):
        gw = self.make_gw()
        self.assertEqual(gw.tenants["alpha"].theta, self.cases_a["theta"])   # 0.965
        self.assertEqual(gw.tenants["beta"].theta, self.cases_b["theta"])    # 0.88
        self.assertNotEqual(gw.tenants["alpha"].theta, gw.tenants["beta"].theta)

    def _bad_registry(self, tenants: dict, default: str | None = "alpha") -> None:
        p = self.tmp / "bad_registry.json"
        p.write_text(json.dumps({"default_tenant": default, "tenants": tenants}),
                     encoding="utf-8")
        with self.assertRaises(RegistryError) as cm:
            load_registry(p)
        self.assertIn("alpha", str(cm.exception))   # 报错带租户名

    def test_registry_model_mismatch_rejected(self):
        self._bad_registry({"alpha": {
            "endpoint": "https://x", "model": "stale-model",
            "cases": str(self.tmp / "cases_a.json"),
            "fieldmap": str(self.tmp / "fieldmap.csv")}})

    def test_registry_missing_cases_rejected(self):
        self._bad_registry({"alpha": {
            "endpoint": "https://x", "model": "fixture-er-model",
            "cases": str(self.tmp / "nope.json"),
            "fieldmap": str(self.tmp / "fieldmap.csv")}})

    def test_registry_unknown_default_rejected(self):
        self._bad_registry({"alpha": {
            "endpoint": "https://x", "model": "fixture-er-model",
            "cases": str(self.tmp / "cases_a.json"),
            "fieldmap": str(self.tmp / "fieldmap.csv")}}, default="ghost")

    def test_registry_errors_collected(self):
        """两个租户都坏 → 一次报全（失败不静默=全量收集）。"""
        bad = {"alpha": {"endpoint": "", "model": "m", "cases": "c", "fieldmap": "f"},
               "beta": {"endpoint": "https://x", "model": "m",
                        "cases": str(self.tmp / "nope.json"),
                        "fieldmap": str(self.tmp / "fieldmap.csv")}}
        p = self.tmp / "bad2.json"
        p.write_text(json.dumps({"tenants": bad}), encoding="utf-8")
        with self.assertRaises(RegistryError) as cm:
            load_registry(p)
        msg = str(cm.exception)
        self.assertIn("alpha", msg)
        self.assertIn("beta", msg)


class TestScoreLogic(GatewayBase):
    def test_structured_serialization_matches_fieldmap_order(self):
        """结构化输入按 fieldmap 声明序拼文本（与训练同口径）——stub 只认这个文本。"""
        fe = FakeEmbed()
        fe.pair("name: zoe quan | city: q-town", "name: zoe quan | city: q town", 0.99)
        gw = self.make_gw(fe)
        out = gw.score("alpha", {"name": "zoe quan", "city": "q-town"},
                       {"name": "zoe quan", "city": "q town"})
        self.assertAlmostEqual(out["score"], 0.99, places=9)
        self.assertEqual(out["mode"], "structured")
        self.assertEqual(fe.calls[0][2], ["name: zoe quan | city: q-town",
                                          "name: zoe quan | city: q town"])

    def test_unknown_field_lists_allowed(self):
        gw = self.make_gw()
        with self.assertRaises(GatewayError) as cm:
            gw.score("alpha", {"name": "a", "taxref": "x"}, {"name": "b"})
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("未知字段", cm.exception.message)
        self.assertIn("name", cm.exception.message)      # 白名单里有正确字段
        self.assertIn("city", cm.exception.message)

    def test_record_id_allowed_but_not_serialized(self):
        fe = FakeEmbed()
        fe.pair("name: a b | city: c", "name: a b | city: d", 0.5)
        gw = self.make_gw(fe)
        out = gw.score("alpha", {"record_id": "x1", "name": "a b", "city": "c"},
                       {"record_id": "x2", "name": "a b", "city": "d"})
        self.assertEqual(out["score"], 0.5)
        self.assertEqual(fe.calls[0][2][0], "name: a b | city: c")   # record_id 不进文本

    def test_parity_with_scores_file(self):
        """验收 ②：在线打分与入库分数逐对同值、判定与 cases.links 一致。
        挑 4 个互不共享记录的对（覆盖：普通真对/θ 边缘真对/影子高分拒连/漏连对）。"""
        fe = FakeEmbed()
        matrix = {frozenset((a, b)): s for a, b, s in self.scores_a["pairs"]}
        links = {frozenset((p["a"], p["b"])) for p in self.cases_a["links"]}
        recs = self.cases_a["records"]
        # 注：精确孪生对（r001/r002、r017/r019——字段全同）文本相同，任何 embedder
        # 只会给 1.0，不在"在线=入库"的可证范围内；挑文本互异的四类对。
        picked = [("r011", "r012"), ("r005", "r006"), ("r003", "r004"), ("r015", "r016")]
        for ra, rb in picked:
            fe.pair(recs[ra]["text"], recs[rb]["text"], matrix[frozenset((ra, rb))])
        gw = self.make_gw(fe)
        theta = gw.tenants["alpha"].theta
        for ra, rb in picked:
            out = gw.score("alpha", recs[ra]["fields"], recs[rb]["fields"])
            self.assertAlmostEqual(out["score"], matrix[frozenset((ra, rb))],
                                   places=6, msg=f"{ra},{rb}")
            self.assertEqual(out["verdict"] == "link",
                             frozenset((ra, rb)) in links, f"{ra},{rb} 判定与 links 不一致")
            self.assertEqual(out["theta"], theta)
        # θ 边缘对：score==θ → link 但非稳判（margin 0 < 0.01）
        out = gw.score("alpha", recs["r005"]["fields"], recs["r006"]["fields"])
        self.assertEqual(out["verdict"], "link")
        self.assertFalse(out["stable"])

    def test_theta_isolation_between_tenants(self):
        """验收 ① 核心：同一余弦 0.955，alpha(θ=0.965) 拒、beta(θ=0.88) 连。"""
        fe = FakeEmbed()
        fe.pair("name: iso x | city: aa", "name: iso y | city: bb", 0.955)
        fe.pair("first: iso x | zip: 11", "first: iso y | zip: 22", 0.955)
        gw = self.make_gw(fe)
        a = gw.score("alpha", {"name": "iso x", "city": "aa"}, {"name": "iso y", "city": "bb"})
        b = gw.score("beta", {"first": "iso x", "zip": "11"}, {"first": "iso y", "zip": "22"})
        self.assertEqual(a["verdict"], "refuse")
        self.assertEqual(b["verdict"], "link")
        # 字段序也互不串：beta 不认 alpha 的字段
        with self.assertRaises(GatewayError) as cm:
            gw.score("beta", {"name": "iso x", "city": "aa"}, {"name": "iso y", "city": "bb"})
        self.assertEqual(cm.exception.code, 400)

    def test_raw_text_mode_compat(self):
        fe = FakeEmbed()
        fe.pair("name: a | city: b", "name: c | city: d", 0.42)
        gw = self.make_gw(fe)
        out = gw.score("alpha", "name: a | city: b", "name: c | city: d")
        self.assertEqual(out["mode"], "raw")
        self.assertEqual(out["verdict"], "refuse")

    def test_no_token_503(self):
        tenants, default = load_registry(self.registry_path)
        gw = Gateway(tenants=tenants, token=None, default_tenant=default)
        with self.assertRaises(GatewayError) as cm:
            gw.score("alpha", {"name": "a", "city": "b"}, {"name": "c", "city": "d"})
        self.assertEqual(cm.exception.code, 503)
        self.assertIn("emb_token", cm.exception.message)   # 人话指路

    def test_unknown_tenant_404(self):
        gw = self.make_gw()
        with self.assertRaises(GatewayError) as cm:
            gw.score("ghost", {"name": "a"}, {"name": "b"})
        self.assertEqual(cm.exception.code, 404)
        self.assertIn("ghost", cm.exception.message)
        self.assertIn("alpha", cm.exception.message)       # 顺便列出可用租户


class TestResolveAndEmbed(GatewayBase):
    def test_resolve_components_and_borderline(self):
        fe = FakeEmbed()
        # t1-t2 0.99 连；t1-t4 0.970（θ=0.965，margin 0.005<0.01 → 连但进 borderline）；其余 0.30
        base = "name: r base | city: c0"
        fe.lone(base)
        for txt, c in [("name: r two | city: c0", 0.99),
                       ("name: r three | city: c9", 0.30),
                       ("name: r four | city: c0", 0.970)]:
            fe.pair(base, txt, c)
        gw = self.make_gw(fe)
        out = gw.resolve("alpha", [
            {"record_id": "x1", "name": "r base", "city": "c0"},
            {"record_id": "x2", "name": "r two", "city": "c0"},
            {"record_id": "x3", "name": "r three", "city": "c9"},
            {"record_id": "x4", "name": "r four", "city": "c0"},
        ])
        self.assertEqual(out["n_records"], 4)
        self.assertEqual(out["n_components"], 2)           # {x1,x2,x4} + {x3}
        self.assertEqual(out["comp_of"]["x1"], out["comp_of"]["x2"])
        self.assertEqual(out["comp_of"]["x1"], out["comp_of"]["x4"])
        self.assertNotEqual(out["comp_of"]["x1"], out["comp_of"]["x3"])
        self.assertEqual(out["n_links"], 3)
        self.assertEqual(out["n_borderline_total"], 1)     # x1-x4（0.970 vs θ=0.965）
        self.assertEqual(out["borderline_pairs"][0]["a"], "x1")

    def test_resolve_caps_batch(self):
        gw = self.make_gw()
        recs = [{"record_id": f"r{i}", "name": "n", "city": "c"}
                for i in range(300)]
        with self.assertRaises(GatewayError) as cm:
            gw.resolve("alpha", recs)
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("分批", cm.exception.message)

    def test_embed_batches_of_32(self):
        fe = FakeEmbed()
        for i in range(33):
            fe.lone(f"name: u{i} | city: c")
        gw = self.make_gw(fe)
        out = gw.embed_records("alpha", [{"name": f"u{i}", "city": "c"} for i in range(33)])
        self.assertEqual(out["count"], 33)
        self.assertEqual(out["dim"], 2)
        self.assertEqual([len(c[2]) for c in fe.calls], [32, 1])   # 32+1 两批


class TestHTTP(GatewayBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fe = FakeEmbed()
        recs = cls.cases_a["records"]
        cls.fe.pair(recs["r011"]["text"], recs["r012"]["text"], 0.99)
        # r005/r006（carol white/whte）：文本不同的一对真对，供 raw 与同源路径测试
        cls.fe.pair(recs["r005"]["text"], recs["r006"]["text"], 0.965)
        cls.gw = cls.make_gw(embed=cls.fe)
        cls.server, cls.port = egw.serve_for_tests(cls.gw)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        super().tearDownClass()

    def auth(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}

    def test_v1_score_with_key(self):
        code, out = _post(self.base + "/v1/er/score",
                          {"tenant": "alpha", "a": {"name": "frank miller", "city": "madison"},
                           "b": {"name": "frank mille", "city": "madison"}},
                          headers=self.auth("key-alpha"))
        self.assertEqual(code, 200)
        self.assertAlmostEqual(out["score"], 0.99, places=6)
        self.assertEqual(out["verdict"], "link")

    def test_v1_score_wrong_or_missing_key(self):
        body = {"tenant": "alpha", "a": {"name": "x", "city": "y"}, "b": {"name": "x", "city": "z"}}
        for headers in ({}, self.auth("key-beta"), self.auth("nope")):
            code, out = _post(self.base + "/v1/er/score", body, headers=headers)
            self.assertEqual(code, 401, out)
            self.assertIn("api_key", out["error"])

    def test_tenant_page_companion_no_key(self):
        """演示页同源路径免 key（页面本身不带任何秘钥）。"""
        code, out = _post(self.base + "/t/alpha/api/score",
                          {"a": {"name": "carol white", "city": "madison"},
                           "b": {"name": "carol whte", "city": "madison"}})
        self.assertEqual(code, 200)
        self.assertAlmostEqual(out["score"], 0.965, places=6)

    def test_api_score_default_tenant_raw_compat(self):
        """顶层 /api/score（play_live_server 兼容路径）：默认租户 + raw 文本。"""
        code, out = _post(self.base + "/api/score",
                          {"a": self.cases_a["records"]["r005"]["text"],
                           "b": self.cases_a["records"]["r006"]["text"]})
        self.assertEqual(code, 200)
        self.assertEqual(out["mode"], "raw")
        self.assertAlmostEqual(out["score"], 0.965, places=6)

    def test_unknown_tenant_http_404(self):
        code, out = _post(self.base + "/v1/er/score",
                          {"tenant": "ghost", "a": {"name": "x"}, "b": {"name": "y"}},
                          headers=self.auth("key-alpha"))
        self.assertEqual(code, 404)
        self.assertIn("未知租户", out["error"])

    def test_no_token_http_503(self):
        tenants, default = load_registry(self.registry_path)
        gw_off = Gateway(tenants=tenants, token=None, default_tenant=default)
        srv, port = egw.serve_for_tests(gw_off)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            code, out = _post(f"http://127.0.0.1:{port}/t/alpha/api/score",
                              {"a": {"name": "x", "city": "y"}, "b": {"name": "x", "city": "z"}})
            self.assertEqual(code, 503)
            self.assertIn("emb_token", out["error"])       # 人话：去哪配令牌
        finally:
            srv.shutdown()

    def test_concurrency_guard_429(self):
        """验收 ③：在途上限 1 时第二个并发请求 429 人话（bench 锚点口径）。"""
        fe = FakeEmbed(sleep_s=0.5)
        recs = self.cases_a["records"]
        fe.pair(recs["r001"]["text"], recs["r002"]["text"], 0.99)
        tenants, default = load_registry(self.registry_path)
        gw1 = Gateway(tenants=tenants, token=TOKEN, default_tenant=default,
                      embed_fn=fe, max_concurrent=1)
        srv, port = egw.serve_for_tests(gw1)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        body = {"a": recs["r001"]["fields"], "b": recs["r002"]["fields"]}
        try:
            results: list[tuple[int, dict]] = []
            def hit():
                results.append(_post(f"http://127.0.0.1:{port}/t/alpha/api/score", body))
            t1 = threading.Thread(target=hit)
            t1.start()
            time.sleep(0.15)          # 让第一个先拿到在途名额
            code, out = _post(f"http://127.0.0.1:{port}/t/alpha/api/score", body)
            self.assertEqual(code, 429)
            self.assertIn("并发", out["error"])
            self.assertIn("重试", out["error"])            # 人话：怎么办
            t1.join()
            self.assertEqual(results[0][0], 200)           # 第一个正常完成
        finally:
            srv.shutdown()

    def test_static_pages_and_landing(self):
        code, body = _get(self.base + "/t/alpha/")
        self.assertEqual(code, 200)
        self.assertIn(b"alpha demo", body)
        code, body = _get(self.base + "/t/beta/")
        self.assertEqual(code, 200)
        self.assertIn(b"beta demo", body)
        code, body = _get(self.base + "/")
        self.assertEqual(code, 200)
        self.assertIn(b"Alpha records", body)
        self.assertIn(b"Beta records", body)
        code, body = _get(self.base + "/t/ghost/")
        self.assertEqual(code, 404)

    def test_landing_escapes_registry_markup(self):
        """落地页对注册表 label/model 统一转义——注入防线不依赖上游卫生
        （注册表是运维侧输入，但仍不该给裸插值留口子）。"""
        t = self.gw.tenants["alpha"]
        old_label = t.label
        t.label = '<script>alert(1)</script>'
        try:
            body = egw._landing(self.gw).decode("utf-8")
        finally:
            t.label = old_label
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)

    def test_bad_json_400(self):
        req = urllib.request.Request(
            self.base + "/v1/er/score", data=b"not json",
            headers={"Content-Type": "application/json", **self.auth("key-alpha")},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=15)
            self.fail("should 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            self.assertIn("JSON", json.loads(e.read())["error"])


if __name__ == "__main__":
    unittest.main()
