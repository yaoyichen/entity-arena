"""服务控制台逻辑层（W6/W7）：客户自助工作区——数据 → 训练 → 效果 → 部署。

纯逻辑无 HTTP（scripts/er_console.py 是薄壳，模式同 gateway.py/er_gateway.py）。
**工作区状态从文件推导，不存第二份状态**（单一事实来源，重启不漂移）：

    <workdir>/
      uploads/records.csv|jsonl + fieldmap.csv (+ pairs.csv)   ① 客户上传原件
      package/… + intake_report.json                           ① run_intake 产物（训练包）
      package.zip                                              ③ 按需确定性打包
      .train/state.json                                        ③ 平台任务指针（W7：job_id/
                                                                 服务地址等外部状态，不是
                                                                 工作区状态的第二份）
      cases.json (+ demo.html + data/)                         ④ 训练产物（W7 起由 ③ 直连
                                                                 流水线自动生成；人工拷入亦可）
      mcp_bundle/START.md …                                    ⑤ MCP 交付包（W4 七件套）

W7 直连模式（scripts/er_console.py 配了平台令牌即激活）：③ 上传包→平台微调→
部署→全对打分→θ 由 link-F1 扫描自动选定→cases/demo/MCP 全套落工作区，④⑤
自动点亮；编排逻辑在 console_train.py。未配令牌时回落 W6 人工模式（下载
zip、操作员中介），行为不变。② 的打分始终是**训练前字面相似度代理**
（intake 同一套序列化与归一），明确标注"非模型输出"。
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
from pathlib import Path

from . import intake
from .cases_core import baseline_score
from .intake import (IntakeError, _to_text, apply_normalize, load_fieldmap,
                     load_records, serialize_record)

PREVIEW_LIMIT = 200                     # 初版展示记录表上限（全量在文件里）
MAX_UPLOAD_BYTES = 20 * 1024 * 1024     # 单文件 20MB（试点规模足够，防误传）


class ConsoleError(Exception):
    """人话失败（失败不静默）：errors 逐条可渲染。"""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------- 示例数据（Febrl 公开基准）
# 与 tests/test_tenant_cases.py 锚 B 同一套写法（village.json → 格式 A CSV +
# fieldmap），保证「一键载入示例」的数据与锚 B 逐字节同源。
SAMPLE_LABELS = {"name": "Name|姓名", "dob": "Date of birth|生日",
                 "address": "Address|地址", "locality": "Locality|地区",
                 "ssn": "ID Number|证件号"}
SAMPLE_WEIGHTS = [("name", 0.40), ("dob", 0.20), ("address", 0.20),
                  ("locality", 0.10), ("ssn", 0.10)]


def sample_files(repo_root: Path | None = None) -> dict:
    """Febrl3 案例村 → 上传载荷（records/fieldmap b64），供「一键载入示例」。

    公开基准、无真实 PII（诚实标注）；仓库缺 village.json 时人话报错不静默。
    """
    root = Path(repo_root or Path(__file__).resolve().parents[2])
    vp = root / "data_open" / "village.json"
    if not vp.exists():
        raise ConsoleError(["仓库里没有 data_open/village.json——示例数据不可用，"
                            "请检查部署完整性"])
    v = json.loads(vp.read_text(encoding="utf-8"))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["record_id", "entity_id", "name", "dob", "address", "locality", "ssn"])
    for r in v["records"]:
        fl = r["fields"]
        w.writerow([r["rid"], r["entity"], fl.get("name", ""), fl.get("dob", ""),
                    fl.get("address", ""), fl.get("locality", ""), fl.get("ssn", "")])
    rec_b64 = base64.b64encode(buf.getvalue().encode("utf-8")).decode("ascii")
    buf2 = io.StringIO()
    w2 = csv.writer(buf2, lineterminator="\n")
    w2.writerow(["field", "role", "normalize", "neg_key", "baseline_weight", "label"])
    for name, wgt in SAMPLE_WEIGHTS:
        w2.writerow([name, "embedding", "", "1", f"{wgt:.2f}", SAMPLE_LABELS[name]])
    fm_b64 = base64.b64encode(buf2.getvalue().encode("utf-8")).decode("ascii")
    return {
        "records": {"name": "febrl3_sample_records.csv", "b64": rec_b64},
        "fieldmap": {"name": "febrl3_sample_fieldmap.csv", "b64": fm_b64},
        "records_n": len(v["records"]),
        "entities_n": len({r["entity"] for r in v["records"]}),
        "note": "Febrl3 public benchmark · programmatically generated · no real PII",
    }


# ---------------------------------------------------------------- 工作区

class Workspace:
    """一个客户工作区：上传 → 校验/训练包 →（操作员训练）→ 产物点亮后续步骤。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "uploads").mkdir(parents=True, exist_ok=True)

    # ---- 步骤状态：全部从文件推导（单一事实来源）
    def steps(self) -> list[dict]:
        has_pkg = (self.root / "package" / "intake_report.json").exists()
        has_cases = (self.root / "cases.json").exists()
        has_demo = (self.root / "demo.html").exists()
        has_mcp = (self.root / "mcp_bundle" / "START.md").exists()
        return [
            {"key": "upload", "state": "done" if has_pkg else "empty"},
            {"key": "preview", "state": "ready" if has_pkg else "empty"},
            {"key": "train",
             "state": "done" if has_cases else ("waiting" if has_pkg else "empty")},
            {"key": "effect",
             "state": "done" if has_cases else "empty", "demo": has_demo},
            {"key": "deploy", "state": "done" if has_mcp else "empty"},
        ]

    # ---- ① 上传：原件落盘 + 全量校验 + 训练包（失败全量收集，人话返回）
    def upload(self, records: dict, fieldmap: dict, pairs: dict | None = None) -> dict:
        errs: list[str] = []
        rec_path = self._save("records", records, errs)
        fm_path = self._save("fieldmap", fieldmap, errs)
        pairs_path = self._save("pairs", pairs, errs) if pairs else None
        if errs:
            raise ConsoleError(errs)
        # 原件已写（便于改后重传）；校验失败不清工作区——报错带行号，改完再传
        try:
            return intake.run_intake(rec_path, fm_path, pairs_path,
                                     out=self.root / "package")
        except IntakeError as e:
            msgs = e.args[0] if e.args and isinstance(e.args[0], list) else [str(e)]
            raise ConsoleError([str(m) for m in msgs])

    def _save(self, slug: str, f: dict, errs: list[str]) -> Path:
        missing = self.root / "uploads" / f".{slug}.missing"
        if not isinstance(f, dict):           # 缺键/非对象载荷：人话报错，不裸崩
            errs.append(f"{slug}: missing or invalid file payload")
            return missing
        name = str(f.get("name") or "").strip()
        if not name:
            errs.append(f"{slug}: missing file name")
            return missing
        if "/" in name or "\\" in name or name.startswith("."):
            errs.append(f"{slug}: illegal file name {name!r}")
            return missing
        try:
            data = base64.b64decode(str(f.get("b64") or ""), validate=True)
        except (binascii.Error, ValueError):
            errs.append(f"{slug} ({name}): content is not valid base64")
            return missing
        if not data:
            errs.append(f"{slug} ({name}): file is empty")
            return missing
        if len(data) > MAX_UPLOAD_BYTES:
            errs.append(f"{slug} ({name}): {len(data):,} bytes exceeds the "
                        f"{MAX_UPLOAD_BYTES:,}-byte limit — split and upload in batches")
            return missing
        p = self.root / "uploads" / name
        p.write_bytes(data)
        return p

    # ---- 上传原件定位：按 intake_report.source 的文件名（不猜扩展名）
    def _find(self, slug: str) -> Path:
        report = self.root / "package" / "intake_report.json"
        if not report.exists():
            raise ConsoleError(["workspace has no validated upload yet "
                                "（先完成数据上传）"])
        name = json.loads(report.read_text(encoding="utf-8")).get(
            "source", {}).get(slug)
        p = self.root / "uploads" / str(name or "")
        if name and p.exists():
            return p
        raise ConsoleError([f"upload original for {slug!r} not found in workspace"])

    def _loaded(self) -> tuple[list, list]:
        return (load_fieldmap(self._find("fieldmap")),
                load_records(self._find("records"))[1])

    # ---- ② 初版展示：记录表 + 序列化文本（与训练同口径）
    def preview(self) -> dict:
        fields, rows = self._loaded()
        emb = [fs for fs in fields if fs.role == "embedding"]
        out = []
        for _, rec in rows[:PREVIEW_LIMIT]:
            disp = {fs.name: (apply_normalize(_to_text(rec.get(fs.name)),
                                              fs.normalize) or None) for fs in emb}
            out.append({
                "rid": rec["record_id"],
                "entity": rec.get("entity_id"),
                "name": next((v for v in disp.values() if v), rec["record_id"]),
                "fields": disp,
                "text": serialize_record(rec, fields),
            })
        return {"n": len(rows), "shown": len(out), "rows": out,
                "fields": [dict(fs.label_dict(), name=fs.name) for fs in emb]}

    # ---- ② 训练前基线代理打分（字面相似度；诚实标注非模型）
    def proxy_score(self, a: str, b: str) -> dict:
        fields, rows = self._loaded()
        rec_map = {rec["record_id"]: rec for _, rec in rows}
        for rid in (a, b):
            if rid not in rec_map:
                raise ConsoleError([f"unknown rid {rid!r} "
                                    f"（workspace holds {len(rec_map)} records）"])
        emb = [fs for fs in fields if fs.role == "embedding"]
        weights = {fs.name: (fs.baseline_weight or 1.0) for fs in emb}

        def norm(rec: dict) -> dict:
            return {fs.name: (apply_normalize(_to_text(rec.get(fs.name)),
                                              fs.normalize) or None) for fs in emb}

        fa, fb = norm(rec_map[a]), norm(rec_map[b])
        return {"a": a, "b": b,
                "score": round(baseline_score(fa, fb, weights), 4),
                "fields": [{"label": fs.label_dict(), "name": fs.name,
                            "a": fa[fs.name], "b": fb[fs.name],
                            "state": ("both_missing" if not fa[fs.name] and not fb[fs.name]
                                      else "missing" if not fa[fs.name] or not fb[fs.name]
                                      else "same" if fa[fs.name] == fb[fs.name]
                                      else "diff")} for fs in emb],
                "text_a": serialize_record(rec_map[a], fields),
                "text_b": serialize_record(rec_map[b], fields)}

    # ---- ④ 效果展示：cases 产物关键数字（θ/断言/误连/漏连，含基线对照）
    def summary(self) -> dict | None:
        p = self.root / "cases.json"
        if not p.exists():
            return None
        c = json.loads(p.read_text(encoding="utf-8"))
        s, b = c.get("stats", {}), c.get("baseline", {})
        recs = c.get("records", {})
        values = recs.values() if isinstance(recs, dict) else recs
        return {
            "model": c.get("model"), "theta": c.get("theta"),
            "records": len(recs),
            "entities": len({r.get("entity") for r in values if isinstance(r, dict)}),
            "asserted": s.get("asserted"), "tp": s.get("tp"),
            "false_links": s.get("false_links"), "missed_pairs": s.get("missed_pairs"),
            "components": len(c.get("components", [])),
            "baseline_theta": b.get("theta"),
            "baseline_asserted": (b.get("stats") or {}).get("asserted"),
            "baseline_false": (b.get("stats") or {}).get("false_links"),
            "baseline_missed": (b.get("stats") or {}).get("missed_pairs"),
        }

    # ---- ④ 网络三态速览（原始纠缠 → 基线代理 → 微调；cases.network + baseline.network）
    def network(self) -> dict:
        p = self.root / "cases.json"
        if not p.exists():
            raise ConsoleError(["还没有效果产物（先完成模型训练）——网络速览无从画起"])
        c = json.loads(p.read_text(encoding="utf-8"))
        net = c.get("network") or {}
        bl = c.get("baseline") or {}
        bn = bl.get("network") or {}
        if not (net.get("nodes_before") and net.get("nodes_after")
                and bn.get("nodes_after")):
            raise ConsoleError(["cases.json 里没有网络三态数据——用新版 "
                                "build_tenant_cases.py 重建产物（或把产物拷全）再试"])

        def pairs(seq):            # "ridA|ridB" → [a, b]
            out = []
            for s in seq or []:
                a, _, b = str(s).partition("|")
                out.append([a, b])
            return out

        def links(seq):            # {a,b,s} → [a, b]（速览不带分数，数字在指标卡）
            return [[e.get("a"), e.get("b")] for e in seq or []
                    if isinstance(e, dict) and e.get("a") and e.get("b")]

        def halos(entities, members, purity):
            out = []
            for e in entities or []:
                mem = (members or {}).get(e.get("e")) or []
                if len(mem) < 2:
                    continue
                out.append({"x": e.get("x"), "y": e.get("y"),
                            "r": 16 + 7 * len(mem),
                            "mixed": not purity.get(e.get("e"), True)})
            return out

        return {
            "canvas": net.get("canvas") or {"w": 1200, "h": 780},
            "shadow": len(net.get("edges_before") or []),
            "stages": [
                {"key": "before",
                 "nodes": net.get("nodes_before") or [],
                 "edges": [[e.get("a"), e.get("b")]
                           for e in net.get("edges_before") or []]},
                {"key": "baseline", "theta": bl.get("theta"),
                 "nodes": bn.get("nodes_after") or [],
                 "links": links(bl.get("links")),
                 "false": pairs(bl.get("false_links")),
                 "missed": pairs(bl.get("missed_pairs")),
                 "halos": halos(bn.get("entities"), bn.get("comp_members"),
                                {x.get("id"): x.get("pure", True)
                                 for x in bl.get("components") or []})},
                {"key": "tuned", "theta": c.get("theta"),
                 "nodes": net.get("nodes_after") or [],
                 "links": links(c.get("links")),
                 "false": pairs(c.get("false_links")),
                 "missed": pairs(c.get("missed_pairs")),
                 "halos": halos(net.get("entities"), net.get("comp_members"),
                                {x.get("id"): x.get("pure", True)
                                 for x in c.get("components") or []})},
            ],
        }

    # ---- 汇总（GET /api/state 一次给全；api_* 由 HTTP 层注入展示信息）
    def state(self, label: str, api_url: str | None = None,
              api_key: str | None = None) -> dict:
        report_p = self.root / "package" / "intake_report.json"
        report = (json.loads(report_p.read_text(encoding="utf-8"))
                  if report_p.exists() else None)
        bundle = self.root / "mcp_bundle"
        # is_dir 而非 exists：路径被误放成文件时视为没有交付包，不裸崩
        files = sorted(bundle.iterdir()) if bundle.is_dir() else []
        mcp = [p.name for p in files if p.is_file()]
        return {"label": label, "steps": self.steps(), "report": report,
                "preview": self.preview() if report else None,
                "summary": self.summary(), "mcp_files": mcp,
                # 逐文件字节数：⑤ 页 CTA 显示「N 个文件 · X KB」（体积是交付元数据）
                "mcp_bytes": {p.name: p.stat().st_size for p in files if p.is_file()},
                "has_demo": (self.root / "demo.html").exists(),
                "net": (self.root / "cases.json").exists(),
                "api": {"url": api_url, "key": api_key}}


