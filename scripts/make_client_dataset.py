#!/usr/bin/env python3
"""客户数据 → inf-retriever 训练平台上传包（数据合同 docs/data-contract.md §1–§2 的入口）。

输入三件（任意客户 schema，无需 Febrl 约定）：
  --records   记录文件（CSV 或 JSONL，UTF-8）：一行一条记录；
              必含 record_id 列（唯一、非空）；缺失值留空——不垫占位符。
  --fieldmap  字段角色表（CSV：field,role[,normalize][,neg_key]；或 JSON 数组）：
              role ∈ embedding / unique-key / ignore；
              normalize ∈ lower / upper / date8（可选）；
              neg_key=0 可把某 embedding 字段排除出硬负例挖掘（默认 1）。
  [--pairs]   格式 B 标签文件（CSV：record_id_a,record_id_b,label，label ∈ {0,1}）。
              省略时走格式 A：记录文件里的 entity_id 列即真值。

输出（BEIR 风格，见 .claude/skills/inf-retriever-finetune/references/data-format.md）：
  <out>/queries.jsonl / corpus.jsonl   全部记录（序列化文本同池双用）
  <out>/qrels/train.tsv / test.tsv     切分后的正例对（score=1）
  <out>/neg/neg.jsonl                  硬负例（格式 A=同关键字段值异实体自动挖掘；
                                       格式 B=客户 label=0 金标负例）
  <out>/intake_report.json             机器可读溯源（切分/规则全文，供演示页复用）
  --zip 时另出 <out>.zip               确定性打包，可直接 upload_dataset

共享逻辑在 src/entity_arena/intake.py（序列化/切分与后续租户体验页、
在线打分同一份——三处口径永远一致）。校验失败全量收集、带原始行号一次性报告。

用法：
  python3 scripts/make_client_dataset.py \
      --records customer.csv --fieldmap fieldmap.csv --out .tmp/client_er --zip
  python3 scripts/make_client_dataset.py \
      --records customer.csv --fieldmap fieldmap.csv \
      --pairs labels.csv --out .tmp/client_er          # 格式 B
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from entity_arena.intake import IntakeError, run_intake  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", type=Path, required=True, help="记录文件（CSV/JSONL）")
    ap.add_argument("--fieldmap", type=Path, required=True,
                    help="字段角色表（CSV: field,role[,normalize][,neg_key] 或 JSON）")
    ap.add_argument("--pairs", type=Path, default=None,
                    help="格式 B 标签文件（CSV: record_id_a,record_id_b,label）；"
                         "省略则要求记录文件含 entity_id 列（格式 A）")
    ap.add_argument("--out", type=Path, default=Path(".tmp/client_er"))
    ap.add_argument("--test-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-neg", type=int, default=5, help="每查询硬负例上限")
    ap.add_argument("--zip", dest="make_zip", action="store_true",
                    help="另出确定性 <out>.zip（可直接 upload_dataset）")
    args = ap.parse_args()

    try:
        r = run_intake(args.records, args.fieldmap, args.pairs, out=args.out,
                       seed=args.seed, test_frac=args.test_frac,
                       max_neg=args.max_neg, make_zip=args.make_zip)
    except IntakeError as e:
        errs = e.errors if isinstance(e.errors, list) else [str(e)]
        print(f"✗ intake 校验失败（{len(errs)} 条，修完重跑）：", file=sys.stderr)
        for msg in errs[:20]:
            print(f"  - {msg}", file=sys.stderr)
        if len(errs) > 20:
            print(f"  …另有 {len(errs) - 20} 条", file=sys.stderr)
        raise SystemExit(1) from e

    fm = "A（entity_id 列）" if r["label_format"] == "A" else "B（逐对标签）"
    print(f"records={r['records']}  label_format={fm}"
          + (f"  entities={r['entities']}" if r["label_format"] == "A"
             else f"  positive_pairs={r['positive_pairs']} zero_pairs={r['zero_pairs']}"))
    sp = r["split"]
    if r["label_format"] == "A":
        print(f"train: {sp['train_entities']} ents / {r['qrels']['train']} qrel pairs | "
              f"test: {sp['test_entities']} ents / {r['qrels']['test']} qrel pairs")
    else:
        print(f"train: {sp['train_pairs']} pairs / {r['qrels']['train']} qrel pairs | "
              f"test: {sp['test_pairs']} pairs / {r['qrels']['test']} qrel pairs")
    ng = r["negatives"]
    print(f"hard negatives: {ng['mined']} mined, {ng['fallback_queries']} queries fell back to random")
    print(f"embedding fields: {', '.join(r['embedding_fields'])}"
          + (f" | unique-key: {', '.join(r['unique_key_fields'])}" if r["unique_key_fields"] else ""))
    for w in r["warnings"]:
        print(f"  ⚠ {w}")
    print(f"package written to {args.out}/"
          + (f"  (+ {r.get('zip')})" if args.make_zip else ""))
    print("下一步：upload_dataset 上传 zip（或目录内容打包），通过 format_validation 后"
          " create_finetune_job（use_hard_negatives=true）")


if __name__ == "__main__":
    main()
