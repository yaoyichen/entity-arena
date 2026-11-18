"""W6/W7 客户服务控制台：Workspace 逻辑 + Trainer 编排 + er_console HTTP 端到端。

覆盖：
- 步骤状态纯文件推导（上传包/训练产物/MCP 交付包落位 → 各步自动点亮）；
- 上传校验失败全量收集（人话 + 不清工作区）、base64/文件名护栏；
- 初版预览与训练同口径序列化（'field: value | …'，intake 同一份）；
- 基线代理打分：精确对 1.0、单侧缺失 0.5 贡献、状态标注 same/diff/missing；
- ④ 网络三态：cases.network/baseline.network → 三阶段 payload
  （原始影子纠缠 / 基线误连红标 / 微调归并），缺块人话报错；
- 一键示例：sample_files（Febrl3 案例村 228/78）直接过 intake 全链；
- W7 训练编排（FakeTrain 注入，不碰网络）：start 上传 zip→开训、状态推进
  RUNNING/SUCCEEDED/FAILED、deploy、build 两段子进程 argv（绝对路径 +
  EMB_TOKEN 只进 env）、产物落位后 ③④⑤ 点亮、缺包/校验失败/缺令牌人话；
- HTTP 全链：SPA 零外链（唯一 URL = SVG 命名空间）、上传→报告、
  /api/sample 回环、/api/train 三端点 + models、package.zip 确定性
  （双请求逐字节一致）、mcp.zip 整包交付（⑤ 主 CTA，同款确定性）、
  MCP 下载防路径穿越、demo 缺席 404 人话、⑤ 页专业交付形态锁定
  （zip 主 CTA/注册命令可复制/API 三态不蒸发/完成收尾）。
"""

from __future__ import annotations

import base64
import csv
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from entity_arena.console import ConsoleError, Workspace, render_console  # noqa: E402
from entity_arena.console_train import Trainer  # noqa: E402
from entity_arena import theme  # noqa: E402
from er_console import serve_for_tests  # noqa: E402

# NETWORK_BLOCK / BASELINE_NET_BLOCK：cases.network / baseline.network 的
# 最小同 shape 块（W6 网络三态速览用）
NETWORK_BLOCK = {
    "canvas": {"w": 400, "h": 260},
    "nodes_before": [{"r": "r001", "x": 10, "y": 10}, {"r": "r002", "x": 30, "y": 20},
                     {"r": "r003", "x": 200, "y": 50}],
    "nodes_after": [{"r": "r001", "x": 50, "y": 60}, {"r": "r002", "x": 50, "y": 90},
                    {"r": "r003", "x": 300, "y": 120}],
    "edges_before": [{"a": "r001", "b": "r003", "rel": "lookalike"}],
    "entities": [{"e": "comp-0", "x": 50, "y": 75, "lx": 50, "ly": 40, "size": 2},
                 {"e": "comp-1", "x": 300, "y": 120, "lx": 300, "ly": 125, "size": 1}],
    "comp_members": {"comp-0": ["r001", "r002"], "comp-1": ["r003"]},
}
BASELINE_NET_BLOCK = {
    "nodes_after": [{"r": "r001", "x": 20, "y": 20}, {"r": "r002", "x": 20, "y": 50},
                    {"r": "r003", "x": 350, "y": 200}],
    "entities": [{"e": "bcomp-0", "x": 20, "y": 35, "lx": 20, "ly": 0, "size": 2}],
    "comp_members": {"bcomp-0": ["r001", "r002", "r003"]},
}
# 与 test_tenant_cases.FIX_ROWS 同款 fixture（两字段 22 行 13 实体，
# 覆盖错字/缺失/影子；实体数足够 seed-42 切分两侧非空）
FIX_ROWS = [
    ("r001", "E01", "alice smith", "springfield"),
    ("r002", "E01", "alice smith", "springfield"),
    ("r003", "E02", "bob jones", "springfield"),
    ("r004", "E02", "bob jones", "riverton"),
    ("r005", "E03", "carol white", "madison"),
    ("r006", "E03", "carol whte", "madison"),
    ("r007", "E04", "dave brown", "riverton"),
    ("r008", "E04", "dave brwn", "riverton"),
    ("r009", "E05", "erin davis", ""),
    ("r010", "E05", "erin davis", "springfield"),
    ("r011", "E06", "frank miller", "madison"),
    ("r012", "E06", "frank mille", "madison"),
    ("r013", "E07", "grace le", ""),
    ("r014", "E07", "grace lee", "springfield"),
    ("r015", "E08", "henry wilson", "springfield"),
    ("r016", "E09", "irene taylor", "springfield"),
    ("r017", "E10", "jack moore", "riverton"),
    ("r018", "E10", "jack moore", "riverton"),
    ("r019", "E11", "jack moore", "riverton"),
    ("r020", "E11", "jack moore", "riverton"),
    ("r021", "E12", "kate nelson", "madison"),
    ("r022", "E13", "kate nelsn", "madison"),
]
N_ENTS = 13
REC_CSV = "record_id,entity_id,name,city\n" + "\n".join(",".join(r) for r in FIX_ROWS) + "\n"
FM_CSV = "field,role,normalize,neg_key\nname,embedding,,1\ncity,embedding,,1\n"


