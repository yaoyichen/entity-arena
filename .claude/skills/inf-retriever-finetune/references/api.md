# Inf Retriever MCP Tool Contract

Target MCP name: `inf-retriever`. If the client displays a different name, match the target by this tool contract; do not call equivalent tools on another MCP.

| Tool | When to call | Input | Return | Error handling |
|---|---|---|---|---|
| `upload_dataset` | User provides an archive | `file_path` or `content_base64` (choose one), optional `name` | `upload_id`, `format_validation`, `viewer_url` | If validation fails, explain the error and do not create a training job |
| `create_finetune_job` | After parameters are confirmed | Required `dataset_ids`, `base_model_name`, `output_name`; optional `mode` (default `train`; supports `train`/`pipeline`), `use_hard_negatives` (default `true`; `false` ignores neg/neg.jsonl and uses random negatives), `train_name`, `serving_name` (pipeline), `evaluate` (default `true`), `evaluation_name`, `generate_report` (default `true`) | `job_id`, `status`, `mode`, `detail_url` | Do not retry unchanged after a parameter error |
| `get_job_status` | Query a job | `job_id`, optional `include_log` and `log_offset` | `{"job": {...}, "detail_url", "evaluation"?, "service"?, "log": {...}?}` | On failure, read troubleshooting |
| `deploy_model` | User confirms deployment | `model_name` or `job_id` (choose one), optional `serving_name` | Deployment `job_id`, `detail_url` | The creation response usually has no URL; continue checking status |
| `evaluate_model` | Standalone evaluation of any model | Required `dataset_ids`; `model_name` (trained model) or `base_model_name` (base model), choose one; optional `name`, `generate_report` (default `true`) | `job_id`, `status`, `model_name`, `detail_url` | Track with `get_job_status`; a missing report does not mean failure |
| `manage_service` | Start/stop/delete after confirmation | Required `action` (`start`/`stop`/`delete`); `serving_id` or `job_id` or `serving_name` to resolve the target service | `action`, `serving_id`, `page_url`, platform response | Delete first stops a running service and may take over 2 minutes |
| `list_resources` | Startup check or resource selection | Optional `types` | Configuration, datasets (with `viewer_url`), models, jobs, services (with `page_url`, `curl_command`) | If the Connector is not connected, enter degraded mode |
| `get_model_usage` | User asks about model token usage | Required `model_name` | `training_token_usage` and `inference_token_usage`, each with `query_tokens`, `document_tokens`, and `total_tokens` | If the model is unknown, confirm it through `list_resources` first; do not guess |
| `preview_dataset` | Inspect samples | `dataset_id`, optional `file` | File list and sample rows, `viewer_url` | Use only for manual inspection; it does not replace format validation |

After a successful upload, read `format_validation.datasets[].has_hard_negatives` and tell the user whether the data contains hard negatives. Before creating a training job, confirm the value of `use_hard_negatives`.

## Console Links

Forward tool-returned links to the user verbatim. Each link opens the matching page with the corresponding panel already open:

| Field | Form | Opens |
|---|---|---|
| `viewer_url` | `#/data/<upload_id>` | Data Center with the dataset viewer open |
| `detail_url` (train/pipeline/deploy) | `#/jobs/<job_id>` | Job Center with the job detail drawer open |
| `detail_url` (evaluation) | `#/evaluation/<run_name or job_id>` | Evaluation Center task detail; a job_id form resolves to the run_name once the run exists |
| `evaluation.report_preview_url` | `#/evaluation/<run_name>/report` | Evaluation Center report online preview |
| `evaluation.report_downloads` | `/evaluations/<run_name>/report.zip\|report.pdf` (`lang=zh/en`) | Markdown (ZIP archive) and PDF downloads in both Chinese and English |
| `page_url` | `#/services/<serving_id>` | Deployed Services page focused on that service |
| `curl_command` | text | Ready-to-run embeddings invocation example |

The Model Center has no sub-content to locate; share the console address + `/#/models` directly.

Notes:

- `create_finetune_job` consumes compute resources; obtain explicit user confirmation first.
- `deploy_model` creation responses usually do not contain `service_url`; top-level fields in `get_job_status` are also not guaranteed to contain it. Get the final URL from `service_url` in `list_resources(types=["services"])`, or look in `get_job_status` under `result.final_response.data.exposed_domains`.
- `evaluate_model` is the standalone evaluation entry point corresponding to the console evaluation center; it does not require a prior training job. `create_finetune_job(mode="pipeline")` with `evaluate` controls automatic post-training evaluation. Both use the same underlying engine.
- `manage_service` deletion is irreversible. Before acting, restate the `serving_name`/`serving_id` and action, and obtain user confirmation.
- `list_resources(types=["services"])` returns each deployed service's `usage`, including call count, success/failure count, and Query/Document token usage. Do not request or persist any embedding response content.
- For `mode=pipeline`, `generate_report` is passed through to the request. Current report behavior depends on the service implementation; this skill must not promise that the switch always takes effect.
- Call `upload_dataset` only when the user provides an explicit archive path or content; do not guess paths.
