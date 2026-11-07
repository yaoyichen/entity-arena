---
name: inf-retriever-finetune
description: English guide for uploading retrieval datasets, fine-tuning embedding models, checking training status, evaluating models, deploying inference services, and checking training/inference token usage through the authorized `inf-retriever` MCP Connector.
---

# INF Retriever Fine-Tuning Guide

## Startup Check

1. Confirm that the MCP Connector named `inf-retriever` is connected.
2. If the client shows a different display name, identify the target only when it exposes the Inf Retriever tool set in `references/api.md`; do not route these calls to another MCP.
3. If connected, continue with the full workflow.
4. If not connected, enter degraded mode: explain the data format, help inspect local data, and guide the user through Connector authorization.
5. After authorization succeeds, resume the full workflow.

## Conversation Flow

1. When the user expresses an intent to fine-tune, first confirm the purpose, data location and size, and base-model preference.
2. Call `list_resources` to query existing datasets and available models.
3. If the user needs to upload data, read `references/data-format.md` before calling `upload_dataset`.
4. Check `format_validation.valid`. Continue only when it is `true`; if it is `false`, explain the specific errors and do not create a training job. After a successful upload, send the returned `viewer_url` to the user (it opens the Data Center with that dataset's viewer open).
5. Before training, restate the dataset, base model, output model name, train/pipeline mode, whether hard negatives are used (`use_hard_negatives`, default `true`), and whether evaluation is enabled, then ask the user to confirm.
6. After confirmation, call `create_finetune_job` and send the returned `detail_url` to the user (it opens the Job Center with that job's detail drawer open).
7. Use the returned `job_id` to call `get_job_status`.
8. If the job fails, read `references/troubleshooting.md`; enable logs when necessary.
9. After success, ask whether to deploy. Call `deploy_model` only after the user confirms.
10. After deployment, get `service_url`, `curl_command`, and `page_url` from `list_resources(types=["services"])`; the top-level fields of `get_job_status` are not guaranteed to contain the URL. Send the user the `curl_command` (a ready-to-run invocation example) and the `page_url` (Deployed Services page link), then ask whether they want a further integration example.
11. If the user requests a standalone evaluation of any model without training, call `evaluate_model`, send the returned `detail_url` to the user (it opens the Evaluation Center task detail page — not the Job Center), and track the result with `get_job_status`.
12. If the user asks to start, stop, or delete a service, first restate the target service and action; after confirmation, call `manage_service`.
13. If the user asks how many tokens a model consumed, training versus inference usage, or deployed-service call volume, call `get_model_usage`; service-level details are available through the `usage` field returned by `list_resources`.

## Link Feedback Rules

Console links returned by tools must be forwarded to the user verbatim; do not reassemble or omit them. Each link opens the matching page with the corresponding panel already open:

| Scenario | Link to send |
|---|---|
| Dataset uploaded | `viewer_url` (Data Center + dataset viewer) |
| Train / pipeline / deploy job created | `detail_url` (Job Center + job detail drawer) |
| Evaluation job created or running | `detail_url` (Evaluation Center + task detail; not the Job Center) |
| Evaluation finished with a report | `evaluation.report_preview_url` (online preview) + `evaluation.report_downloads` (ZIP and PDF download links in both Chinese and English; provide all of them) |
| Deployment finished | `page_url` (Deployed Services page) + `curl_command` (invocation example) |
| User wants to see trained models | Model Center page: console address + `/#/models` (this page has no sub-content and needs no id) |

## Short-Turn Strategy

| User says | Action |
|---|---|
| Is it finished? | Call `get_job_status` |
| Why did it fail? | Check status and logs; read troubleshooting |
| Can 800 records train? | Give data-size advice; do not start training directly |
| Train another version | Reuse the previous configuration and confirm only differences; if unspecified, keep the previous `use_hard_negatives` value |
| Are the metrics good? | Read evaluation and compare with the baseline |
| How do I use the URL? | Generate an integration example from the final `service_url` |
| How many tokens were used? | Call `get_model_usage` and report training and inference Query/Document tokens separately |
| How many service calls happened? | Call `list_resources(types=["services"])` and read the matching service's `usage` |
| Evaluate only, no training | Call `evaluate_model`; confirm the model and dataset |
| Service is down/unneeded | Check status, then use `manage_service` to start, stop, or delete |

## Resource Routing

| Scenario | Read |
|---|---|
| Calling tools | `references/api.md` |
| Inspecting data | `references/data-format.md` |
| Debugging errors | `references/troubleshooting.md` |
| Interpreting metrics | `references/evaluation.md` |

## Safety Boundaries

This skill contains no endpoint, token, or account information. All external actions run through the authorized `inf-retriever` MCP Connector; installing this skill does not automatically authorize the Connector.

Require explicit user confirmation before creating a training job, creating a pipeline job, deploying a model, deleting resources, starting or stopping a service, deleting a service, or starting a standalone evaluation. Do not persist `job_id`, `dataset_id`, `model_name`, `serving_id`, or `service_url` in this skill.
