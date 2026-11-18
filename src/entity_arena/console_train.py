"""③ 训练编排层（W7：控制台直连训练平台，上传 → 微调 → 部署 → 取样建 cases）。

职责与边界：
- 平台交互全部走 ``train_client.TrainClient``（MCP-over-HTTP，可注入 fake 测试）；
- 外部状态（job_id / dataset_id / 服务地址）记在 ``<workdir>/.train/state.json``
  ——**这是外部世界的指针文件，不是第二份工作区状态**：③④⑤ 的点亮仍然只看
  cases.json / demo.html / mcp_bundle（Workspace.steps 不变）；
- θ 不在这里选：build_tenant_cases 的 link-F1 扫描就是 θ 选择（口径与全仓一致）；
- 向量只落 ``.train/vectors.json``（工作区内部，不进仓库），令牌只进子进程
  环境变量、绝不落盘。

降级：未配置平台客户端（无令牌）时 ``can_train=False``，控制台回落到
W6 的人工模式（下载 zip、操作员中介）——行为与旧版一致。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import intake
from .console import ConsoleError
from .train_client import TrainClient, TrainError

ROOT = Path(__file__).resolve().parents[2]
BUSY = ("training", "deploying", "building")


# ---------------------------------------------------------------- argv 组装（纯函数，测试锁）

def embed_argv(py: str, script: Path, records: Path, fieldmap: Path,
               url: str, model: str, out: Path) -> list[str]:
    """取样向量：fetch_village_embeddings（intake 序列化与训练同口径）。"""
    return [py, str(script), "--records", str(records), "--fieldmap", str(fieldmap),
            "--url", url, "--model", model, "--out", str(out)]


def cases_argv(py: str, script: Path, records: Path, fieldmap: Path, emb: Path,
               out: Path, demo_dir: Path, mcp_dir: Path, label: str) -> list[str]:
    """向量 → cases.json + demo.html + MCP 交付包（θ=link-F1 扫描在内）。"""
    return [py, str(script), "--records", str(records), "--fieldmap", str(fieldmap),
            "--emb", str(emb), "--out", str(out), "--demo-dir", str(demo_dir),
            "--mcp-dir", str(mcp_dir), "--dataset-label", label]


# ---------------------------------------------------------------- Trainer

class Trainer:
    """一个工作区的训练编排器（client 可注入 fake；run 可注入假子进程；
    start/deploy/build 变更互斥——ThreadingHTTPServer 下防并发双发）。"""

    def __init__(self, root: Path, client: TrainClient | None = None, *,
                 token: str | None = None, label: str = "ER workspace",
                 run=None):
        self.root = Path(root)
        self.client = client
        self.token = token            # 部署取样用平台令牌（EMB_TOKEN 语义，只进 env）
        self.label = label
        self._run = run or _subprocess_run
        self._lock = threading.Lock()  # 变更互斥（非阻塞抢锁，抢不到人话报错）
        (self.root / ".train").mkdir(parents=True, exist_ok=True)

    def _begin(self) -> None:
        """同一时刻只允许一个 start/deploy/build 在执行；status 只读不锁。"""
        if not self._lock.acquire(blocking=False):
            raise ConsoleError(["已有训练操作正在执行中——等它结束再试"
                                "（取样打分可能持续数分钟）"])

    # ---- state.json（外部世界指针；③④⑤ 点亮仍只看产物文件）
    @property
    def state_path(self) -> Path:
        return self.root / ".train" / "state.json"

    def _read(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, **kw) -> dict:
        st = {**self._read(), **kw, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.state_path.write_text(json.dumps(st, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        return st

    def _fail(self, msg: str) -> dict:
        return self._write(phase="failed", error=msg)

    # ---- 无副文件的配置面（/api/state 用，不碰网络）
    def config(self) -> dict:
        return {"can_train": self.client is not None,
                "can_embed": bool(self.token)}

    def file_state(self) -> dict:
        st = self._read()
        if st:
            return st
        has_pkg = (self.root / "package" / "intake_report.json").exists()
        return {"phase": "ready" if has_pkg else "idle"}

    # ---- 上传原件定位（与 Workspace._find 同源逻辑，读 intake_report.source）
    def _find(self, slug: str) -> Path:
        report = self.root / "package" / "intake_report.json"
        if not report.exists():
            raise ConsoleError(["还没有训练包——先在 ① 数据页上传并通过校验"])
        name = json.loads(report.read_text(encoding="utf-8")).get(
            "source", {}).get(slug)
        p = self.root / "uploads" / str(name or "")
        if name and p.exists():
            return p
        raise ConsoleError([f"工作区里找不到 {slug} 上传原件（{name}）——重新上传一次"])

    # ---- ③-1 上传 + 开训
    def start(self, base_model: str, output_name: str | None = None,
              serving_name: str | None = None) -> dict:
        self._begin()
        try:
            return self._start(base_model, output_name, serving_name)
        finally:
            self._lock.release()

    def _start(self, base_model: str, output_name: str | None = None,
               serving_name: str | None = None) -> dict:
        if self.client is None:
            raise ConsoleError(["平台客户端未配置（缺令牌？）——用 --train-token/"
                                "TRAIN_TOKEN 启动 er_console，或走人工模式下载 zip"])
        if not (self.root / "package" / "intake_report.json").exists():
            raise ConsoleError(["还没有训练包——先在 ① 数据页上传并通过校验"])
        if self.file_state().get("phase") in BUSY:
            raise ConsoleError(["已有任务在进行中——等它结束（或刷新看进度）再开新的"])
        base_model = (base_model or "").strip()
        if not base_model:
            raise ConsoleError(["base model 不能为空（③ 页面有平台可选基模列表）"])
        out_name = (output_name or "").strip() or _auto_name(self.label)
        svc_name = (serving_name or "").strip() or f"{out_name}-svc"

        zp = intake.zip_package(self.root / "package")
        try:
            up = self.client.upload_dataset(
                zp.read_bytes(), f"{_slug(self.label)}-{time.strftime('%Y%m%d')}.zip")
        except TrainError as e:
            raise ConsoleError([f"上传训练包失败：{e}"]) from None
        fv = up.get("format_validation") or {}
        if fv.get("valid") is False:
            errs = fv.get("errors") or [str(fv)[:300]] or ["平台校验未通过"]
            raise ConsoleError([f"平台校验未通过：{e}" for e in errs][:8])
        ds_id = up.get("upload_id") or up.get("dataset_id")
        if not ds_id:
            raise ConsoleError([f"平台上传没回 dataset id：{json.dumps(up)[:300]}"])

        try:
            job = self.client.create_finetune_job(
                [str(ds_id)], base_model, out_name, serving_name=svc_name)
        except TrainError as e:
            raise ConsoleError([f"创建训练任务失败：{e}"]) from None
        job_id = job.get("job_id")
        if not job_id:
            raise ConsoleError([f"平台没回 job_id：{json.dumps(job)[:300]}"])
        return self._write(phase="training", dataset_id=str(ds_id), job_id=str(job_id),
                           base_model=base_model, output_name=out_name,
                           serving_name=svc_name, viewer_url=up.get("viewer_url"),
                           detail_url=job.get("detail_url"), error=None)

    # ---- 状态推进（唯一会碰平台网络的路径，轮询由前端 /api/train 驱动）
    def status(self) -> dict:
        st = self._read()
        if not st:
            return self.file_state()
        phase = st.get("phase")
        if self.client is None:
            return st
        if phase == "training" and st.get("job_id"):
            try:
                out = self.client.get_job_status(str(st["job_id"]))
            except TrainError as e:
                return {**st, "warning": f"平台状态查询失败：{e}"}
            return self._advance_train(st, out)
        if phase == "deploying" and st.get("deploy_job_id"):
            try:
                out = self.client.get_job_status(str(st["deploy_job_id"]))
            except TrainError as e:
                return {**st, "warning": f"平台状态查询失败：{e}"}
            return self._advance_deploy(st, out)
        return st

    def _advance_train(self, st: dict, out: dict) -> dict:
        job = out.get("job") or out
        pstat = str(job.get("status") or "").upper()
        if "FAIL" in pstat or "ERROR" in pstat:
            return self._fail(f"训练任务失败（{job.get('status')}）——"
                              f"详情见平台任务页；日志可用 get_job_status include_log")
        if "SUCCEED" in pstat or "COMPLETE" in pstat or pstat == "FINISHED":
            ev = out.get("evaluation") or {}
            return self._write(phase="trained", evaluation=ev, error=None)
        return self._write(phase="training", platform_status=job.get("status"))

    def _advance_deploy(self, st: dict, out: dict) -> dict:
        job = out.get("job") or out
        pstat = str(job.get("status") or "").upper()
        if "FAIL" in pstat or "ERROR" in pstat:
            return self._fail(f"部署失败（{job.get('status')}）——"
                              f"平台 Deployed Services 里确认服务状态后重试")
        if not ("SUCCEED" in pstat or "COMPLETE" in pstat or pstat == "FINISHED"):
            return self._write(phase="deploying", platform_status=job.get("status"))
        url = _service_url(out) or self._service_from_list(st)
        if not url:
            return self._fail("部署完成但没拿到服务地址——去平台 Deployed Services "
                              "确认后把服务地址补进 .train/state.json（service_url）")
        return self._write(phase="deployed", service_url=url, error=None)

    def _service_from_list(self, st: dict) -> str | None:
        try:
            for svc in self.client.list_services():
                if (svc.get("model_name") == st.get("output_name")
                        or svc.get("name") == st.get("serving_name")):
                    return svc.get("service_url") or svc.get("url")
        except (TrainError, AttributeError):
            pass
        return None

    # ---- ③-2 部署
    def deploy(self) -> dict:
        self._begin()
        try:
            return self._deploy()
        finally:
            self._lock.release()

    def _deploy(self) -> dict:
        if self.client is None:
            raise ConsoleError(["平台客户端未配置——无法自动部署"])
        st = self._read()
        if st.get("phase") not in ("trained", "failed", "deploying"):
            raise ConsoleError(["先等训练完成（phase=trained）再部署"])
        if st.get("phase") == "deploying":
            raise ConsoleError(["部署已在进行中——刷新看进度"])
        try:
            job = self.client.deploy_model(job_id=str(st.get("job_id")),
                                           serving_name=st.get("serving_name"))
        except TrainError as e:
            raise ConsoleError([f"创建部署任务失败：{e}"]) from None
        djob = job.get("job_id")
        if not djob:
            raise ConsoleError([f"平台没回部署 job_id：{json.dumps(job)[:300]}"])
        return self._write(phase="deploying", deploy_job_id=str(djob),
                           deploy_detail_url=job.get("detail_url"), error=None)

    # ---- ③-3 取样向量 → θ 扫描 → cases/demo/MCP（④⑤ 点亮）
    def build(self) -> dict:
        self._begin()
        try:
            return self._build()
        finally:
            self._lock.release()

    def _build(self) -> dict:
        st = self._read()
        if st.get("phase") not in ("deployed", "failed", "building"):
            raise ConsoleError(["先完成部署（phase=deployed）再生成效果数据"])
        url = st.get("service_url")
        if not url:
            raise ConsoleError(["缺部署服务地址（service_url）——先完成部署"])
        if not self.token:
            raise ConsoleError(["缺平台令牌（取样向量要用）——用 --train-token/"
                                "TRAIN_TOKEN 启动 er_console 后重试"])
        rec, fm = self._find("records"), self._find("fieldmap")
        vecs = self.root / ".train" / "vectors.json"
        model = st.get("output_name") or ""
        self._write(phase="building", error=None)
        env = {**os.environ, "EMB_TOKEN": self.token}
        a1 = embed_argv(sys.executable, ROOT / "scripts" / "fetch_village_embeddings.py",
                        rec, fm, url, model, vecs)
        p1 = self._run(a1, cwd=ROOT, env=env)
        if p1.returncode != 0:
            return self._fail(f"向量取样失败：{_tail(p1)}")
        a2 = cases_argv(sys.executable, ROOT / "scripts" / "build_tenant_cases.py",
                        rec, fm, vecs, self.root / "cases.json", self.root,
                        self.root / "mcp_bundle", self.label)
        p2 = self._run(a2, cwd=ROOT, env=env)
        if p2.returncode != 0:
            return self._fail(f"效果产物构建失败：{_tail(p2)}")
        if not (self.root / "cases.json").exists():
            return self._fail("构建命令成功返回但 cases.json 没出现——路径疑被写错")
        return self._write(phase="done", error=None)

    # ---- 便捷
    def models(self) -> list[dict]:
        if self.client is None:
            return []
        try:
            return self.client.list_models()
        except TrainError:
            return []


def _subprocess_run(argv: list[str], *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True,
                          text=True, timeout=1800)


def _tail(p: subprocess.CompletedProcess, n: int = 400) -> str:
    blob = ((p.stderr or "") + "\n" + (p.stdout or "")).strip()
    return blob[-n:] or "(无输出)"


def _auto_name(label: str) -> str:
    return _slug(label) + "-er-" + time.strftime("%y%m%d")


def _slug(label: str) -> str:
    s = "".join(c if c.isalnum() else "-" for c in label.lower())
    return "-".join(x for x in s.split("-") if x)[:32] or "tenant"


def _service_url(out: dict) -> str | None:
    """从 get_job_status 响应里挖服务地址（skill：顶层不保证有，多级兜底）。"""
    svc = out.get("service") or {}
    for k in ("service_url", "url"):
        if isinstance(svc, dict) and svc.get(k):
            return svc[k]
    job = out.get("job") or {}
    fr = ((job.get("result") or {}).get("final_response") or {})
    data = fr.get("data") or {}
    doms = data.get("exposed_domains")
    if isinstance(doms, list) and doms and isinstance(doms[0], str):
        return doms[0]
    if isinstance(data.get("service_url"), str):
        return data["service_url"]
    return None
