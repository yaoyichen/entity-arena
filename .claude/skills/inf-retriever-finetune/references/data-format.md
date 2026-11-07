# Data Format

After extraction, an uploaded archive should contain one or more structures like:

```text
dataset/
├── queries.jsonl
├── corpus.jsonl
├── neg/neg.jsonl (optional)
└── qrels/
    ├── train.tsv
    └── test.tsv
```

Each line in `queries.jsonl` is one JSON object:

```json
{"_id": "q1", "text": "query text"}
```

Each line in `corpus.jsonl` is one JSON object; `title` is optional:

```json
{"_id": "d1", "title": "optional title", "text": "document text"}
```

Each `qrels/*.tsv` file is tab-separated and has at least three columns. A header row is allowed and is skipped during validation and reading:

```text
query-id<TAB>corpus-id<TAB>score
```

The optional `neg/neg.jsonl` annotates hard negatives for training queries. Each line is one JSON object; ids in `neg` must exist in `corpus.jsonl`, and every query appearing in the training qrels must have hard negatives:

```json
{"qid":"q1","neg":["d2","d3"]}
```

Upload validation returns `has_hard_negatives`, `hard_negative_query_count`, and `hard_negative_count` in `format_validation.datasets`. When creating the training job, `create_finetune_job` accepts `use_hard_negatives` to choose whether to use those annotated negatives; `false` ignores them and samples random negatives.

## Valid Example

```text
queries.jsonl:
{"_id":"q1","text":"What is retrieval-augmented generation"}

corpus.jsonl:
{"_id":"d1","title":"RAG","text":"Retrieval-augmented generation retrieves documents before generating an answer."}

qrels/train.tsv:
q1<TAB>d1<TAB>1
```

## Common Errors

- Files are nested under unrelated directories and the root has no `queries.jsonl` and `corpus.jsonl`.
- A JSONL line is not an object, such as a bare string or array.
- Missing or empty `_id` or `text`.
- Duplicate `_id` within the same file.
- A qrels row has fewer than three columns, or uses spaces instead of tabs.
- A qrels `query_id` or `corpus_id` does not exist in the corresponding JSONL file.
- Missing `qrels/test.tsv`: format validation remains valid, but evaluation may fail or be skipped; warn the user before training.

`format_validation.errors` contains file, line number, and reason. When explaining to the user, show the first three errors, then ask them to fix and re-upload. Use `preview_dataset` to inspect the samples the service actually reads.