# ---------------------------------------------------------------- 页面（同源服务，零外链）

_HEAD = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<script>
/* 主题先行（与演示页同一套约定）：localStorage > ?theme= > 浅色 */
(function(){
  var t = localStorage.getItem('ea_theme') ||
    (location.search.match(/theme=(dark|light)/)||[])[1] || 'light';
  document.documentElement.dataset.theme = t;
})();
</script>
<style>__CSS__</style>
</head>
<body>
<header><div class="hbar">
  <div class="logo"><span class="mark">◆ ENTITY RESOLUTION</span><span class="zh">SERVICE CONSOLE</span></div>
  <span class="pill" id="wsPill">__LABEL__</span>
  <span style="flex:1"></span>
  <!-- 回能力演示页：相对路径 ../（零 URL 字符串），默认 hidden——
       由同源探测点亮（与演示页→控制台入口互为对称）：
       只有挂在一体化 er_server 下（../healthz 存在）才亮；
       单独跑 er_console（自己就是根）时探测 404，保持隐藏不留死链 -->
  <a class="lang" id="demoLink" href="../" hidden>能力演示 ↗</a>
  <button class="lang" id="themeBtn" aria-label="theme">☾</button>
  <button class="lang" id="langBtn">中文</button>
</div></header>
<main class="cwrap">
  <aside class="cside" id="cside"></aside>
  <section class="cmain" id="cmain">loading…</section>
</main>
<script>__JS__</script>
</body>
</html>
"""

_EXTRA_CSS = """
/* ---- 控制台布局（只加不改，token 全走 theme.py）---- */
.cwrap { display:grid; grid-template-columns:216px 1fr; gap:0; max-width:1240px;
  margin:0 auto; padding:20px 24px 60px; min-height:calc(100vh - 52px); }
.cside { border-right:1px solid var(--line); padding-right:14px; }
.cside h3 { font-size:10.5px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--t3); margin:0 0 6px; font-weight:650; }
.cside button.snav { display:flex; align-items:center; gap:8px; width:100%;
  text-align:left; background:none; border:none; color:var(--t2); font-size:12.5px;
  padding:7px 10px; border-radius:var(--r-s); cursor:pointer; font-family:inherit; }
.cside button.snav:hover { background:var(--hover); color:var(--t1); }
.cside button.snav.active { background:var(--navsel); color:var(--t1); font-weight:600; }
.sdot { width:8px; height:8px; border-radius:50%; flex:none;
  background:var(--line2); }
.sdot.done { background:var(--cyan); }
.sdot.ready { background:none; border:2px solid var(--cyan); }  /* 就绪≠完成：空心 */
.sdot.waiting { background:var(--t3); }
.cmain { padding-left:22px; min-width:0; }
.cmain h1 { font-size:22px; margin:2px 0 4px; letter-spacing:-.01em; }
.cmain .sub { color:var(--t3); font-size:12.5px; margin:0 0 18px; line-height:1.6; }
.stepgrid { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin:14px 0 22px; }
.stepcard { border:1px solid var(--line); border-radius:var(--r-m); padding:12px 12px 10px;
  background:var(--panel); position:relative; }
.stepcard .n { font-size:10px; color:var(--t3); letter-spacing:.08em; }
.stepcard .t { font-size:12.5px; font-weight:650; margin:2px 0 6px; color:var(--t1); }
.stepcard .s { font-size:11px; color:var(--t3); }
.stepcard.on { border-color:rgba(72,197,208,.5); }
.stepcard.on::before { content:''; position:absolute; left:-1px; top:-1px; bottom:-1px;
  width:3px; border-radius:var(--r-m) 0 0 var(--r-m); background:var(--sig); }
.numgrid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }
.numcard { border:1px solid var(--line); border-radius:var(--r-m); padding:12px 14px; background:var(--panel); }
.numcard .v { font-size:24px; font-weight:700; letter-spacing:-.02em; }
.numcard .v small { font-size:12px; font-weight:500; color:var(--t3); }
.numcard .k { font-size:11px; color:var(--t3); margin-top:2px; line-height:1.45; }
.drop { border:1.5px dashed var(--line2); border-radius:var(--r-m); padding:12px 16px;
  margin-bottom:10px; cursor:pointer; display:flex; gap:14px; align-items:center;
  flex-wrap:wrap; transition:border-color .15s, background .15s; }
.drop:hover, .drop.over { border-color:var(--cyan); background:rgba(72,197,208,.05); }
.drop:focus-visible { outline:2px solid var(--cyan); outline-offset:2px; }
.drop .dt { font-size:12.5px; font-weight:650; }
.drop .dh { font-size:11px; color:var(--t3); margin-top:1px; }
.drop .fn { font-family:var(--mono); font-size:11.5px; color:var(--cyan-b);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:360px; }
.drop .clr { margin-left:auto; background:none; border:none; color:var(--t3);
  cursor:pointer; font-size:13px; padding:2px 6px; }
.drop .clr:hover { color:var(--red-b); }
.samplerow { display:flex; gap:12px; align-items:center; margin:0 0 14px; flex-wrap:wrap; }
.cfggrid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
  gap:12px; margin-top:10px; }
.cfggrid label { display:flex; flex-direction:column; gap:5px; font-size:11.5px;
  color:var(--t3); }
.phchip { display:inline-block; font-size:11px; font-weight:650; padding:2px 10px;
  border-radius:99px; border:1px solid var(--line2); color:var(--t2);
  background:var(--panel2); vertical-align:1px; }
.phchip.bad { color:var(--red-b); border-color:rgba(207,109,88,.5);
  background:rgba(207,109,88,.08); }
.btn { background:var(--sig); color:var(--on-sig); border:none; border-radius:var(--r-s);
  padding:8px 18px; font-size:12.5px; font-weight:650; cursor:pointer; font-family:inherit;
  display:inline-block; text-decoration:none; }
.btn:disabled { opacity:.45; cursor:not-allowed; }
.btn.ghost { background:none; border:1px solid var(--line2); color:var(--t2); }
.ctable { width:100%; border-collapse:collapse; font-size:12px; }
.ctable th { text-align:left; color:var(--t3); font-weight:600; padding:6px 8px;
  border-bottom:1px solid var(--line); font-size:11px; white-space:nowrap; }
