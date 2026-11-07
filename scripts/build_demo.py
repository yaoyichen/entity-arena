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
    return {
        "meta": {
            "seed": json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))["seed"],
            "n_records": len(records),
            "n_entities": len(json.loads((data_dir / "entities.json").read_text(encoding="utf-8"))),
            "threshold": json.loads((data_dir / "metrics.json").read_text(encoding="utf-8"))["threshold"],
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
        "sources": json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))["sources"],
        "scenarios": json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))["scenarios"],
        "records": records,
        "entities": json.loads((data_dir / "entities.json").read_text(encoding="utf-8")),
        "results": json.loads((data_dir / "results.json").read_text(encoding="utf-8"))["models"],
        "metrics": json.loads((data_dir / "metrics.json").read_text(encoding="utf-8")),
        "network": json.loads((data_dir / "network.json").read_text(encoding="utf-8")),
        "queries": json.loads((data_dir / "queries.json").read_text(encoding="utf-8")),
        "pairs": json.loads((data_dir / "pairs.json").read_text(encoding="utf-8")),
    }


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
    for marker in ('id="view-arena"', 'id="view-network"', 'id="view-explain"',
                   'id="view-scoreboard"', "const DATA = {", "netToggle", "renderScoreboard"):
        if marker not in html:
            raise SystemExit(f"self-check failed: marker missing: {marker}")
    assert "</script><script" not in html.split("<body>")[0]  # 数据未破坏脚本结构
    print("self-check: 4 views + data blob + interactions ✓")

    if args.open:
        import subprocess

        subprocess.run(["open", str(args.out)], check=False)


if __name__ == "__main__":
    main()
