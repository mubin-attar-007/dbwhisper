# Retrieval evaluation - retrieval-retail-local-k8

**Retrieval only.** No model call, no policy engine, no execution. This measures one thing: whether
the context pack handed to the model contained the tables the reference query actually needed.

Local CPU embeddings - the same model a self-hosted deployment uses.

| Field | Value |
|---|---|
| Measured | 2026-08-24T13:42:37+00:00 |
| k | 8 |
| Embeddings | `local` |
| Embedding model | `fastembed:BAAI/bge-small-en-v1.5:384:n:1` |
| Cases scored | 50 |
| Table recall@8 | **90 of 91 (98.9%)** |
| Mean reciprocal rank | **0.9767** |
| Measures model quality | no |

## Per dataset

| Dataset | Cases | Table recall@8 | MRR |
|---|---|---|---|
| retail | 50 | 90 of 91 (98.9%) | 0.9767 |

## Reproduce

```
uv run python -m app.evaluation.cli retrieval --dataset retail --k 8 --embeddings local
```

## Environment

```
Windows 10, Python 3.13.13
```