.ctable td { padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
.ctable tr:hover td { background:var(--hover); }
.mono { font-family:var(--mono); font-size:11.5px; }
.fdiff { width:100%; border-collapse:collapse; font-size:12px; }
.fdiff td, .fdiff th { padding:5px 8px; border-bottom:1px solid var(--line); text-align:left; }
.fdiff .st { font-size:10.5px; padding:1px 7px; border-radius:99px; white-space:nowrap; }
.fdiff .st.same { color:var(--cyan-b); background:rgba(72,197,208,.12); }
.fdiff .st.diff { color:var(--red-b); background:rgba(207,109,88,.12); }
.fdiff .st.missing { color:var(--t3); background:var(--panel2); }
.scorebar { height:10px; border-radius:99px; background:var(--panel2); overflow:hidden;
  border:1px solid var(--line); }
.scorebar i { display:block; height:100%; background:var(--sig); border-radius:99px; }
.errbox { border:1px solid rgba(207,109,88,.5); background:rgba(207,109,88,.08);
  border-radius:var(--r-m); padding:10px 14px; font-size:12px; color:var(--t2);
  margin:10px 0; line-height:1.7; }
.okbox { border:1px solid rgba(72,197,208,.45); background:rgba(72,197,208,.07);
  border-radius:var(--r-m); padding:10px 14px; font-size:12px; color:var(--t2); margin:10px 0; }
.empty { border:1px dashed var(--line2); border-radius:var(--r-m); padding:26px 22px;
  color:var(--t3); font-size:12.5px; line-height:1.8; }
.klist { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:8px; margin:12px 0; }
/* ⑤ 部署：zip 主 CTA 一行 + 注册命令代码块 + 清单行内小下载钮 */
.dprow { display:flex; align-items:center; gap:12px; margin:12px 0; flex-wrap:wrap; }
.dpzip { padding:9px 16px; font-size:13px; }
.codebox { display:flex; align-items:center; gap:8px; background:var(--panel2);
  border:1px solid var(--line); border-radius:var(--r-s); padding:9px 11px; margin-top:8px; }
.codebox code { flex:1; font-size:11.5px; line-height:1.6; overflow-wrap:anywhere; }
.copybtn { background:var(--panel2); border:1px solid var(--line2); color:var(--t2);
  border-radius:var(--r-s); padding:4px 10px; font-size:11px; cursor:pointer;
  font-family:inherit; white-space:nowrap; }
.copybtn:hover { border-color:var(--cyan); color:var(--t1); }
.copybtn:disabled { opacity:.6; cursor:default; }
.filelink.dponly { padding:4px 9px; }
.filelink { display:flex; justify-content:space-between; gap:8px; border:1px solid var(--line);
  border-radius:var(--r-s); padding:7px 11px; font-size:12px; color:var(--t2);
  text-decoration:none; }
.filelink:hover { border-color:var(--cyan); color:var(--t1); }
select, input[type=text] { background:var(--panel2); border:1px solid var(--line);
  color:var(--t1); border-radius:var(--r-s); padding:6px 8px; font-size:12px;
  font-family:inherit; max-width:100%; }
.pline { display:flex; gap:10px; align-items:flex-start; padding:7px 0; font-size:12.5px; }
/* ④ 效果对比卡：角色色左线（cyan=微调 / violet=基线代理，视觉语言同 stepcard.on）*/
.cmp { position:relative; }
.cmp.cy { border-color:rgba(72,197,208,.5); }
.cmp.vi { border-color:rgba(120,123,210,.5); }
.cmp::before { content:''; position:absolute; left:-1px; top:-1px; bottom:-1px;
  width:3px; border-radius:var(--r-m) 0 0 var(--r-m); }
.cmp.cy::before { background:var(--cyan); }
.cmp.vi::before { background:var(--violet); }
/* ④ 网络三态（可交互）：阶段页签 + 大画布 + 悬停/锁定（角色色同全页约定）*/
.stabs { display:flex; gap:8px; margin:10px 0 12px; flex-wrap:wrap; }
.stab { background:var(--panel2); border:1px solid var(--line); border-radius:var(--r-s);
  padding:6px 12px; font-size:12px; color:var(--t2); cursor:pointer; font-family:inherit; }
.stab:hover { border-color:var(--cyan); color:var(--t1); }
.stab.active { background:var(--sig); color:var(--on-sig); border-color:transparent;
  font-weight:650; }
.stab .num { font-size:10.5px; opacity:.8; margin-left:5px; font-family:var(--mono); }
.netwrapc { position:relative; }  /* 别叫 netwrap：theme.py 那是演示页 1fr+270px 双栏，会撞名 */
.netcv { border:1px solid var(--line); border-radius:var(--r-s); background:var(--panel2);
  overflow:hidden; }
.netcv svg { display:block; width:100%; height:auto; }  /* 高随 viewBox 宽高比——固定高会留两侧死白 */
.netcv circle[data-rid] { cursor:pointer; }
.netcv line { transition:opacity .15s, stroke-width .1s; }
.netcv line.hl { stroke-width:3.5; }
@keyframes netfade { from { opacity:.25; } to { opacity:1; } }
.netcv.fadein { animation:netfade .28s ease-out; }
.dim { opacity:.1; }
.nettip { position:absolute; display:none; pointer-events:none; background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-s); padding:5px 9px; font-size:11px;
  font-family:var(--mono); color:var(--t1); z-index:5; max-width:360px;
  box-shadow:var(--shadow-1); }
.netcap { display:flex; justify-content:space-between; gap:10px; font-size:11px;
  color:var(--t3); margin-top:8px; flex-wrap:wrap; }
.nshadow { stroke:var(--edge); stroke-opacity:.7; stroke-width:1; }  /* 与演示页影子边同 token 同可见度 */
.nlink-c { stroke:var(--cyan); stroke-width:2; }
.nlink-v { stroke:var(--violet); stroke-width:2; }
.nbad { stroke:var(--red); stroke-width:2; }
.nhalo { fill:rgba(72,197,208,.10); stroke:rgba(72,197,208,.45); stroke-width:1; }
.nhalo-bad { fill:rgba(207,109,88,.10); stroke:rgba(207,109,88,.45); stroke-width:1; }
.ndot { fill:var(--t2); stroke:var(--panel2); stroke-width:1; }
.netleg { display:flex; flex-wrap:wrap; gap:14px; font-size:11px; color:var(--t3); margin-top:9px; }
.netleg .k { display:inline-flex; align-items:center; gap:5px; }
.netleg .sw { width:14px; border-top:2px solid; display:inline-block; }
.netleg .sw.dash { border-top-style:dashed; }
/* theme.py 只锁 :focus-visible 不锁链接色——控制台正文链接统一角色色（双主题 token）*/
.cmain a { color:var(--cyan-b); text-decoration:underline;
  text-decoration-color:var(--cyan-l); text-underline-offset:3px; }
.cmain a:hover { text-decoration-thickness:2px; }
.cmain a.filelink, .cmain a.btn { text-decoration:none; }
/* 链接形态的实心按钮必须显式夺回字色：上面 .cmain a 的 cyan-b 会以
   0,1,1 赢过 .btn 的 0,1,0，深色主题下亮青文字落在亮渐变上≈1.2:1 直接消失
   （ghost 除外：透明底上 on-sig 近黑同样不可读，保持 t2） */
