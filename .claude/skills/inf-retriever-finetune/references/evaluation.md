# Metric Interpretation

Common metrics:

- `Recall@k`: proportion of queries for which the correct document appears in the top k results; measures recall coverage.
- `MRR@10`: mean reciprocal rank of the first correct result; measures how early the first correct result appears.
- `NDCG@10`: rank-discounted gain; measures overall ranking quality.
- `MAP`: mean average precision across queries; useful when multiple documents are relevant.
- `QA metrics`: text-match metrics for question answering; use only as supporting evidence, not as a replacement for retrieval metrics.

## Judging Improvement

- Compare with a baseline using the same dataset, split, and base model.
- Focus on whether the primary metric (usually `NDCG@10`) improves, while checking whether Recall declines.
- Differences within 1% on small datasets are usually unstable; explain the limited confidence.
- Do not directly compare runs whose test sets, `top_k`, or data filtering differ.

## Getting Reports

1. Use `get_job_status` to query an evaluation or pipeline job.
2. Inspect the evaluation/report information in the result.
3. Reports include aggregate metrics, charts, and model comparisons.
4. A missing report does not mean training failed; first check the test set and logs.