def fixture_cases() -> dict:
    """训练产物最小可用 shape（④ 效果 + 网络三态速览数据齐）。"""
    return {
        "model": "fixture-er-model", "theta": 0.965,
        "records": [{"rid": r[0], "entity": r[1]} for r in FIX_ROWS],
        "stats": {"asserted": 5, "tp": 5, "false_links": 0, "missed_pairs": 1},
        "components": [{"id": "comp-0", "pure": True}, {"id": "comp-1", "pure": True}],
        "links": [{"a": "r001", "b": "r002", "s": 0.99}],
        "missed_pairs": ["r003|r004"],
        "network": NETWORK_BLOCK,
        "baseline": {"theta": 0.75,
                     "stats": {"asserted": 6, "false_links": 1, "missed_pairs": 0},
                     "links": [{"a": "r001", "b": "r002", "s": 1.0}],
                     "false_links": ["r001|r003"],
                     "missed_pairs": [],
                     "components": [{"id": "bcomp-0", "pure": False}],
                     "network": BASELINE_NET_BLOCK},
    }


def b64_file(text: str, name: str) -> dict:
    return {"name": name, "b64": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def make_ws(tmp: Path, upload: bool = True) -> Workspace:
    ws = Workspace(tmp)
    if upload:
        ws.upload(b64_file(REC_CSV, "records.csv"), b64_file(FM_CSV, "fieldmap.csv"))
    return ws


# ---- W7：训练编排 fake（平台客户端 + 子进程 runner，全部可注入不碰网络）
class FakeTrain:
    """TrainClient 桩：script 按调用点给响应；calls 记录全程。"""

    def __init__(self, script: dict | None = None):
        self.script = script or {}
        self.calls: list[tuple] = []

    def upload_dataset(self, data: bytes, name: str) -> dict:
        self.calls.append(("upload_dataset", name, bytes(data[:2])))
        return self.script.get("upload",
                               {"upload_id": "ds-1", "viewer_url": "https://p/v",
                                "format_validation": {"valid": True}})

    def create_finetune_job(self, dataset_ids, base_model_name, output_name, **kw) -> dict:
        self.calls.append(("create_finetune_job", list(dataset_ids), base_model_name,
                           output_name, kw.get("serving_name")))
        return self.script.get("create",
                               {"job_id": "job-1", "detail_url": "https://p/j"})

    def get_job_status(self, job_id: str, include_log: bool = False) -> dict:
        self.calls.append(("get_job_status", job_id))
        return self.script.get(("status", job_id), {"job": {"status": "RUNNING"}})

    def deploy_model(self, **kw) -> dict:
        self.calls.append(("deploy_model", dict(kw)))
        return self.script.get("deploy",
                               {"job_id": "djob-1", "detail_url": "https://p/d"})

    def list_services(self) -> list[dict]:
        self.calls.append(("list_services",))
        return self.script.get("services", [])

    def list_models(self) -> list[dict]:
        return [{"name": "embeddinggemma-2"}, {"name": "bge-m3"}]


class FakeProc:
    def __init__(self, rc: int = 0, out: str = "", err: str = ""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def fake_runner(workdir: Path, jobs: list):
    """假 subprocess.run：记录 argv；见到 build_tenant_cases 就模拟落产物。"""
    def run(argv, *, cwd=None, env=None):
        argv = list(argv)
        jobs.append((argv, cwd, dict(env or {})))
        if any("build_tenant_cases.py" in a for a in argv):
            (workdir / "cases.json").write_text(
                json.dumps({"model": "m", "theta": 0.9}), encoding="utf-8")
            (workdir / "demo.html").write_text("<p>d</p>", encoding="utf-8")
            (workdir / "mcp_bundle").mkdir(exist_ok=True)
            (workdir / "mcp_bundle" / "START.md").write_text("# s", encoding="utf-8")
        else:                                   # fetch_village_embeddings → 向量文件
            out = argv[argv.index("--out") + 1]
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text('{"model":"m","dim":2,"vectors":{}}', encoding="utf-8")
        return FakeProc()
    return run


class TestWorkspaceLogic(unittest.TestCase):
    def test_upload_and_steps(self):
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = make_ws(Path(d))
            self.assertTrue((ws.root / "package" / "queries.jsonl").exists())
            st = {s["key"]: s["state"] for s in ws.steps()}
            self.assertEqual(st, {"upload": "done", "preview": "ready",
                                  "train": "waiting", "effect": "empty",
                                  "deploy": "empty"})
            rep = json.loads((ws.root / "package" / "intake_report.json")
                             .read_text(encoding="utf-8"))
            self.assertEqual(rep["records"], len(FIX_ROWS))
            self.assertEqual(rep["entities"], N_ENTS)

    def test_upload_errors_collect_all(self):
        """缺文件名 + 坏 base64 一次全报（失败不静默、不清工作区）。"""
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = make_ws(Path(d))
            with self.assertRaises(ConsoleError) as cm:
                ws.upload({"name": "", "b64": "AAAA"},
                          {"name": "fm.csv", "b64": "!!not-base64!!"})
            self.assertTrue(any("missing file name" in e for e in cm.exception.errors))
            self.assertTrue(any("base64" in e for e in cm.exception.errors))
            # 原工作区未被破坏
            self.assertTrue((ws.root / "package" / "queries.jsonl").exists())

    def test_upload_bad_fieldmap_human_errors(self):
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = Workspace(Path(d))
            with self.assertRaises(ConsoleError) as cm:
                ws.upload(b64_file(REC_CSV, "records.csv"),
                          b64_file("field,role\nzip,embedding\n", "fieldmap.csv"))
            self.assertTrue(cm.exception.errors)
            # intake 报错带字段名定位（人话）
            self.assertIn("zip", "\n".join(cm.exception.errors))

    def test_upload_filename_guard(self):
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = Workspace(Path(d))
            with self.assertRaises(ConsoleError) as cm:
                ws.upload(b64_file(REC_CSV, "../evil.csv"), b64_file(FM_CSV, "fm.csv"))
            self.assertTrue(any("illegal file name" in e for e in cm.exception.errors))

    def test_preview_serialization_training_aligned(self):
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = make_ws(Path(d))
            pv = ws.preview()
            self.assertEqual(pv["n"], len(FIX_ROWS))
            self.assertEqual(pv["shown"], len(FIX_ROWS))
            by = {r["rid"]: r for r in pv["rows"]}
            # 序列化与训练同口径：'field: value | field: value'，空值跳过
            self.assertEqual(by["r001"]["text"], "name: alice smith | city: springfield")
            self.assertEqual(by["r009"]["text"], "name: erin davis")
            self.assertIsNone(by["r009"]["fields"]["city"])
            self.assertEqual([f["name"] for f in pv["fields"]], ["name", "city"])

    def test_proxy_score_semantics(self):
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = make_ws(Path(d))
            exact = ws.proxy_score("r001", "r002")
            self.assertEqual(exact["score"], 1.0)                 # 全同 → 满分
            miss = ws.proxy_score("r009", "r010")                 # city 单侧缺失
            self.assertEqual(miss["score"], 0.75)                 # (1.0+0.5)/2 等权
            states = {f["name"]: f["state"] for f in miss["fields"]}
            self.assertEqual(states, {"name": "same", "city": "missing"})
            typo = ws.proxy_score("r005", "r006")                 # carol white/whte
            self.assertLess(typo["score"], 1.0)
            self.assertGreater(typo["score"], 0.8)
            shadow = ws.proxy_score("r017", "r019")               # 同名同城异实体
            self.assertEqual(shadow["score"], 1.0)                # 代理打分看不见实体边界
            with self.assertRaises(ConsoleError):
                ws.proxy_score("r001", "nope")

    def test_steps_light_up_with_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = make_ws(Path(d))
            cases = fixture_cases()
            (ws.root / "cases.json").write_text(
                json.dumps(cases, ensure_ascii=False), encoding="utf-8")
            (ws.root / "demo.html").write_text("<!doctype html><p>demo</p>",
                                               encoding="utf-8")
            (ws.root / "mcp_bundle").mkdir()
            (ws.root / "mcp_bundle" / "START.md").write_text("# start", encoding="utf-8")
            st = {s["key"]: s["state"] for s in ws.steps()}
            self.assertEqual(st, {"upload": "done", "preview": "ready", "train": "done",
                                  "effect": "done", "deploy": "done"})
            s = ws.summary()
            self.assertEqual(s["model"], "fixture-er-model")
            self.assertEqual(s["theta"], 0.965)
            self.assertEqual(s["entities"], N_ENTS)
            self.assertEqual(s["asserted"], 5)
            self.assertEqual(s["baseline_false"], 1)


    def test_network_payload(self):
        """④ 网络三态速览：三阶段形状 + 对字符串→二元组 + 光环纯度。"""
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = make_ws(Path(d))
            with self.assertRaises(ConsoleError):   # 无产物 → 人话报错
                ws.network()
            (ws.root / "cases.json").write_text(
                json.dumps({"theta": 1.0}), encoding="utf-8")
            with self.assertRaises(ConsoleError) as cm:   # 有产物缺网络块 → 人话报错
                ws.network()
            self.assertTrue(any("网络三态" in e for e in cm.exception.errors))
            (ws.root / "cases.json").write_text(
                json.dumps(fixture_cases(), ensure_ascii=False), encoding="utf-8")
            net = ws.network()
            self.assertEqual(net["canvas"], {"w": 400, "h": 260})
            self.assertEqual(net["shadow"], 1)
            keys = [s["key"] for s in net["stages"]]
            self.assertEqual(keys, ["before", "baseline", "tuned"])
            before, base, tuned = net["stages"]
            self.assertEqual(before["edges"], [["r001", "r003"]])          # 影子纠缠
            self.assertEqual(base["links"], [["r001", "r002"]])            # 基线断言
            self.assertEqual(base["false"], [["r001", "r003"]])            # 误连（| 串拆对）
            self.assertEqual(base["missed"], [])
            self.assertTrue(base["halos"][0]["mixed"])                    # bcomp-0 不纯 → 红光环
            self.assertEqual(tuned["missed"], [["r003", "r004"]])          # 漏连
            self.assertFalse(tuned["halos"][0]["mixed"])                   # comp-0 纯 → cyan 光环
            self.assertEqual(len(tuned["nodes"]), 3)

    def test_state_with_mcp_bundle_as_file(self):
        """mcp_bundle 被误放成文件：视为没有交付包，不裸崩（失败不静默）。"""
        with tempfile.TemporaryDirectory(prefix="ea-console-") as d:
            ws = Workspace(Path(d))
            (ws.root / "mcp_bundle").write_text("not a dir", encoding="utf-8")
            st = ws.state("x")
            self.assertEqual(st["mcp_files"], [])


class TestSampleData(unittest.TestCase):
    """一键示例：sample_files 产物直接走 Workspace.upload 全链（intake 校验真跑）。"""

    def test_sample_roundtrip(self):
        from entity_arena.console import sample_files
        s = sample_files()
        self.assertEqual(s["records_n"], 228)          # Febrl3 案例村全量
        self.assertEqual(s["entities_n"], 78)
        with tempfile.TemporaryDirectory(prefix="ea-sample-") as d:
            ws = Workspace(Path(d))
            rep = ws.upload(s["records"], s["fieldmap"])
            self.assertEqual(rep["records"], 228)
            self.assertEqual(rep["entities"], 78)
            self.assertEqual([f["field"] for f in rep["fields"]],
                             ["name", "dob", "address", "locality", "ssn"])
            pv = ws.preview()
            self.assertEqual(pv["n"], 228)
            self.assertTrue(pv["rows"][0]["text"].startswith("name: "))


class TestTrainer(unittest.TestCase):
    """W7 直连编排：start/poll/deploy/build 状态机 + argv 组装（全 fake 不碰网络）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ea-trainer-")
        self.d = Path(self._tmp.name)
        self.ws = make_ws(self.d)
        self.fake = FakeTrain()
        self.jobs: list = []
        self.tr = Trainer(self.d, self.fake, token="tok-x", label="Pilot Co",
                          run=fake_runner(self.d, self.jobs))

    def tearDown(self):
        self._tmp.cleanup()

    def test_start_upload_then_job(self):
        out = self.tr.start("embeddinggemma-2", "pilot-er-1")
        self.assertEqual(out["phase"], "training")
        self.assertEqual(out["job_id"], "job-1")
        kinds = [c[0] for c in self.fake.calls]
        self.assertEqual(kinds, ["upload_dataset", "create_finetune_job"])
        self.assertEqual(self.fake.calls[0][2], b"PK")          # 上传的是 zip
        self.assertEqual(self.fake.calls[1][1], ["ds-1"])
        self.assertEqual(self.fake.calls[1][2], "embeddinggemma-2")
        self.assertTrue((self.d / ".train" / "state.json").exists())

    def test_start_auto_names(self):
        out = self.tr.start("bge-m3")                            # 名字全默认
        self.assertTrue(out["output_name"].startswith("pilot-co-er-"))
        self.assertEqual(out["serving_name"], out["output_name"] + "-svc")

    def test_start_invalid_dataset_collects_errors(self):
        self.fake.script["upload"] = {"upload_id": "ds-bad",
                                      "format_validation": {"valid": False,
                                                            "errors": ["bad qrels"]}}
        with self.assertRaises(ConsoleError) as cm:
            self.tr.start("bge-m3")
        self.assertTrue(any("bad qrels" in e for e in cm.exception.errors))
        self.assertNotIn("create_finetune_job", [c[0] for c in self.fake.calls])

    def test_start_requires_package(self):
        tr = Trainer(self.d / "empty", self.fake)
        with self.assertRaises(ConsoleError) as cm:
            tr.start("bge-m3")
        self.assertTrue(any("数据页" in e or "训练包" in e for e in cm.exception.errors))

    def test_poll_transitions(self):
        self.tr.start("embeddinggemma-2", "m1")
        self.assertEqual(self.tr.status()["phase"], "training")  # RUNNING
        self.fake.script[("status", "job-1")] = {"job": {"status": "SUCCEEDED"},
                                                 "evaluation": {"x": 1}}
        st = self.tr.status()
        self.assertEqual(st["phase"], "trained")
        self.assertEqual(st["evaluation"], {"x": 1})
        self.fake.script[("status", "job-1")] = {"job": {"status": "FAILED"}}
        # 已到 trained 不再回看训练任务；deploy 后失败路径见下
        self.assertEqual(self.tr.status()["phase"], "trained")

    def test_train_failure_recorded(self):
        self.tr.start("embeddinggemma-2", "m1")
        self.fake.script[("status", "job-1")] = {"job": {"status": "FAILED"}}
        st = self.tr.status()
        self.assertEqual(st["phase"], "failed")
        self.assertIn("训练任务失败", st["error"])

    def test_deploy_then_build_full_chain(self):
        self.tr.start("embeddinggemma-2", "m1")
        self.fake.script[("status", "job-1")] = {"job": {"status": "SUCCEEDED"}}
        self.tr.status()
        out = self.tr.deploy()
        self.assertEqual(out["phase"], "deploying")
        self.assertEqual(self.fake.calls[-1][0], "deploy_model")
        self.fake.script[("status", "djob-1")] = {
            "job": {"status": "SUCCEEDED"},
            "service": {"service_url": "https://svc.example"}}
        st = self.tr.status()
        self.assertEqual(st["phase"], "deployed")
        self.assertEqual(st["service_url"], "https://svc.example")
        out = self.tr.build()
        self.assertEqual(out["phase"], "done")
        # 两段子进程 argv：取样向量 → build_tenant_cases（全绝对路径 + 关键旗标）
        self.assertEqual(len(self.jobs), 2)
        a1, a2 = self.jobs[0][0], self.jobs[1][0]
        self.assertIn("fetch_village_embeddings.py", a1[1])
        for flag in ("--records", "--fieldmap", "--url", "--model", "--out"):
            self.assertIn(flag, a1)
        self.assertEqual(a1[a1.index("--url") + 1], "https://svc.example")
        self.assertEqual(a1[a1.index("--model") + 1], "m1")
        self.assertIn("EMB_TOKEN", self.jobs[0][2])
        self.assertEqual(self.jobs[0][2]["EMB_TOKEN"], "tok-x")
        self.assertIn("build_tenant_cases.py", a2[1])
        for flag in ("--emb", "--demo-dir", "--mcp-dir", "--dataset-label"):
            self.assertIn(flag, a2)
        self.assertEqual(a2[a2.index("--dataset-label") + 1], "Pilot Co")
        for p in a2:
            self.assertFalse(p.startswith("./"))                  # 相对路径坑（cwd=仓库根）
        # 产物落位 → ③④⑤ 全亮（状态推导单一来源不被绕过）
        st_map = {s["key"]: s["state"] for s in self.ws.steps()}
        self.assertEqual(st_map, {"upload": "done", "preview": "ready",
                                  "train": "done", "effect": "done", "deploy": "done"})

    def test_build_requires_token(self):
        self.tr.start("embeddinggemma-2", "m1")
        self.fake.script[("status", "job-1")] = {"job": {"status": "SUCCEEDED"}}
        self.tr.status()
        self.tr.deploy()
        self.fake.script[("status", "djob-1")] = {
            "job": {"status": "SUCCEEDED"},
            "service": {"service_url": "https://svc.example"}}
        self.tr.status()
        self.tr.token = None
        with self.assertRaises(ConsoleError) as cm:
            self.tr.build()
        self.assertTrue(any("令牌" in e for e in cm.exception.errors))

    def test_no_client_falls_back_to_manual(self):
        tr = Trainer(self.d, None, label="x")
        self.assertEqual(tr.config(), {"can_train": False, "can_embed": False})
        with self.assertRaises(ConsoleError):
            tr.start("bge-m3")

    def test_mutations_mutexed(self):
        """ThreadingHTTPServer 下防并发双发：锁被占时 start/deploy/build 立即人话报错。"""
        with self.tr._lock:                    # 模拟一个在途长任务占着锁
            for call in (lambda: self.tr.start("bge-m3"),
                         self.tr.deploy, self.tr.build):
                with self.assertRaises(ConsoleError) as cm:
                    call()
                self.assertTrue(any("正在执行" in e for e in cm.exception.errors))

    def test_service_url_dig_from_exposed_domains(self):
        from entity_arena.console_train import _service_url
        self.assertEqual(_service_url({"service": {"service_url": "u1"}}), "u1")
        self.assertEqual(_service_url({"job": {"result": {"final_response":
            {"data": {"exposed_domains": ["u2"]}}}}}), "u2")
        self.assertIsNone(_service_url({"job": {}}))


class TestConsoleHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-console-http-"))
        cls.ws = make_ws(cls.tmp)
        # 训练产物（含网络三态块）+ MCP 交付包落位（④⑤ 点亮）
        (cls.tmp / "cases.json").write_text(
            json.dumps(fixture_cases(), ensure_ascii=False), encoding="utf-8")
        (cls.tmp / "mcp_bundle").mkdir()
        (cls.tmp / "mcp_bundle" / "START.md").write_text("# START", encoding="utf-8")
        cls.srv, cls.port = serve_for_tests(cls.ws, "Pilot console",
                                            api_url="http://gw.example/v1/er/score")
        import threading
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        if data is None:
            r = urllib.request.urlopen(url, timeout=10)
        else:
            r = urllib.request.urlopen(
                urllib.request.Request(url, data=json.dumps(data).encode("utf-8"),
                                       headers={"Content-Type": "application/json"},
                                       method="POST"), timeout=10)
        return r.status, r.read()

    def test_spa_zero_external_urls(self):
        code, body = self.req("/")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn("SERVICE CONSOLE", html)
        # 唯一允许的 URL 字符串 = SVG 命名空间常量（硬约束 2 同款锁定）
        import re
        urls = re.findall(r"https?://[^\s'\"<>]+", html)
        self.assertEqual(urls, [])
        self.assertIn("api/upload", html)          # 同源相对路径 fetch

    def test_spa_next_step_cta_and_train_naming(self):
        """P0 视觉修整锁定：① 概览「下一步」必须是 CTA 按钮形态（渐变实心钮
        a.btn、文案复用 nav.<step>），不得退回纯文字链——全页最重要的
        动作不能埋掉；② 训练页 h1 与左导航同名（旧「平台训练」像内部运维
        用语，客户会当成另一个页面）。"""
        code, body = self.req("/")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn("""<a class="btn" href="#/' + routeOf(nextKey)""", html)
        self.assertIn("ov.next", html)
        self.assertIn("zh:'模型训练'", html)        # train.h 新值（= nav.train 去掉步号）
        self.assertNotIn("zh:'平台训练'", html)     # 旧名不得复活

    def test_spa_nav_labels_no_circled_numbers(self):
        """导航标签不得带 ①③④⑤ 数字圆标（视觉审查否决：丑，且总览步骤卡的
        01–05 眉标已承担步骤序号，卡片上等于双重编号）。按名称指代的正文
        文案一并去数字（「④⑤ 自动点亮」这类指代没了锚点）。"""
        code, body = self.req("/")
        html = body.decode("utf-8")
        for bad in ("zh:'① 数据'", "zh:'③ 模型训练'", "zh:'④ 效果展示'",
                    "zh:'⑤ 服务部署'", "zh:'② 初版展示'", "zh:'① 数据上传'"):
            self.assertNotIn(bad, html)
        self.assertIn("zh:'数据'", html)            # 去号后的干净标签必须在
        self.assertIn("zh:'效果展示'", html)
        self.assertNotIn("④⑤ 自动点亮", html)      # 正文数字指代不得复活

    def test_spa_back_to_demo_link(self):
        """回能力演示页链接：相对路径 ../（零 URL 字符串）+ 默认 hidden +
        同源探测点亮（../healthz 有则亮）——与演示页→控制台入口互为对称；
        单独跑 er_console（自身就是根）探测 404，链接保持隐藏不留死链。"""
        code, body = self.req("/")
        html = body.decode("utf-8")
        for marker in ('id="demoLink"', 'href="../" hidden', "fetch('../healthz'",
                       "'top.demo'"):
            self.assertIn(marker, html)
        # 探测成功才摘 hidden——点亮逻辑必须存在
        self.assertIn("demoLink').hidden = false", html)

    def test_state_shape(self):
        code, body = self.req("/api/state")
        self.assertEqual(code, 200)
        st = json.loads(body)
        self.assertEqual(st["label"], "Pilot console")
        self.assertEqual(st["api"]["url"], "http://gw.example/v1/er/score")
        self.assertEqual({s["key"]: s["state"] for s in st["steps"]}["deploy"], "done")
        self.assertTrue(st["net"])          # cases.json 在 → ④ 网络速览可用
        self.assertIn("START.md", st["mcp_files"])
        self.assertTrue(st["mcp_bytes"].get("START.md", 0) > 0)  # ⑤ 页 CTA 体积元数据
        self.assertEqual(st["summary"]["theta"], 0.965)

    def test_upload_bad_fieldmap_400(self):
        url = f"http://127.0.0.1:{self.port}/api/upload"
        payload = {"records": b64_file(REC_CSV, "records.csv"),
                   "fieldmap": b64_file("field,role\nzip,embedding\n", "fm.csv")}
        try:
            urllib.request.urlopen(urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST"), timeout=10)
            self.fail("expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            errs = json.loads(e.read())["errors"]
            self.assertTrue(any("zip" in x for x in errs))

    def test_proxy_score_endpoint(self):
        code, body = self.req("/api/proxy-score", {"a": "r001", "b": "r002"})
        self.assertEqual(code, 200)
        out = json.loads(body)
        self.assertEqual(out["score"], 1.0)
        with self.assertRaises(urllib.error.HTTPError) as cm:   # 未知 rid → 400 人话
            self.req("/api/proxy-score", {"a": "r001", "b": "nope"})
        self.assertEqual(cm.exception.code, 400)

    def test_network_endpoint(self):
        code, body = self.req("/api/network")
        self.assertEqual(code, 200)
        net = json.loads(body)
        self.assertEqual([s["key"] for s in net["stages"]],
                         ["before", "baseline", "tuned"])
        self.assertEqual(net["stages"][1]["false"], [["r001", "r003"]])

    def test_package_zip_deterministic(self):
        c1, b1 = self.req("/download/package.zip")
        c2, b2 = self.req("/download/package.zip")
        self.assertEqual((c1, c2), (200, 200))
        self.assertEqual(b1[:2], b"PK")
        self.assertEqual(b1, b2)      # 确定性：双请求逐字节一致

    def test_mcp_download_and_traversal(self):
        code, body = self.req("/download/mcp/START.md")
        self.assertEqual(code, 200)
        self.assertIn(b"START", body)
        try:
            self.req("/download/mcp/..%2F..%2Fcases.json")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, (400, 404))
        try:
            self.req("/download/mcp/missing.txt")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_mcp_zip_endpoint(self):
        """⑤ 主 CTA：整包一个 zip（确定性双跑逐字节一致）；缺包 404 人话。"""
        import io as _io
        import zipfile as _zf
        c1, b1 = self.req("/download/mcp.zip")
        c2, b2 = self.req("/download/mcp.zip")
        self.assertEqual((c1, c2), (200, 200))
        self.assertEqual(b1[:2], b"PK")
        self.assertEqual(b1, b2)      # 确定性：双请求逐字节一致（同 package.zip 口径）
        self.assertIn("START.md", _zf.ZipFile(_io.BytesIO(b1)).namelist())
        # 缺交付包 → 404 人话（独立空工作区，不带 mcp_bundle）
        tmp2 = Path(tempfile.mkdtemp(prefix="ea-console-zip-"))
        srv, port = serve_for_tests(make_ws(tmp2, upload=False), "no bundle")
        import threading
        th2 = threading.Thread(target=srv.serve_forever, daemon=True)
        th2.start()   # 必须先 serve_forever：否则 shutdown() 等永不置位的停机事件=死锁
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/download/mcp.zip", timeout=10)
            self.assertEqual(cm.exception.code, 404)
            self.assertIn("mcp_bundle", json.loads(cm.exception.read())["errors"][0])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_deploy_view_professional_delivery(self):
        """⑤ 页 P1 重构锁定：交付形态=一个 zip 主 CTA（不是散落小文件列表）、
        注册命令代码块可一键复制（<bundle> 占位与 START.md 同形态）、
        HTTP API 三态（未开通给占位卡不整段蒸发，导语随在场切换）、
        包内清单带角色/大小、终点收尾条。"""
        code, body = self.req("/")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn("download/mcp.zip", html)     # 一个 zip 收拢交付
        self.assertIn("dpzip", html)                # 主 CTA 按钮形态
        self.assertIn("data-copy-cmd", html)        # 注册命令一键复制
        self.assertIn("ER_META=<bundle>/er_meta.json", html)
        self.assertIn("dp.api.off", html)           # 未开通占位卡键
        self.assertIn("dp.sub.off", html)           # 导语随 API 在场切换
        self.assertIn("dp.done", html)              # 终点收尾条
        self.assertIn("DP_ROLES", html)             # 包内清单带角色说明
        # 旧的散链大胶囊（整宽 filelink 当主下载入口）不得复活
        self.assertNotIn('<a class="filelink" href="download/mcp/', html)

    def test_demo_404_human_message(self):
        try:
            self.req("/demo/")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)
            errs = json.loads(e.read())["errors"]
            self.assertTrue(any("build_tenant_cases" in x for x in errs))

    def test_upload_missing_keys_human_400(self):
        """缺 records/fieldmap 键：400 人话 JSON（曾 AttributeError 裸崩断连）。"""
        try:
            self.req("/api/upload", {})
            self.fail("expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            errs = json.loads(e.read())["errors"]
            self.assertTrue(any("records" in x for x in errs))
            self.assertTrue(any("fieldmap" in x for x in errs))

    def test_internal_error_500_human_json(self):
        """兜底：未预期异常 → 500 人话 JSON + 服务端打栈，不裸崩断连。"""
        from unittest import mock
        with mock.patch.object(self.ws, "state", side_effect=RuntimeError("boom")):
            try:
                self.req("/api/state")
                self.fail("expected 500")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 500)
                self.assertTrue(json.loads(e.read())["errors"])


class TestConsoleTrainHTTP(unittest.TestCase):
    """W7 HTTP 面：/api/sample 一键示例 + /api/train 全链（trainer 注入 fake）。"""

    @classmethod
    def setUpClass(cls):
        import threading
        cls.tmp = Path(tempfile.mkdtemp(prefix="ea-console-train-http-"))
        cls.ws = make_ws(cls.tmp)                    # 只上传，不落 cases（训练前状态）
        cls.fake = FakeTrain()
        cls.srv, cls.port = serve_for_tests(
            cls.ws, "Pilot train", api_url=None, api_key=None,
            trainer=Trainer(cls.tmp, cls.fake, token="tok-x", label="Pilot train",
                            run=fake_runner(cls.tmp, [])))
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        if data is None:
            r = urllib.request.urlopen(url, timeout=10)
        else:
            r = urllib.request.urlopen(
                urllib.request.Request(url, data=json.dumps(data).encode("utf-8"),
                                       headers={"Content-Type": "application/json"},
                                       method="POST"), timeout=10)
        return r.status, r.read()

    def test_state_carries_train_cfg(self):
        code, body = self.req("/api/state")
        st = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(st["train_cfg"]["can_train"], True)
        self.assertEqual(st["train"]["phase"], "ready")     # 有包无任务

    def test_train_start_status_models(self):
        code, body = self.req("/api/train/start",
                              {"base_model": "embeddinggemma-2"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["phase"], "training")
        code, body = self.req("/api/train")
        self.assertEqual(json.loads(body)["phase"], "training")
        code, body = self.req("/api/train?models=1")
        self.assertEqual(json.loads(body)["models"][0], "embeddinggemma-2")
        # 无基模 → 400 人话（不静默；先清任务指针避开「任务进行中」守卫）
        (self.tmp / ".train" / "state.json").unlink(missing_ok=True)
        try:
            self.req("/api/train/start", {"base_model": ""})
            self.fail("expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            self.assertTrue(any("base model" in x for x in json.loads(e.read())["errors"]))

    def test_sample_endpoint_roundtrip(self):
        code, body = self.req("/api/sample")
        self.assertEqual(code, 200)
        s = json.loads(body)
        self.assertEqual(s["records_n"], 228)
        code, body = self.req("/api/upload", {"records": s["records"],
                                              "fieldmap": s["fieldmap"]})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["report"]["records"], 228)


class TestRenderConsole(unittest.TestCase):
    def test_template_placeholders_filled(self):
        html = render_console(theme.CSS, 'Pilot <"&"> console')
        for ph in ("__TITLE__", "__CSS__", "__JS__", "__LABEL__"):
            self.assertNotIn(ph, html)
        self.assertIn("&lt;&quot;&amp;&quot;&gt;", html)   # label HTML 转义
        self.assertIn(":root", html)                        # theme token 注入
        self.assertIn(".cwrap", html)                       # 控制台样式注入


if __name__ == "__main__":
    unittest.main()
