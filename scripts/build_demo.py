#!/usr/bin/env python3
"""构建演示页：data/*.json → build/demo.html（单文件、零外链、离线可开）。

用法：python scripts/build_demo.py [--data-dir data] [--out build/demo.html] [--open]
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.page import render_page  # noqa: E402
from entity_arena.theme import CSS  # noqa: E402


def load(data_dir: Path) -> dict:
    records: dict[str, dict] = {}
    for line in (data_dir / "records.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            records[r["record_id"]] = r
    pairs = json.loads((data_dir / "pairs.json").read_text(encoding="utf-8"))
    sources = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))["sources"]
    oc = _load_open_cases(data_dir)
    if oc and "explain" in oc:
        # Explain 实测卡：真实模型分数 + 逐字段四态拆解（R1 连对 / R2 拒连）
        pairs = pairs + oc["explain"]["pairs"]
        records.update(oc["explain"]["records"])
        sources = {**sources, "febrl3": {"en": "Febrl3 (public ER benchmark)",
                                         "zh": "Febrl3（公开 ER 基准）"}}
    return {
        "meta": {
            "seed": json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))["seed"],
            "n_records": len(records),
            "n_entities": len(json.loads((data_dir / "entities.json").read_text(encoding="utf-8"))),
            "threshold": json.loads((data_dir / "metrics.json").read_text(encoding="utf-8"))["threshold"],
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
        "sources": sources,
        "scenarios": json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))["scenarios"],
        "records": records,
        "entities": json.loads((data_dir / "entities.json").read_text(encoding="utf-8")),
        "results": json.loads((data_dir / "results.json").read_text(encoding="utf-8"))["models"],
        "metrics": json.loads((data_dir / "metrics.json").read_text(encoding="utf-8")),
        "network": json.loads((data_dir / "network.json").read_text(encoding="utf-8")),
        "queries": json.loads((data_dir / "queries.json").read_text(encoding="utf-8")),
        "pairs": pairs,
        "open_bench": _load_open_bench(data_dir),
        "open_cases": oc,
    }


def _load_open_bench(data_dir: Path) -> dict:
    """开源基准轨数据（真实模型实测）。缺文件直接 raise，不带静默降级。"""
    p = data_dir / "open_benchmark.json"
    if not p.exists():
        raise SystemExit(
            f"缺少 {p}（开源基准轨数据）。先运行：python3 scripts/update_open_benchmark.py"
        )
    payload = json.loads(p.read_text(encoding="utf-8"))
    if not payload.get("models"):
        raise SystemExit(f"{p}: models 为空，拒绝构建")
    return payload


def _load_open_cases(data_dir: Path) -> dict | None:
    """网络视图 Febrl3 实测轨数据（案例村 + 微调模型真实分数）。

    可选数据：平台部署修复前允许缺席——页面只出合成轨（轨道条隐藏，非静默：
    构建输出会显式提示）。接入后缺失字段仍直接 raise。
    """
    p = data_dir / "open_cases.json"
    if not p.exists():
        print(f"note: {p} 不存在 —— 网络视图 Febrl3 实测轨未接入（仅合成轨）。\n"
              f"      平台部署修复后：fetch_village_embeddings.py → "
              f"build_open_cases.py --emb … → 重新构建即可点亮。")
        return None
    payload = json.loads(p.read_text(encoding="utf-8"))
    for key in ("model", "theta", "stats", "records", "links", "false_links",
                "missed_pairs", "components", "comp_of", "top3", "network"):
        if key not in payload:
            raise SystemExit(f"{p}: 缺字段 {key}，拒绝构建")
    if not payload["records"]:
        raise SystemExit(f"{p}: records 为空，拒绝构建")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "demo.html")
    ap.add_argument("--open", action="store_true", help="build 完成后用浏览器打开")
    args = ap.parse_args()

    bundle = load(args.data_dir)
    html = render_page(bundle, CSS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")

    size_kb = args.out.stat().st_size / 1024
    print(f"built {args.out}  ({size_kb:.0f} KB, single file, zero external links)")

    # 自检：关键标记齐全（失败不静默）
    markers = ['id="view-arena"', 'id="view-network"', 'id="view-explain"',
               'id="view-scoreboard"', 'id="openBench"', "const DATA = {",
               "netToggle", "renderScoreboard", "renderOpenBench",
               'id="netTrack"', "switchNetTrack", "openNetSide"]
    if bundle["open_cases"] is not None:
        markers.append('"open_cases":{')  # 数据就绪时必须真的打进页面
    for marker in markers:
        if marker not in html:
            raise SystemExit(f"self-check failed: marker missing: {marker}")
    assert "</script><script" not in html.split("<body>")[0]  # 数据未破坏脚本结构
    print("self-check: 4 views + data blob + interactions ✓")

    if args.open:
        import subprocess

        subprocess.run(["open", str(args.out)], check=False)


if __name__ == "__main__":
    main()
