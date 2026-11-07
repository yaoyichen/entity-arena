# Troubleshooting

## `inf-retriever` Connector Not Connected

- Symptom: tools are unavailable.
- Cause: the `inf-retriever` MCP Connector is not installed, enabled, or authorized.
- Investigation: check Connector status; if not connected, enter degraded mode.
- Suggested wording: “The training and deployment service is not connected. I can first help you check the data format.”

## Invalid Service Token

- Symptom: a tool reports service authentication failure.
- Cause: the training/deployment service token is invalid or not configured.
- Investigation: ask an administrator to update Connector authorization; do not ask the user to send secrets in chat.
- Suggested wording: “Authorization failed. Please update it in the Connector authorization settings.”

## Unsupported Upload Format

- Symptom: parameter validation fails before upload.
- Cause: the archive is not in a supported compression format.
- Investigation: convert it to `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`, or `.txz`.
- Suggested wording: “Please first package the data directory as a supported archive.”

## Data Format Validation Failure

- Symptom: `format_validation.valid=false`.
- Cause: missing files, invalid fields, duplicate IDs, or invalid qrels references.
- Investigation: explain the first three errors; call `preview_dataset` if needed.
- Suggested wording: “The data structure has issues. Fix these rows and upload again.”

## Missing Dataset or Base Model

- Symptom: job creation is rejected by the service.
- Cause: `dataset_id` was deleted, or `base_model_name` is not configured.
- Investigation: call `list_resources` and verify.
- Suggested wording: “The service does not currently have this resource. Let me help you choose again.”

## Training Job Failure

- Symptom: `get_job_status` returns `FAILED`.
- Investigation: enable logs and identify platform, dependency, or data errors; do not immediately resubmit.
- Suggested wording: “The job failed. The key reason in the logs is...”

## Deployment Job Failure

- Symptom: the deploy job is `FAILED`.
- Investigation: check `serving_id`, image, and platform status in logs; confirm the model is registered.
- Suggested wording: “Deployment did not finish. Check the cause from the logs before retrying.”

## Missing Evaluation Report

- Symptom: the job succeeds but no report entry is available.
- Cause: the test set is missing, report generation failed, or the pipeline did not pass the switch through.
- Investigation: check `has_test`, report fields in the job result, and logs.
- Suggested wording: “Training succeeded, but the report was not generated; a missing report does not mean training failed.”

## Service Start/Stop/Delete Failure

- Symptom: `manage_service` returns a platform error.
- Investigation: first confirm `serving_id` and platform state through `list_resources(types=["services"])`. When deleting a running service, the server first stops it and then deletes it, which may require waiting.
- Suggested wording: “The platform did not complete this operation. The current state is... Try later or handle it in the console.”

## Standalone Evaluation Failure

- Symptom: the job created by `evaluate_model` is `FAILED`.
- Investigation: confirm `model_name` is registered in `list_resources`, the dataset exists and contains `qrels/test.tsv`, then inspect `get_job_status` logs.
- Suggested wording: “Evaluation failed. The key reason in the logs is...”
