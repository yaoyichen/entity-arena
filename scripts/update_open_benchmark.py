#!/usr/bin/env python3
"""平台评测报告 → data/open_benchmark.json（演示页"开源基准"板块数据源）。

输入：data_open/eval_reports/ 下 6 份平台报告原文（.md，随仓库提交，溯源用）：
  baseline-<tag>.md   训练前独立评测（单列均值）
  post-<tag>.md       训练后评测（基线列 + 训练后列，自带前后对比）

输出：data/open_benchmark.json —— 确定性生成：同输入逐字节一致；
  基线报告均值与 post 报告基线列不一致、或报告声明的模型名与预期不符时直接 raise（失败不静默）。

数据事实（写入 JSON 的 provenance，2026-11-08 平台上传校验回执）：
  Febrl3 共 5000 条记录 / 2000 实体；训练 1600 实体（10478 对）/ 测试 400 实体（2598 对）；
  硬负例 10998 条（覆盖 3992 个训练查询）。训练 use_hard_negatives=true。

用法：python3 scripts/update_open_benchmark.py [--reports data_open/eval_reports] [--out data/open_benchmark.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (tag, 基模名, 微调模型名) —— 与平台 create_finetune_job 参数一致
MODELS: list[tuple[str, str, str]] = [
    ("inf15", "inf-retriever-v1-1.5b-pro", "febrl3-er-inf15"),
    ("nemotron", "nemotron-3.5-embed-1b-bf16", "febrl3-er-nemotron"),
    ("gemma", "embeddinggemma-2", "febrl3-er-gemma"),
]

# 指标名 → JSON 键（与页面渲染键一致）
METRICS: list[tuple[str, str]] = [
    ("NDCG@1", "ndcg1"),
    ("NDCG@5", "ndcg5"),
    ("NDCG@10", "ndcg10"),
    ("Recall@1", "recall1"),
    ("Recall@5", "recall5"),
    ("Recall@10", "recall10"),
    ("MAP@10", "map10"),
]

# 平台上传校验回执（upload_dataset format_validation）与数据切分事实
DATASET_FACTS = {
    "name": "Febrl3",
    "source": "recordlinkage 官方发行版（含 OCR 错、音译错、字段缺失/冲突）",
    "records": 5000,
    "entities": 2000,
    "train_entities": 1600,
    "train_pairs": 10478,
    "test_entities": 400,
    "test_pairs": 2598,
    "hard_negative_count": 10998,
    "hard_negative_query_count": 3992,
    "use_hard_negatives": True,
    "platform": "inf-retriever 训练平台（evaluator: pytrec_eval, 固定 test split）",
    "measured_at": "2026-11-08",
    "reports_in_repo": "data_open/eval_reports/",
}

ROW_POST = re.compile(r"^\|\s*([A-Za-z]+@\d+)\s*\|")
ROW_WTL = re.compile(r"win\s*(\d+)\s*/\s*tie\s*(\d+)\s*/\s*loss\s*(\d+)")
MODEL_LINE = re.compile(r"^- \*\*(基线模型|训练后模型)\*\*：(.+)$", re.M)


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_post(path: Path) -> tuple[dict[str, float], dict[str, float], tuple[int, int, int]]:
    """post 报告：`| 指标 | 基线 | 训练后 | 变化 |…`，每个指标取首次出现（整体对比节）。"""
    before: dict[str, float] = {}
    after: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_POST.match(line)
        if not m:
            continue
        name = m.group(1)
        if name in before:  # 只取每个指标的首次出现（后面是宏平均重复行）
            continue
        cs = cells(line)
        # 形如 | NDCG@10 | 0.986 | 0.998 | +0.013 | 1/1 | 或去掉覆盖列
        nums = [c for c in cs[1:] if re.fullmatch(r"\d\.\d+", c)]
        if len(nums) < 2:
            continue
        before[name], after[name] = float(nums[0]), float(nums[1])
    wtl_m = ROW_WTL.search(path.read_text(encoding="utf-8"))
    if not wtl_m:
        raise ValueError(f"{path.name}: 找不到 win/tie/loss 行")
    wtl = tuple(int(g) for g in wtl_m.groups())
    missing = [n for n, _ in METRICS if n not in before]
    if missing:
        raise ValueError(f"{path.name}: post 报告缺指标 {missing}")
    return before, after, wtl


def parse_baseline(path: Path) -> dict[str, float]:
    """baseline 报告：`| 指标 | 均值 | 1/1 |`。"""
    out: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_POST.match(line)
        if not m or m.group(1) in out:
            continue
        cs = cells(line)
        nums = [c for c in cs[1:] if re.fullmatch(r"\d\.\d+", c)]
        if nums:
            out[m.group(1)] = float(nums[0])
    missing = [n for n, _ in METRICS if n not in out]
    if missing:
        raise ValueError(f"{path.name}: baseline 报告缺指标 {missing}")
    return out


def check_model_names(path: Path, expect_base: str, expect_tuned: str) -> None:
    pairs = MODEL_LINE.findall(path.read_text(encoding="utf-8"))
    got = dict(pairs)
    if got.get("基线模型") != expect_base or got.get("训练后模型") != expect_tuned:
        raise ValueError(f"{path.name}: 模型名对不上：{got} ≠ {expect_base} / {expect_tuned}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reports", type=Path, default=ROOT / "data_open" / "eval_reports")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "open_benchmark.json")
    args = ap.parse_args()

    models_out = []
    for tag, base, tuned in MODELS:
        post_p, base_p = args.reports / f"post-{tag}.md", args.reports / f"baseline-{tag}.md"
        for p in (post_p, base_p):
            if not p.exists():
                raise SystemExit(f"缺少报告文件: {p}")
        check_model_names(post_p, base, tuned)

        post_before, post_after, wtl = parse_post(post_p)
        standalone = parse_baseline(base_p)
        for name, _ in METRICS:  # 两次独立评测的基线必须一致，否则口径有问题
            if abs(post_before[name] - standalone[name]) > 1e-9:
                raise SystemExit(
                    f"{tag}: 基线不一致 {name}: 独立评测={standalone[name]} "
                    f"post 报告基线列={post_before[name]}（评测口径漂移，先查平台）"
                )
        models_out.append({
            "key": tag,
            "base_model": base,
            "tuned_model": tuned,
            "win_tie_loss": list(wtl),
            "before": {k: standalone[n] for n, k in METRICS},
            "after": {k: post_after[n] for n, k in METRICS},
        })

    payload = {"provenance": DATASET_FACTS, "models": models_out}
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    for m in models_out:
        print(f"  {m['key']}: NDCG@10 {m['before']['ndcg10']:.3f}→{m['after']['ndcg10']:.3f}"
              f"  Recall@5 {m['before']['recall5']:.3f}→{m['after']['recall5']:.3f}"
              f"  w/t/l={m['win_tie_loss']}")


if __name__ == "__main__":
    main()