.cmain a.btn:not(.ghost) { color:var(--on-sig); }
/* 顶栏回演示页链接：借 .lang 钮件形态，去掉锚默认下划线与按钮一致 */
#demoLink { text-decoration:none; }
"""

_JS = r"""
'use strict';
/* ============ 服务控制台 SPA（同源 fetch，零外链；主题/语言与演示页同约定） ============ */
let LANG = localStorage.getItem('ea_lang') ||
  ((navigator.language || 'en').toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en');

const I18N = {
  'app.title':   {en:'ER Service Console', zh:'实体归并 · 服务控制台'},
  'top.demo':    {en:'Capability demo ↗', zh:'能力演示 ↗'},
  'nav.overview':{en:'Overview', zh:'总览'},
  /* 导航标签不带步号圆标（①③④⑤ 太丑，视觉审查否决）：步骤序号语义
     由总览步骤卡的 01–05 眉标承担，左侧导航只留名称 + 状态点 */
  'nav.data':    {en:'Data', zh:'数据'},
  'nav.upload':  {en:'Data upload', zh:'数据上传'},   /* 总览步骤卡仍用 */
  'nav.preview': {en:'First look', zh:'初版展示'},    /* 总览步骤卡仍用 */
  'nav.train':   {en:'Training', zh:'模型训练'},
  'nav.effect':  {en:'Results', zh:'效果展示'},
  'nav.deploy':  {en:'Deployment', zh:'服务部署'},
  'st.empty':    {en:'not started', zh:'未开始'},
  'st.ready':    {en:'ready', zh:'就绪'},
  'st.waiting':  {en:'waiting', zh:'等待中'},
  'st.done':     {en:'done', zh:'完成'},
  'ov.h':  {en:'From your data to a live ER service', zh:'从你的数据到可用的归并服务'},
  'ov.sub': {en:'Five steps, one workspace. Everything here is computed from your uploaded records — validation, split and the pre-training baseline run on this workspace; model training runs on our fine-tuning platform, and the results and deployment pages light up automatically when the trained artifacts land.', zh:'五个步骤、一个工作区。这里的一切都由你上传的记录计算而来——校验、切分与训练前基线在本工作区实时完成；模型训练在我们的微调平台执行，训练产物落回后，效果与部署页自动点亮。'},
  'ov.now': {en:'Workspace', zh:'工作区'},
  'ov.next': {en:'Next action', zh:'下一步'},
  'ov.next.upload':  {en:'Upload records + field map to start', zh:'上传记录 + 字段角色表，从这里开始'},
  'ov.next.train':   {en:'Download the training package — our operator runs the platform fine-tune', zh:'下载训练上传包——操作员在平台执行微调'},
  'ov.next.effect':  {en:'Review the fine-tuned results on your data', zh:'查看微调模型在你数据上的效果'},
  'ov.next.deploy':  {en:'Pick up the MCP delivery bundle and your scoring API key', zh:'领取 MCP 交付包与打分 API key'},
  'gen.empty': {en:'Not ready yet — complete the earlier steps first:', zh:'尚未就绪——请先完成前置步骤：'},
  'up.h':   {en:'Upload your data', zh:'上传你的数据'},
  'up.sub': {en:'Records (CSV/JSONL), field-role map (CSV), optional pair labels (CSV). Validation is total: every problem is listed with its line number — nothing fails silently. Files stay in this workspace.', zh:'记录（CSV/JSONL）、字段角色表（CSV）、可选逐对标签（CSV）。校验是全量的：每个问题带行号列出，绝不静默。文件只留在本工作区。'},
  'up.rec':   {en:'Records *', zh:'记录文件 *'},
  'up.fm':    {en:'Field map *', zh:'字段角色表 *'},
  'up.pairs': {en:'Pair labels (optional · format B)', zh:'逐对标签（可选 · 格式 B）'},
  'up.cur': {en:'The file names above are what this workspace currently uses — pick new files to replace them.', zh:'槽位里显示的是本工作区当前生效的文件——重新选择即覆盖。'},
  'up.go':  {en:'Validate & build package', zh:'校验并构建训练包'},
  'up.ok':  {en:'Validation passed — training package built.', zh:'校验通过——训练包已构建。'},
  'up.rep.h': {en:'Intake report', zh:'接入校验报告'},
  'data.h': {en:'Data — upload & first look', zh:'数据 · 上传与初版'},
  'data.sub': {en:'One page for the whole data step: upload (or load the Febrl sample), see the intake report, browse your records exactly as training will see them, and try the pre-training baseline scorer.', zh:'数据这一步全在一页：上传（或一键载入 Febrl 示例）、看接入校验报告、按训练同口径浏览记录、试打训练前基线分。'},
  'up.pick': {en:'click or drop a file here', zh:'点击选择或拖拽文件到此处'},
  'up.none': {en:'no file chosen', zh:'未选择文件'},
  'up.sample': {en:'Load Febrl sample data', zh:'载入 Febrl 示例数据'},
  'up.sample.h': {en:'public benchmark · no real PII · fastest way to try the console end to end', zh:'公开基准 · 无真实个人数据 · 最快跑通全流程的方式'},
  'up.sample.busy': {en:'loading sample…', zh:'示例数据载入中…'},
  'train.h':   {en:'Model training', zh:'模型训练'},   /* 与左导航 nav.train 同名（旧「平台训练」像内部运维用语，且客户会当成另一个页面） */
  'train.sub': {en:'Direct pipeline: this console uploads your training package to the INF retriever platform, starts the fine-tune, deploys the trained model and scores every record pair — θ is picked automatically by the F1 scan. No hopping between interfaces. The manual fallback (download the zip, operator-mediated) stays available when no platform token is configured.', zh:'直连流水线：本控制台把训练包上传到 INF retriever 平台、开训、部署训练好的模型，并对全部记录对打分——θ 由 F1 扫描自动选定，不用在多个界面之间跳转。未配置平台令牌时保留人工兜底（下载 zip、操作员中介）。'},
  'train.sub.manual': {en:'v1 is human-in-the-loop by design: the package is submitted to the INF retriever fine-tuning platform (by you or our operator); training, evaluation and the θ scan run there; the artifacts land back in this workspace and the results and deployment pages light up automatically.', zh:'v1 有意保持人机协同：上传包提交到 INF retriever 微调平台（你或我们的操作员执行），训练、评测与 θ 扫描在平台完成；产物回到本工作区后，效果与部署页自动点亮。'},
  'train.cfg': {en:'Training config', zh:'训练配置'},
  'train.base': {en:'Base model', zh:'基座模型'},
  'train.out':  {en:'Output model name', zh:'输出模型名'},
  'train.out.ph': {en:'auto: <workspace>-er-<date>', zh:'自动：<工作区>-er-<日期>'},
  'train.serve': {en:'Serving name (optional)', zh:'服务名（可选）'},
  'train.start': {en:'Upload package & start training', zh:'上传训练包并开始训练'},
  'train.prog': {en:'Live status (auto-refresh)', zh:'实时进度（自动刷新）'},
  'train.view': {en:'view on platform ↗', zh:'在平台上查看 ↗'},
  'train.deploy': {en:'Deploy the trained model', zh:'部署训练好的模型'},
  'train.build': {en:'Score all pairs → build results (θ by F1 scan)', zh:'全对打分 → 生成效果数据（θ 由 F1 扫描自动选定）'},
  'train.build.busy': {en:'scoring every pair & building artifacts… (minutes)', zh:'全对打分并生成产物中…（分钟级）'},
  'train.done': {en:'Artifacts landed — open Results', zh:'产物已落位——去效果展示'},
  'train.retry': {en:'Retry', zh:'重试'},
  'train.svc': {en:'Deployed service', zh:'部署服务'},
  'train.warn': {en:'(platform status query failed — will keep retrying)', zh:'（平台状态查询失败——会继续重试）'},
  'ph.idle':      {en:'no package yet', zh:'还没有训练包'},
  'ph.ready':     {en:'package ready', zh:'训练包就绪'},
  'ph.training':  {en:'training on platform', zh:'平台训练中'},
  'ph.trained':   {en:'training finished', zh:'训练完成'},
  'ph.deploying': {en:'deploying', zh:'部署中'},
  'ph.deployed':  {en:'deployed', zh:'已部署'},
  'ph.building':  {en:'scoring & building results', zh:'打分并生成效果数据'},
  'ph.done':      {en:'results ready', zh:'效果就绪'},
  'ph.failed':    {en:'failed', zh:'失败'},
  'train.pkg': {en:'Training package — deterministic zip, ready for platform upload', zh:'训练上传包——确定性 zip，可直接上传平台'},
  'train.dl':  {en:'Download package.zip', zh:'下载 package.zip'},
  'train.steps.h': {en:'Pipeline', zh:'流水线'},
  'ts.pkg':    {en:'Package built (queries/corpus/qrels + hard negatives)', zh:'上传包就绪（queries/corpus/qrels + 硬负例）'},
  'ts.submit': {en:'Submit to platform → fine-tune (operator)', zh:'提交平台 → 微调（操作员）'},
  'ts.eval':   {en:'Evaluate on held-out split → pick θ by F1 scan', zh:'留出集评测 → F1 扫描定 θ'},
  'ts.back':   {en:'Artifacts land in workspace (cases.json → results page + MCP bundle)', zh:'产物落回工作区（cases.json → 效果页 + MCP 包）'},
  'pv.h':   {en:'First look at your data', zh:'初版展示 · 你的数据'},
  'pv.sub': {en:'Records serialized exactly as training will see them (field order from the map). Pick any two for a pre-training baseline score — literal field similarity, honestly labeled: NOT a model output, this is what "before fine-tuning" looks like.', zh:'记录按训练同口径序列化（字段序取自角色表）。任挑两条做训练前基线打分——字段字面相似度，诚实标注：非模型输出，这是"微调前"的样子。'},
  'pv.score': {en:'Score a pair (pre-training baseline)', zh:'给一对打分（训练前基线）'},
  'pv.go':    {en:'Score', zh:'打分'},
  'pv.proxy': {en:'Baseline proxy · literal similarity · NOT a model output', zh:'基线代理 · 字面相似度 · 非模型输出'},
  'pv.rec':  {en:'Records', zh:'记录'},
  'pv.search': {en:'Filter by rid / entity / text…', zh:'按 rid / 实体 / 文本过滤…'},
  'ef.h':   {en:'Results on your data', zh:'你的数据上的效果'},
  'ef.sub': {en:"Computed from the fine-tuned model's real pairwise scores (sampled from the deployed service). Baseline column is the literal-similarity proxy, shown for contrast.", zh:'全部由微调模型的真实成对分数计算（取样自部署服务）。基线列为字面相似度代理，供对照。'},
  'ef.open':  {en:'Open full results page', zh:'打开完整效果页'},
  'ef.tuned': {en:'Fine-tuned (real model)', zh:'微调（真实模型）'},
  'ef.base':  {en:'Baseline (proxy)', zh:'基线（代理）'},
  'ef.net.h': {en:'Effect at a glance — same records, three states', zh:'效果速览——同一批记录的三种状态'},
  'ef.net.s0': {en:'Raw records', zh:'原始记录'},
  'ef.net.s1': {en:'Baseline (proxy)', zh:'基线（代理）'},
  'ef.net.s2': {en:'Fine-tuned (real model)', zh:'微调（真实模型）'},
  'ef.net.shadow': {en:'look-alike tangles', zh:'影子相似纠缠'},
  'ef.net.leg.link': {en:'asserted link', zh:'断言连线'},
  'ef.net.leg.bad': {en:'false merge', zh:'误连'},
  'ef.net.leg.miss': {en:'missed true link', zh:'漏连（红虚线）'},
  'ef.net.leg.halo': {en:'merged island', zh:'归并组件'},
  'ef.net.hint': {en:'hover a record or line · click a record to pin', zh:'悬停记录或连线 · 点击记录锁定'},
  'ef.net.deg':  {en:'links in this stage', zh:'条连线（本阶段）'},
  'ef.net.k.shadow': {en:'look-alike tie', zh:'影子相似'},
  'ef.net.k.link':  {en:'asserted link', zh:'断言连线'},
  'ef.net.k.bad':   {en:'false merge', zh:'误连'},
  'ef.net.k.miss':  {en:'missed true link', zh:'漏连'},
  'lg.base':  {en:'baseline link', zh:'基线断言连线'},
  'ef.empty': {en:'No results yet — training artifacts have not landed. When they do, key numbers appear here and the full results page unlocks.', zh:'还没有效果数据——训练产物尚未落回。落库后这里会出现关键数字，完整效果页随之解锁。'},
  'dp.h':   {en:'Deploy & integrate', zh:'部署与接入'},
  'dp.sub': {en:'Two integration paths: the HTTP API via the multi-tenant gateway (per-tenant static key — the one shown below is yours), and an offline MCP bundle for your own AI client.', zh:'两条接入路径：多租户网关的 HTTP API（每租户静态 key——下表展示的就是发给你的），以及给你自己 AI 客户端的离线 MCP 交付包。'},
  'dp.sub.off': {en:'Your integration path for now is the offline MCP bundle (one zip, zero-config); the gateway HTTP API is not provisioned for this workspace yet.', zh:'当前接入路径：离线 MCP 交付包（一个 zip、零配置）；多租户网关的 HTTP API 尚未对本工作区开通。'},
  'dp.api':  {en:'HTTP API (gateway)', zh:'HTTP API（网关）'},
  'dp.api.off': {en:'Not provisioned. Once the multi-tenant gateway is set up for this workspace, your dedicated endpoint and static key appear here.', zh:'未开通。多租户网关为本工作区开通后，这里会展示你的专属 endpoint 与静态 key。'},
  'dp.api.nokey': {en:'Endpoint provisioned; the static key is issued separately — ask your operator.', zh:'endpoint 已开通；静态 key 另行发放——联系运营获取。'},
  'dp.mcp':  {en:'MCP delivery bundle (offline · zero-config)', zh:'MCP 交付包（离线 · 零配置）'},
  'dp.zip':  {en:'Download the bundle (.zip)', zh:'下载交付包（.zip）'},
  'dp.zip.n': {en:'{N} files · {K} KB · deterministic build', zh:'{N} 个文件 · {K} KB · 确定性打包'},
  'dp.cmd.h': {en:'Register in one line (Claude Code)', zh:'一行注册（Claude Code）'},
  'dp.cmd.note': {en:'After unpacking, replace <bundle> with the absolute path of the unpacked directory. Same content as START.md inside the zip.', zh:'解压后把 <bundle> 替换成解压目录的绝对路径。与 zip 内 START.md 同款命令。'},
  'dp.files.h': {en:'What’s inside', zh:'包内文件'},
  'dp.f.server':  {en:'MCP server (pure-stdlib Python ≥3.11)', zh:'MCP 服务器（纯标准库 Python ≥3.11）'},
  'dp.f.client':  {en:'Embeddings client layer (TLS1.2/retry, byte-identical copy)', zh:'embeddings 客户端共用层（TLS1.2/重试，原样拷贝）'},
  'dp.f.env':     {en:'Environment variables (source & run)', zh:'环境变量（source 后直接起服务器）'},
  'dp.f.meta':    {en:'Tenant metadata (labels/field table/θ)', zh:'租户口径元数据（标签/字段表/θ）'},
  'dp.f.records': {en:'Your records (rid/entity/fields/serialized text)', zh:'本数据集记录（rid/实体/字段/序列化文本）'},
  'dp.f.scores':  {en:'Pre-sampled full pairwise real scores (offline matrix)', zh:'预取样全量成对真实分数（离线矩阵）'},
  'dp.f.start':   {en:'Setup guide (registration & both scoring modes)', zh:'接入指南（注册方式与两种打分模式）'},
  'dp.empty': {en:'Delivery bundle not landed yet — it is generated together with the results artifacts after training.', zh:'交付包尚未就位——它与训练产出的效果物料一同生成。'},
  'dp.copy':  {en:'Copy', zh:'复制'},
  'dp.copied': {en:'Copied ✓', zh:'已复制 ✓'},
  'dp.done':  {en:'All five steps complete. After registering, ask your AI client er_about — a healthy bundle answers with model, θ and record count.', zh:'五步全部完成。注册完成后，向你的 AI 客户端问一句 er_about——健康的交付包会答出模型、θ 与记录数。'},
  'dp.done.k': {en:'Workspace complete', zh:'工作区已就绪'},
  'k.records':  {en:'records', zh:'条记录'},
  'k.file':  {en:'file', zh:'文件'},
  'k.role':  {en:'role', zh:'作用'},
  'k.size':  {en:'size', zh:'大小'},
  'k.entities': {en:'entities', zh:'个实体'},
  'k.pairs':    {en:'labeled positive pairs', zh:'带标正例对'},
  'k.fields':   {en:'embedding fields', zh:'个 embedding 字段'},
  'k.qrels':    {en:'labeled pairs (train/test)', zh:'带标对（训练/测试）'},
  'k.theta':    {en:'decision θ', zh:'判定线 θ'},
  'k.asserted': {en:'asserted links', zh:'断言连接'},
  'k.false':    {en:'false links', zh:'误连'},
  'k.missed':   {en:'missed true pairs', zh:'漏连真对'},
};
function t(k){ const e = I18N[k]; return e ? (e[LANG] || e.en) : k; }
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

/* ---- 状态与路由 ---- */
let S = null, VIEW = 'overview';
function stateOf(key){ const s = (S && S.steps || []).find(x => x.key === key); return s ? s.state : 'empty'; }

async function refresh(){
  const r = await fetch('api/state');
  if (!r.ok){ document.getElementById('cmain').innerHTML =
    '<div class="errbox">console state unavailable (HTTP ' + r.status + ')</div>'; return; }
  S = await r.json();
  render();
}
function route(){
  let v = (location.hash.match(/^#\/(overview|data|train|effect|deploy|upload|preview)/) || [])[1];
  if (v === 'upload' || v === 'preview') v = 'data';   /* 旧深链并入数据页 */
  VIEW = v || 'overview';
  if (VIEW !== 'train') trainPollStop();
  if (S) render(); else refresh();
}
window.addEventListener('hashchange', route);

/* ---- 渲染 ---- */
function render(){
  document.getElementById('langBtn').textContent = LANG === 'en' ? '中文' : 'EN';
  document.getElementById('demoLink').textContent = t('top.demo');
  document.title = t('app.title');
  document.getElementById('themeBtn').textContent =
    document.documentElement.dataset.theme === 'dark' ? '☀' : '☾';
  renderSide(); renderMain();
}
function renderSide(){
  const items = [['overview','nav.overview',null],['data','nav.data','upload'],
    ['train','nav.train','train'],['effect','nav.effect','effect'],
    ['deploy','nav.deploy','deploy']];
  let h = '<h3>' + esc(t('ov.now')) + '</h3>';
  for (const [k, key, dot] of items){
    const st = dot ? stateOf(dot) : 'done';
    h += '<button class="snav' + (VIEW === k ? ' active' : '') + '" data-v="' + k + '">' +
      '<span class="sdot ' + st + '"' + (dot ? '' : ' style="opacity:.35"') + '></span>' +
      '<span>' + esc(t(key)) + '</span></button>';
  }
  document.getElementById('cside').innerHTML = h;
  document.querySelectorAll('#cside button.snav').forEach(b =>
    b.addEventListener('click', function(){ location.hash = '#/' + b.dataset.v; }));
  document.getElementById('wsPill').textContent = S ? S.label : '';
}
function renderMain(){
  const el = document.getElementById('cmain');
  if (VIEW === 'overview') el.innerHTML = viewOverview();
  else if (VIEW === 'data'){ el.innerHTML = viewData(); bindData(); }
  else if (VIEW === 'train'){ el.innerHTML = viewTrain(); bindTrain(); }
  else if (VIEW === 'effect'){ el.innerHTML = viewEffect(); bindEffect(); }
  else if (VIEW === 'deploy'){ el.innerHTML = viewDeploy(); bindDeploy(); }
  if (VIEW !== 'train') trainPollStop();
}

/* ---- 总览 ---- */
function stepGrid(){
  const defs = [['upload','nav.upload'],['preview','nav.preview'],['train','nav.train'],
                ['effect','nav.effect'],['deploy','nav.deploy']];
  return '<div class="stepgrid">' + defs.map(function(d, i){
    const st = stateOf(d[0]);
    return '<div class="stepcard' + ((st === 'done' || st === 'ready') ? ' on' : '') + '">' +
      '<div class="n">0' + (i + 1) + '</div><div class="t">' + esc(t(d[1])) + '</div>' +
      '<div class="s">' + esc(t('st.' + (st === 'ready' ? 'done' : st))) + '</div></div>';
  }).join('') + '</div>';
}
function numcard(v, k){
  return '<div class="numcard"><div class="v">' + v + '</div><div class="k">' +
    esc(k) + '</div></div>';
}
function viewOverview(){
  let nextKey = 'upload';
  if (stateOf('upload') === 'done') nextKey = stateOf('train') === 'done' ? 'deploy' : 'train';
  let h = '<h1>' + esc(t('ov.h')) + '</h1><p class="sub">' + esc(t('ov.sub')) + '</p>' + stepGrid();
  if (S && S.report){
    const r = S.report;
    h += '<div class="numgrid">' +
      numcard(r.records, t('k.records')) + entCard(r) +
      numcard((r.embedding_fields || []).length, t('k.fields')) +
      numcard((r.qrels && r.qrels.train + ' / ' + r.qrels.test) || '—', t('k.qrels')) +
      '</div>';
  }
  if (S && S.summary){
    const s = S.summary;
    h += '<div class="numgrid" style="margin-top:10px">' +
      numcard(s.theta != null ? s.theta.toFixed(4) : '—', t('k.theta')) +
      numcard(s.asserted != null ? s.asserted : '—', t('k.asserted')) +
      numcard(s.false_links != null ? s.false_links : '—', t('k.false')) +
      numcard(s.missed_pairs != null ? s.missed_pairs : '—', t('k.missed')) +
      '</div>';
  }
  /* 下一步是全页最重要的动作，必须给 CTA 按钮形态（渐变实心钮），不能只埋一行文字链。
     按钮文案直接复用 nav.<step>（与左侧导航逐字一致——点击去向可预测）；
     a.btn 实心形态的字色由 .cmain a.btn:not(.ghost) 兜底（见 console CSS 注释，别删） */
  h += '<p class="sub" style="margin-top:20px"><b>' + esc(t('ov.next')) + ':</b> ' +
    esc(t('ov.next.' + nextKey)) + '</p>' +
    '<a class="btn" href="#/' + routeOf(nextKey) + '" style="display:inline-block;margin:12px 0 2px">' +
    esc(t('nav.' + nextKey)) + ' →</a>';
  return h;
}
function routeOf(stepKey){ return (stepKey === 'upload' || stepKey === 'preview') ? 'data' : stepKey; }
function entCard(r){
  if (r.entities != null) return numcard(r.entities, t('k.entities'));
  if (r.positive_pairs != null) return numcard(r.positive_pairs, t('k.pairs'));
  return numcard('—', t('k.entities'));
}

/* ---- ①② 数据页（上传 + 初版展示合一；自定义拖放区，无原生「选择文件」铬件）---- */
const PICKED = {};   /* slot → File（提交时才读，页面刷新即弃） */
function dropSlot(id, labelKey, srcKey){
  const base = ((S && S.report && S.report.source) || {})[srcKey];
  const picked = PICKED[srcKey];
  const name = picked ? picked.name : base;
  return '<div class="drop" id="' + id + '" data-slot="' + srcKey + '" tabindex="0">' +
    '<div><div class="dt">' + esc(t(labelKey)) + '</div>' +
    '<div class="dh">' + esc(t('up.pick')) + '</div></div>' +
    '<div class="fn" id="' + id + 'n">' + (name ? esc(name) : esc(t('up.none'))) + '</div>' +
    '<button class="clr" id="' + id + 'x" title="clear" style="' +
    (picked ? '' : 'display:none') + '">✕</button>' +
    '<input type="file" id="' + id + 'i" hidden></div>';
}
function viewData(){
  let h = '<h1>' + esc(t('data.h')) + '</h1><p class="sub">' + esc(t('data.sub')) + '</p>' +
    '<div class="samplerow"><button class="btn ghost" id="sampleBtn">✦ ' +
    esc(t('up.sample')) + '</button><span class="sub" style="margin:0">' +
    esc(t('up.sample.h')) + '</span><span class="sub" id="sampleBusy" style="margin:0"></span></div>' +
    '<div class="card"><div class="sh">' + esc(t('up.h')) + '</div>' +
    '<p class="sub" style="margin:8px 0 12px">' + esc(t('up.sub')) + '</p>' +
    dropSlot('fRec', 'up.rec', 'records') + dropSlot('fFm', 'up.fm', 'fieldmap') +
    dropSlot('fPairs', 'up.pairs', 'pairs') +
    '<p class="sub" style="margin:2px 0 10px">' + esc(t('up.cur')) + '</p>' +
    '<div style="display:flex;gap:10px;margin:6px 0 2px;align-items:center">' +
    '<button class="btn" id="upGo">' + esc(t('up.go')) + '</button>' +
    '<span id="upBusy" class="sub" style="margin:0"></span></div>' +
    '<div id="upMsg"></div></div>';
  if (S && S.report) h += reportHtml(S.report);
  if (S && S.preview) h += previewCard();
  return h;
}
function previewCard(){
  const p = S.preview;
  const opt = function(r){ return '<option value="' + esc(r.rid) + '">' +
    esc(r.rid) + ' · ' + esc(r.name) + '</option>'; };
  return '<div class="card" style="margin-top:14px"><div class="sh">' + esc(t('pv.h')) + '</div>' +
    '<p class="sub" style="margin:6px 0 0">' + esc(t('pv.sub')) + '</p></div>' +
    '<div class="card" style="margin-top:14px"><div class="sh">' + esc(t('pv.score')) + '</div>' +
    '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0">' +
    '<select id="pvA">' + p.rows.map(opt).join('') + '</select>' +
    '<select id="pvB">' + p.rows.map(opt).join('') + '</select>' +
    '<button class="btn ghost" id="pvGo">' + esc(t('pv.go')) + '</button></div>' +
    '<div id="pvOut"></div></div>' +
    '<div class="card" style="margin-top:14px"><div class="sh">' + esc(t('pv.rec')) +
    ' <span class="mono" style="color:var(--t3)">' + p.shown + ' / ' + p.n + '</span></div>' +
    '<input type="text" id="pvQ" placeholder="' + esc(t('pv.search')) +
    '" style="width:280px;margin:8px 0">' +
    '<div style="overflow:auto"><table class="ctable" id="pvTbl"><tr><th>rid</th><th>entity</th><th>serialized</th></tr>' +
    p.rows.map(function(r){
      return '<tr data-q="' + esc((r.rid + ' ' + r.entity + ' ' + r.text).toLowerCase()) +
        '"><td class="mono">' + esc(r.rid) + '</td><td class="mono">' + esc(r.entity) +
        '</td><td class="mono" style="color:var(--t2)">' + esc(r.text) + '</td></tr>';
    }).join('') + '</table></div></div>';
}
function reportHtml(r){
  const flds = (r.fields || []).map(function(f){
    return '<tr><td class="mono">' + esc(f.field) + '</td><td>' + esc(f.role) + '</td><td>' +
      esc(f.normalize || '—') + '</td><td>' + (f.neg_key ? '✓' : '') + '</td></tr>';
  }).join('');
  const warn = (r.warnings || []).map(function(w){ return '<div>⚠ ' + esc(w) + '</div>'; }).join('');
  return '<div class="card" style="margin-top:14px"><div class="sh">' + esc(t('up.rep.h')) + '</div>' +
    '<div class="okbox" style="margin-top:10px">' + esc(t('up.ok')) + '</div>' +
    '<div class="numgrid">' +
    numcard(r.records, t('k.records')) + entCard(r) +
    numcard((r.qrels && r.qrels.train + ' / ' + r.qrels.test) || '—', t('k.qrels')) +
    numcard((r.negatives && r.negatives.mined) || 0, 'hard negatives') +
    '</div><table class="ctable" style="margin-top:12px"><tr><th>field</th><th>role</th>' +
    '<th>normalize</th><th>neg_key</th></tr>' + flds + '</table>' +
    (warn ? '<div class="errbox" style="margin-top:10px">' + warn + '</div>' : '') +
    '</div>';
}
function wireDrop(id){
  const zone = document.getElementById(id), inp = document.getElementById(id + 'i'),
        slot = zone.dataset.slot;
  const set = function(f){
    PICKED[slot] = f || undefined;
    document.getElementById(id + 'n').textContent = f ? f.name : t('up.none');
    document.getElementById(id + 'x').style.display = f ? '' : 'none';
  };
  const base = ((S && S.report && S.report.source) || {})[slot];
  zone.addEventListener('click', function(){ inp.click(); });
  zone.addEventListener('keydown', function(e){ if (e.key === 'Enter') inp.click(); });
  inp.addEventListener('change', function(){ set(inp.files[0] || null); });
  ['dragover', 'dragenter'].forEach(function(ev){
    zone.addEventListener(ev, function(e){ e.preventDefault(); zone.classList.add('over'); }); });
  ['dragleave', 'drop'].forEach(function(ev){
    zone.addEventListener(ev, function(e){ e.preventDefault(); zone.classList.remove('over'); }); });
  zone.addEventListener('drop', function(e){ set(e.dataTransfer.files[0] || null); });
  document.getElementById(id + 'x').addEventListener('click', function(e){
    e.stopPropagation(); inp.value = ''; PICKED[slot] = undefined;   /* 只清新选择，回落到当前生效文件 */
    document.getElementById(id + 'n').textContent = base || t('up.none');
    document.getElementById(id + 'x').style.display = 'none';
  });
}
function readFile(f){
  return new Promise(function(res){
    if (!f) return res(null);
    const rd = new FileReader();
    rd.onload = function(){ res({name: f.name, b64: String(rd.result).split(',', 2)[1]}); };
    rd.readAsDataURL(f);
  });
}
async function postJSON(url, body){
  const r = await fetch(url, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  const j = await r.json().catch(function(){ return {}; });
  return {ok: r.ok, status: r.status, j: j};
}
function errHtml(j, status){
  return ((j && j.errors) || ['HTTP ' + status]).map(function(e){
    return '<div>✗ ' + esc(e) + '</div>'; }).join('');
}
function bindData(){
  ['fRec', 'fFm', 'fPairs'].forEach(wireDrop);
  document.getElementById('upGo').addEventListener('click', async function(){
    const msg = document.getElementById('upMsg'), busy = document.getElementById('upBusy');
    const body = {records: await readFile(PICKED.records),
                  fieldmap: await readFile(PICKED.fieldmap),
                  pairs: await readFile(PICKED.pairs)};
    if (!body.records || !body.fieldmap){
      msg.innerHTML = '<div class="errbox">records + field map are required（记录与字段角色表必传）</div>';
      return;
    }
    busy.textContent = '…'; msg.innerHTML = '';
    const r = await postJSON('api/upload', body);
    busy.textContent = '';
    if (!r.ok){ msg.innerHTML = '<div class="errbox">' + errHtml(r.j, r.status) + '</div>'; return; }
    await refresh();
  });
  const sb = document.getElementById('sampleBtn');
  sb.addEventListener('click', async function(){
    const busy = document.getElementById('sampleBusy'), msg = document.getElementById('upMsg');
    busy.textContent = t('up.sample.busy'); msg.innerHTML = '';
    const r = await fetch('api/sample');
    const j = await r.json().catch(function(){ return {}; });
    if (!r.ok){ busy.textContent = '';
      msg.innerHTML = '<div class="errbox">' + errHtml(j, r.status) + '</div>'; return; }
    const up = await postJSON('api/upload', {records: j.records, fieldmap: j.fieldmap});
    busy.textContent = '';
    if (!up.ok){ msg.innerHTML = '<div class="errbox">' + errHtml(up.j, up.status) + '</div>'; return; }
    await refresh();
  });
  bindPreviewBits();
}
function bindPreviewBits(){
  const q = document.getElementById('pvQ');
  if (q) q.addEventListener('input', function(){
    const v = q.value.toLowerCase();
    document.querySelectorAll('#pvTbl tr[data-q]').forEach(function(tr){
      tr.style.display = (!v || tr.dataset.q.indexOf(v) >= 0) ? '' : 'none';
    });
  });
  const go = document.getElementById('pvGo');
  if (go) go.addEventListener('click', async function(){
    const a = document.getElementById('pvA').value, b = document.getElementById('pvB').value;
    const out = document.getElementById('pvOut');
    if (a === b){ out.innerHTML = '<div class="errbox">pick two different records（请挑两条不同记录）</div>'; return; }
    out.textContent = '…';
    const r = await postJSON('api/proxy-score', {a: a, b: b});
    if (!r.ok){ out.innerHTML = '<div class="errbox">' + errHtml(r.j, r.status) + '</div>'; return; }
    const j = r.j;
    const rows = j.fields.map(function(f){
      const st = {both_missing: '—', missing: 'missing', same: 'same', diff: 'diff'}[f.state];
      return '<tr><th style="width:130px">' + esc((f.label && (f.label[LANG] || f.label.en)) || f.name) +
        '</th><td class="mono">' + esc(f.a || '—') + '</td><td class="mono">' + esc(f.b || '—') +
        '</td><td><span class="st ' + (f.state === 'both_missing' ? 'missing' : f.state) +
        '">' + st + '</span></td></tr>';
    }).join('');
    out.innerHTML = '<div style="display:flex;gap:14px;align-items:center;margin:6px 0 10px">' +
      '<div class="scorebar" style="flex:1"><i style="width:' + Math.round(j.score * 100) + '%"></i></div>' +
      '<div class="mono" style="font-weight:700">' + j.score.toFixed(4) + '</div></div>' +
      '<table class="fdiff">' + rows + '</table>' +
      '<div class="mono" style="margin-top:8px;color:var(--t3);font-size:11px">' + esc(t('pv.proxy')) + '</div>';
  });
}

/* ---- ③ 训练（W7 直连平台：上传包 → 开训 → 部署 → 全对打分定 θ；无令牌回落人工模式）---- */
let TRAIN_TIMER = null, TRAIN_MODELS = null;
function trainPollStop(){
  if (TRAIN_TIMER){ clearInterval(TRAIN_TIMER); TRAIN_TIMER = null; }
}
function trainPollMaybe(phase){
  if (phase === 'training' || phase === 'deploying' || phase === 'building'){
    if (!TRAIN_TIMER) TRAIN_TIMER = setInterval(async function(){
      const r = await fetch('api/train');
      if (!r.ok) return;
      S.train = await r.json();
      if (VIEW === 'train') renderMain(); else trainPollStop();
    }, 4000);
  } else trainPollStop();
}
function phChip(phase){
  const bad = phase === 'failed';
  return '<span class="phchip' + (bad ? ' bad' : '') + '">' + esc(t('ph.' + phase)) + '</span>';
}
function trainLink(url){
  return url ? ' · <a href="' + esc(url) + '" target="_blank" rel="noopener">' +
    esc(t('train.view')) + '</a>' : '';
}
function viewTrainManual(){
  const st = stateOf('train');
  const line = function(done, label){
    return '<div class="pline"><span class="sdot ' + (done ? 'done' : '') +
      '" style="margin-top:5px"></span><span style="color:' +
      (done ? 'var(--t1)' : 'var(--t3)') + '">' + esc(label) + '</span></div>';
  };
  return '<h1>' + esc(t('train.h')) + '</h1><p class="sub">' + esc(t('train.sub.manual')) + '</p>' +
    '<div class="card"><div class="sh">' + esc(t('train.pkg')) + '</div>' +
    '<div style="margin-top:8px"><a class="btn ghost" href="download/package.zip" download>' +
    esc(t('train.dl')) + '</a></div></div>' +
    '<div class="card" style="margin-top:14px"><div class="sh">' + esc(t('train.steps.h')) + '</div>' +
    line(true, t('ts.pkg')) + line(st === 'done', t('ts.submit')) +
    line(st === 'done', t('ts.eval')) + line(st === 'done', t('ts.back')) + '</div>';
}
function viewTrain(){
  if (!S || !S.report) return emptyBox('data', 'gen.empty', 'nav.data');
  const cfg = (S && S.train_cfg) || {can_train: false};
  if (!cfg.can_train) return viewTrainManual();
  const tr = (S && S.train) || {phase: 'ready'};
  const phase = tr.phase || 'ready';
  trainPollMaybe(phase);
  let h = '<h1>' + esc(t('train.h')) + '</h1><p class="sub">' + esc(t('train.sub')) + '</p>';
  /* 配置卡：未开训/失败后可重配（开训后配置锁定在 state 里，不重复渲染） */
  if (phase === 'ready' || phase === 'idle' || phase === 'failed'){
    const models = (TRAIN_MODELS || []);
    const def = models.indexOf('embeddinggemma-2') >= 0 ? 'embeddinggemma-2'
      : (models[0] || '');
    const modelCtl = models.length
      ? '<select id="trBase">' + models.map(function(m){
          return '<option value="' + esc(m) + '"' + (m === def ? ' selected' : '') +
            '>' + esc(m) + '</option>'; }).join('') + '</select>'
      : '<input type="text" id="trBase" placeholder="base model name" style="width:240px">';
    h += '<div class="card"><div class="sh">' + esc(t('train.cfg')) + '</div>' +
      '<div class="cfggrid">' +
      '<label>' + esc(t('train.base')) + modelCtl + '</label>' +
      '<label>' + esc(t('train.out')) +
        '<input type="text" id="trOut" placeholder="' + esc(t('train.out.ph')) + '"></label>' +
      '<label>' + esc(t('train.serve')) +
        '<input type="text" id="trSvc" placeholder="<auto>"></label></div>' +
      '<div style="display:flex;gap:10px;align-items:center;margin-top:12px">' +
      '<button class="btn" id="trStart">' + esc(t('train.start')) + '</button>' +
      '<span class="sub" id="trBusy" style="margin:0"></span></div>' +
      '<div id="trMsg" style="margin-top:8px"></div></div>';
  }
  /* 进度卡 */
  h += '<div class="card" style="margin-top:14px"><div class="sh">' + esc(t('train.prog')) +
    ' ' + phChip(phase) + '</div><div style="margin-top:10px" id="trBody">' +
    trainBody(tr, phase) + '</div></div>';
  return h;
}
function trainBody(tr, phase){
  let b = '';
  if (tr.detail_url) b += '<div style="font-size:12.5px;margin-bottom:6px">' +
    '<span class="mono" style="color:var(--t3)">' + esc(tr.output_name || tr.job_id || '') +
    '</span>' + trainLink(tr.detail_url) + '</div>';
  if (tr.platform_status) b += '<div class="sub" style="margin:0 0 6px">platform: ' +
    '<span class="mono">' + esc(tr.platform_status) + '</span></div>';
  if (tr.warning) b += '<div class="sub" style="margin:0 0 6px;color:var(--red-b)">' +
    esc(tr.warning) + '</div>';
  if (phase === 'training') b += '<div class="sub" style="margin:0">' +
    esc(t('ph.training')) + '…</div>';
  if (phase === 'deploying') b += '<div class="sub" style="margin:0">' +
    esc(t('ph.deploying')) + '…</div>';
  if (phase === 'building') b += '<div class="sub" style="margin:0">' +
    esc(t('train.build.busy')) + '</div>';
  if (phase === 'trained'){
    b += '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
      '<button class="btn" id="trDeploy">' + esc(t('train.deploy')) + '</button>' +
      '<a class="btn ghost" href="download/package.zip" download>' + esc(t('train.dl')) + '</a></div>';
    const evr = (tr.evaluation && (tr.evaluation.report_preview_url ||
               (tr.evaluation.report_downloads || [])[0])) || null;
    if (evr) b += '<div style="font-size:12px;margin-top:8px"><a href="' + esc(evr) +
      '" target="_blank" rel="noopener">' + esc(t('train.view')) + '</a></div>';
  }
  if (phase === 'deployed'){
    b += '<div style="font-size:12px;margin-bottom:8px">' + esc(t('train.svc')) + ': ' +
      '<span class="mono">' + esc(tr.service_url || '—') + '</span></div>' +
      '<button class="btn" id="trBuild">' + esc(t('train.build')) + '</button>';
  }
  if (phase === 'done'){
    b += '<div class="okbox" style="margin:0 0 10px">' + esc(t('train.done')) + '</div>' +
      '<a class="btn" href="#/effect">' + esc(t('nav.effect')) + ' →</a>';
  }
  if (phase === 'failed'){
    b += '<div class="errbox" style="margin:0 0 10px">' + esc(tr.error || 'failed') +
      '</div><button class="btn ghost" id="trRetry">' + esc(t('train.retry')) + '</button>';
  }
  if (phase === 'ready' || phase === 'idle')
    b += '<div class="sub" style="margin:0">' + esc(t('ph.' + phase)) + '</div>';
  return b;
}
function bindTrain(){
  const cfg = (S && S.train_cfg) || {can_train: false};
  if (!cfg.can_train) return;
  if (TRAIN_MODELS === null){
    fetch('api/train?models=1').then(function(r){ return r.ok ? r.json() : null; })
      .then(function(j){
        TRAIN_MODELS = (j && j.models) || [];
        if (VIEW === 'train' && document.getElementById('trBase')) renderMain();
      }).catch(function(){ TRAIN_MODELS = []; });
  }
  const start = document.getElementById('trStart');
  if (start) start.addEventListener('click', async function(){
    const msg = document.getElementById('trMsg'), busy = document.getElementById('trBusy');
    busy.textContent = '…'; msg.innerHTML = '';
    const r = await postJSON('api/train/start', {
      base_model: (document.getElementById('trBase') || {}).value || '',
      output_name: (document.getElementById('trOut') || {}).value || '',
      serving_name: (document.getElementById('trSvc') || {}).value || ''});
    busy.textContent = '';
    if (!r.ok){ msg.innerHTML = '<div class="errbox">' + errHtml(r.j, r.status) + '</div>'; return; }
    S.train = r.j; renderMain();
  });
  const dep = document.getElementById('trDeploy');
  if (dep) dep.addEventListener('click', async function(){
    const r = await postJSON('api/train/deploy', {});
    if (!r.ok){ alert((r.j.errors || []).join('\n') || 'deploy failed'); return; }
    S.train = r.j; renderMain();
  });
  const bu = document.getElementById('trBuild');
  if (bu) bu.addEventListener('click', async function(){
    bu.disabled = true; bu.textContent = t('train.build.busy');
    const r = await postJSON('api/train/build', {});
    if (!r.ok){ bu.disabled = false; bu.textContent = t('train.build');
      alert((r.j.errors || []).join('\n') || 'build failed'); return; }
    S.train = r.j; await refresh(); renderMain();
  });
  const re = document.getElementById('trRetry');
  if (re) re.addEventListener('click', function(){
    /* 失败重试：回到配置卡（phase 仍是 failed；start 允许从 failed 重开） */
    renderMain();
  });
}

/* ---- ④ 效果 ---- */
function viewEffect(){
  if (!S || !S.summary) return '<h1>' + esc(t('ef.h')) + '</h1><p class="sub">' +
    esc(t('ef.sub')) + '</p>' + emptyBox('train', 'ef.empty');
  const s = S.summary;
  const col = function(title, theta, asserted, fls, missed, isProxy){
    return '<div class="card cmp ' + (isProxy ? 'vi' : 'cy') + '" style="margin-top:14px">' +
      '<div class="sh" style="' + (isProxy ? 'color:var(--violet-b)' : 'color:var(--cyan-b)') + '">' +
      esc(title) + '</div><div class="numgrid" style="margin-top:8px">' +
      numcard(theta != null ? theta.toFixed(4) : '—', t('k.theta')) +
      numcard(asserted != null ? asserted : '—', t('k.asserted')) +
      numcard(fls != null ? fls : '—', t('k.false')) +
      numcard(missed != null ? missed : '—', t('k.missed')) + '</div></div>';
  };
  let h = '<h1>' + esc(t('ef.h')) + '</h1><p class="sub">' + esc(t('ef.sub')) + '</p>';
  if (S.net){
    h += '<div class="card" style="margin-top:14px"><div class="sh">' + esc(t('ef.net.h')) + '</div>' +
      '<div class="stabs" id="netTabs"></div>' +
      '<div class="netwrapc"><div id="netCv" class="netcv"></div>' +
      '<div class="nettip" id="netTip"></div></div>' +
      '<div class="netcap" id="netCap"></div>' +
      '<div class="netleg">' +
      '<span class="k"><span class="sw" style="border-color:var(--cyan)"></span>' + esc(t('ef.net.leg.link')) + '</span>' +
      '<span class="k"><span class="sw" style="border-color:var(--red)"></span>' + esc(t('ef.net.leg.bad')) + '</span>' +
      '<span class="k"><span class="sw dash" style="border-color:var(--red)"></span>' + esc(t('ef.net.leg.miss')) + '</span>' +
      '<span class="k"><span class="sw" style="border-color:var(--violet)"></span>' + esc(t('lg.base')) + '</span>' +
      '<span class="k">' + esc(t('ef.net.leg.halo')) + '</span></div></div>';
  }
  h += col(t('ef.tuned'), s.theta, s.asserted, s.false_links, s.missed_pairs, false) +
    col(t('ef.base'), s.baseline_theta, s.baseline_asserted, s.baseline_false,
        s.baseline_missed, true) +
    (S.has_demo ? '<div style="margin-top:16px"><a class="btn" href="demo/" target="_blank" rel="noopener">' +
      esc(t('ef.open')) + ' ↗</a></div>' : '');
  return h;
}

/* ④ 网络三态（可交互）：api/network → 阶段页签 + 悬停高亮/气泡 + 点击锁定
   ——同一批记录的三种状态，页签上带各阶段数字，一眼对比效果变化 */
let NET = null, NET_STAGE = 2, NET_PIN = null;
function netStageOf(){
  return NET ? NET.stages[Math.min(NET_STAGE, NET.stages.length - 1)] : null;
}
function netStageSvg(stage, cv){
  const pos = {};
  stage.nodes.forEach(function(n){ pos[n.r] = n; });
  const P = function(r){ return pos[r] || {x: cv.w / 2, y: cv.h / 2}; };
  const ln = function(pair, cls, kind){
    const p1 = P(pair[0]), p2 = P(pair[1]);
    return '<line x1="' + p1.x + '" y1="' + p1.y + '" x2="' + p2.x + '" y2="' + p2.y +
      '" class="' + cls + '" data-a="' + esc(pair[0]) + '" data-b="' + esc(pair[1]) +
      '" data-k="' + kind + '"' +
      (kind === 'miss' ? ' stroke-dasharray="6 5"' : '') + '/>';
  };
  let inner = '';
  (stage.halos || []).forEach(function(hl){
    inner += '<circle cx="' + hl.x + '" cy="' + hl.y + '" r="' + hl.r +
      '" class="' + (hl.mixed ? 'nhalo-bad' : 'nhalo') + '"/>';
  });
  (stage.edges || []).forEach(function(e){ inner += ln(e, 'nshadow', 'shadow'); });
  (stage.links || []).forEach(function(e){
    inner += ln(e, stage.key === 'baseline' ? 'nlink-v' : 'nlink-c', 'link');
  });
  (stage.false || []).forEach(function(e){ inner += ln(e, 'nbad', 'bad'); });
  (stage.missed || []).forEach(function(e){ inner += ln(e, 'nbad', 'miss'); });
  stage.nodes.forEach(function(n){
    inner += '<circle cx="' + n.x + '" cy="' + n.y + '" r="5" class="ndot" data-rid="' +
      esc(n.r) + '"/>';
  });
  return '<svg viewBox="' + netViewBox(stage, cv).join(' ') +
    '" preserveAspectRatio="xMidYMid meet">' + inner + '</svg>';
}
/* viewBox 贴内容：小数据集（如 22 条租户试点）的分位归一坐标会挤在画布中段、
   个别离群点甚至落在 1200×780 之外被裁——按各阶段内容包围盒取景（含光环半径
   +26 呼吸边距），宽高比收进 [1.4, 2.6] 防过高/过扁。0 起点布局不受影响。 */
function netViewBox(stage, cv){
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const grow = function(x, y, r){
    x0 = Math.min(x0, x - r); y0 = Math.min(y0, y - r);
    x1 = Math.max(x1, x + r); y1 = Math.max(y1, y + r);
  };
  stage.nodes.forEach(function(n){ grow(+n.x, +n.y, 6); });
  (stage.halos || []).forEach(function(h){ grow(+h.x, +h.y, (+h.r || 0)); });
  if (!isFinite(x0) || x1 - x0 < 2 || y1 - y0 < 2){
    const m = Math.min(cv.w, cv.h) / 2;
    return [cv.w / 2 - m, cv.h / 2 - m, 2 * m, 2 * m];
  }
  const pad = 26;
  x0 -= pad; y0 -= pad; x1 += pad; y1 += pad;
  let w = x1 - x0, h = y1 - y0;
  if (w / h < 1.4){ const nw = 1.4 * h; x0 -= (nw - w) / 2; w = nw; }
  else if (w / h > 2.6){ const nh = w / 2.6; y0 -= (nh - h) / 2; h = nh; }
  return [Math.round(x0 * 10) / 10, Math.round(y0 * 10) / 10,
          Math.round(w * 10) / 10, Math.round(h * 10) / 10];
}
function netTabs(){
  const el = document.getElementById('netTabs');
  if (!el || !NET) return;
  const stat = [esc(t('ef.net.shadow')) + ' ' + NET.shadow,
    '✗ ' + NET.stages[1].false.length + ' · ⊘ ' + NET.stages[1].missed.length,
    '✗ ' + NET.stages[2].false.length + ' · ⊘ ' + NET.stages[2].missed.length];
  const keys = ['ef.net.s0', 'ef.net.s1', 'ef.net.s2'];
  el.innerHTML = keys.map(function(k, i){
    return '<button class="stab' + (i === NET_STAGE ? ' active' : '') + '" data-i="' + i +
      '">' + esc(t(k)) + '<span class="num">' + stat[i] + '</span></button>';
  }).join('');
  el.querySelectorAll('button.stab').forEach(function(b){
    b.addEventListener('click', function(){
      NET_STAGE = +b.dataset.i; NET_PIN = null; netTabs(); netRender();
    });
  });
}
function netRender(){
  const cv = document.getElementById('netCv'), cap = document.getElementById('netCap');
  const st = netStageOf();
  if (!cv || !st) return;
  cv.innerHTML = netStageSvg(st, NET.canvas);
  cv.classList.remove('fadein'); void cv.offsetWidth; cv.classList.add('fadein');
  if (cap){
    const th = st.theta != null ? ' · θ=' + Number(st.theta).toFixed(4) : '';
    const cnt = st.key === 'before'
      ? t('ef.net.shadow') + ' ' + NET.shadow
      : '✗ ' + st.false.length + ' · ⊘ ' + st.missed.length + th;
    cap.innerHTML = '<span><b style="color:var(--t2)">' +
      esc(t(['ef.net.s0', 'ef.net.s1', 'ef.net.s2'][NET_STAGE])) + '</b> · ' + cnt + '</span>' +
      '<span style="opacity:.75">' + esc(t('ef.net.hint')) + '</span>';
  }
  if (NET_PIN) netFocus(NET_PIN, null);
}
function netFocus(rid, edge){
  const svg = document.querySelector('#netCv svg');
  if (!svg) return;
  svg.querySelectorAll('line').forEach(function(l){
    const on = !rid || l.dataset.a === rid || l.dataset.b === rid;
    l.classList.toggle('dim', !on);
  });
  svg.querySelectorAll('circle[data-rid]').forEach(function(c){
    const on = !rid || c.dataset.rid === rid;
    c.classList.toggle('dim', !on);
    c.setAttribute('r', (rid && c.dataset.rid === rid) ? 7.5 : 5);
  });
}
function netTip(node, html){
  const tip = document.getElementById('netTip');
  if (!tip) return;
  if (html == null){ tip.style.display = 'none'; return; }
  const wrap = tip.parentElement.getBoundingClientRect();
  tip.innerHTML = html;
  tip.style.display = 'block';
  const x = Math.min(Math.max(node.clientX - wrap.left + 14, 6),
                     wrap.width - tip.offsetWidth - 8);
  const y = Math.min(node.clientY - wrap.top + 14, wrap.height - tip.offsetHeight - 6);
  tip.style.left = x + 'px'; tip.style.top = Math.max(y, 4) + 'px';
}
function netBindCv(){
  const cv = document.getElementById('netCv');
  if (!cv) return;
  cv.addEventListener('mouseover', function(e){
    const t0 = e.target;
    if (t0.tagName === 'circle' && t0.dataset.rid){
      const rid = t0.dataset.rid;
      const svg = cv.querySelector('svg');
      const deg = svg ? Array.prototype.filter.call(
        svg.querySelectorAll('line'), function(l){
          return l.dataset.a === rid || l.dataset.b === rid; }).length : 0;
      netFocus(rid, null);
      netTip(e, esc(rid) + ' · ' + deg + ' ' + esc(t('ef.net.deg')));
    } else if (t0.tagName === 'line'){
      t0.classList.add('hl');
      netTip(e, esc(t0.dataset.a) + ' ↔ ' + esc(t0.dataset.b) + ' · ' +
        esc(t('ef.net.k.' + t0.dataset.k)));
    }
  });
  cv.addEventListener('mouseout', function(e){
    if (e.target.tagName === 'line') e.target.classList.remove('hl');
    if (NET_PIN) netFocus(NET_PIN, null);
    else { netFocus(null, null); netTip(null, null); }
  });
  cv.addEventListener('click', function(e){
    if (e.target.tagName === 'circle' && e.target.dataset.rid){
      NET_PIN = (NET_PIN === e.target.dataset.rid) ? null : e.target.dataset.rid;
      netFocus(NET_PIN, null);
    } else { NET_PIN = null; netFocus(null, null); }
  });
}
function bindEffect(){
  const cv = document.getElementById('netCv');
  if (!cv) return;
  netBindCv();
  fetch('api/network').then(function(r){
    return r.ok ? r.json() : r.json().then(function(j){
      throw new Error((j.errors || ['HTTP ' + r.status]).join('; ')); });
  }).then(function(net){
    NET = net; netTabs(); netRender();
  }).catch(function(e){
    cv.innerHTML = '<div class="errbox">' + esc(e.message) + '</div>';
  });
}

/* ---- ⑤ 部署 ---- */
var DP_ROLES = {
  'er_mcp_server.py': 'dp.f.server', 'embed_client.py': 'dp.f.client',
  'er_mcp.env': 'dp.f.env', 'er_meta.json': 'dp.f.meta',
  'records.json': 'dp.f.records', 'scores.json': 'dp.f.scores',
  'START.md': 'dp.f.start'
};
function dpCmd(){
  /* 与交付包内 START.md 的注册命令同形态；<bundle> 由客户替换为解压目录绝对路径 */
  return 'claude mcp add entity-er -- env ER_VILLAGE=<bundle>/records.json ' +
    'ER_SCORES=<bundle>/scores.json ER_META=<bundle>/er_meta.json ' +
    'python3 <bundle>/er_mcp_server.py';
}
function dpSize(n){
  return n >= 10240 ? Math.round(n / 1024) + ' KB'
    : n >= 1024 ? (n / 1024).toFixed(1) + ' KB' : n + ' B';
}
function copyText(txt, btn){
  const orig = btn.textContent, done = function(){
    btn.textContent = t('dp.copied'); btn.disabled = true;
    setTimeout(function(){ btn.textContent = orig; btn.disabled = false; }, 1400);
  };
  const fb = function(){                       /* 非 https/旧浏览器兜底 */
    const ta = document.createElement('textarea');
    ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    document.body.removeChild(ta);
  };
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(txt).then(done, fb);
  else fb();
}
function bindDeploy(){
  document.querySelectorAll('#cmain .copybtn').forEach(function(b){
    b.addEventListener('click', function(){
      copyText(b.dataset.copyCmd ? dpCmd() : b.dataset.copy, b);
    });
  });
}
function viewDeploy(){
  const apiLive = S && S.api && S.api.url;
  let h = '<h1>' + esc(t('dp.h')) + '</h1><p class="sub">' +
    esc(t(apiLive ? 'dp.sub' : 'dp.sub.off')) + '</p>';
  /* 路径一：HTTP API——三态（url+key / 仅 url / 未开通），未开通也给占位卡不整段蒸发 */
  h += '<div class="card"><div class="sh">' + esc(t('dp.api')) + '</div>';
  if (apiLive){
    h += '<table class="ctable" style="margin-top:8px">' +
      '<tr><th>endpoint</th><td class="mono">' + esc(S.api.url) + '</td>' +
      '<td style="width:1%"><button class="copybtn" data-copy="' + esc(S.api.url) +
      '">' + esc(t('dp.copy')) + '</button></td></tr>';
    h += S.api.key
      ? '<tr><th>api_key</th><td class="mono">' + esc(S.api.key) + '</td>' +
        '<td style="width:1%"><button class="copybtn" data-copy="' + esc(S.api.key) +
        '">' + esc(t('dp.copy')) + '</button></td></tr>'
      : '<tr><th>api_key</th><td colspan="2" style="color:var(--t3)">' +
        esc(t('dp.api.nokey')) + '</td></tr>';
    h += '</table>';
  } else {
    h += '<div class="empty" style="margin-top:10px">' + esc(t('dp.api.off')) + '</div>';
  }
  h += '</div>';
  /* 路径二：MCP 交付包——一个 zip 主 CTA（不再散落小文件）+ 一行注册命令 + 包内清单 */
  h += '<div class="card" style="margin-top:14px"><div class="sh">' + esc(t('dp.mcp')) + '</div>';
  if (S && S.mcp_files && S.mcp_files.length){
    const kb = Object.keys(S.mcp_bytes || {}).reduce(
      function(a, k){ return a + (S.mcp_bytes[k] || 0); }, 0);
    h += '<div class="dprow"><a class="btn dpzip" href="download/mcp.zip" download>⇣ ' +
      esc(t('dp.zip')) + '</a><span class="sub" style="margin:0">' +
      esc(t('dp.zip.n').replace('{N}', String(S.mcp_files.length))
        .replace('{K}', String(Math.max(1, Math.round(kb / 1024))))) +
      '</span></div>' +
      '<div class="sh" style="margin-top:16px">' + esc(t('dp.cmd.h')) + '</div>' +
      '<div class="codebox"><code class="mono">' + esc(dpCmd()) + '</code>' +
      '<button class="copybtn" data-copy-cmd="1">' + esc(t('dp.copy')) + '</button></div>' +
      '<div style="font-size:12px;color:var(--t3);margin-top:6px">' +
      esc(t('dp.cmd.note')) + '</div>' +
      '<div class="sh" style="margin-top:16px">' + esc(t('dp.files.h')) + '</div>' +
      '<table class="ctable" style="margin-top:8px"><tr><th>' + esc(t('k.file')) + '</th><th>' +
      esc(t('k.role')) + '</th><th style="text-align:right">' + esc(t('k.size')) + '</th><th></th></tr>' +
      S.mcp_files.map(function(f){
        const b = (S.mcp_bytes || {})[f];
        return '<tr><td class="mono" style="white-space:nowrap">' + esc(f) + '</td><td>' +
          (DP_ROLES[f] ? esc(t(DP_ROLES[f])) : '—') + '</td>' +
          '<td style="text-align:right;color:var(--t3);white-space:nowrap">' +
          (b != null ? dpSize(b) : '—') + '</td>' +
          '<td style="width:1%"><a class="filelink dponly" href="download/mcp/' +
          encodeURIComponent(f) + '" download title="' + esc(f) + '">⇣</a></td></tr>';
      }).join('') + '</table>' +
      '<div style="font-size:12px;margin-top:8px"><a href="download/mcp/START.md" ' +
      'target="_blank" rel="noopener">' + esc(t('dp.f.start')) + ' ⇗</a></div>';
  } else {
    h += '<div class="empty" style="margin-top:8px">' + esc(t('dp.empty')) + '</div>';
  }
  h += '</div>';
  /* 终点收尾：五步完成 + 装好验证方式 + 回总览 */
  if (stateOf('deploy') === 'done'){
    h += '<div class="okbox" style="margin-top:14px"><b>' + esc(t('dp.done.k')) +
      ' ✓</b> — ' + esc(t('dp.done')) + ' <a href="#/overview">' +
      esc(t('nav.overview')) + '</a></div>';
  }
  return h;
}

function emptyBox(routeKey, msgKey, labelKey){
  return '<div class="empty">' + esc(t(msgKey)) + ' <a href="#/' + routeKey + '">' +
    esc(t(labelKey || ('nav.' + routeKey))) + '</a></div>';
}

/* ---- 主题 / 语言 ---- */
document.getElementById('themeBtn').addEventListener('click', function(){
  const h = document.documentElement;
  h.dataset.theme = h.dataset.theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('ea_theme', h.dataset.theme); } catch (e) {}
  render();
});
document.getElementById('langBtn').addEventListener('click', function(){
  LANG = LANG === 'en' ? 'zh' : 'en';
  try { localStorage.setItem('ea_lang', LANG); } catch (e) {}
  render();
});
route();
/* 回能力演示页：与演示页→控制台入口互为对称的同源探测——上一级源真有
   /healthz（一体化 er_server）才点亮；单独跑 er_console（自身就是根，
   ../healthz 归一到 /healthz → 404）保持隐藏，绝不留死链 */
fetch('../healthz').then(function(r){
  if (r.ok) document.getElementById('demoLink').hidden = false;
}).catch(function(){});
"""


def render_console(css: str, label: str, title: str | None = None) -> str:
    """控制台 SPA（同源服务页；沿用演示页主题 token 与 localStorage 约定）。"""
    return (_HEAD
            .replace("__TITLE__", title or "ER Service Console")
            .replace("__LABEL__", esc_label(label))
            .replace("__CSS__", css + _EXTRA_CSS)
            .replace("__JS__", _JS))


def esc_label(label: str) -> str:
    return (str(label).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
