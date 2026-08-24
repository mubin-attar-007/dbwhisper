# Retrieval evaluation - retrieval-all-local-k8

**Retrieval only.** No model call, no policy engine, no execution. This measures one thing: whether
the context pack handed to the model contained the tables the reference query actually needed.

Local CPU embeddings - the same model a self-hosted deployment uses.

| Field | Value |
|---|---|
| Measured | 2026-08-24T13:43:18+00:00 |
| k | 8 |
| Embeddings | `local` |
| Embedding model | `fastembed:BAAI/bge-small-en-v1.5:384:n:1` |
| Cases scored | 135 |
| Table recall@8 | **217 of 219 (99.1%)** |
| Mean reciprocal rank | **0.9802** |
| Measures model quality | no |

## Per dataset

| Dataset | Cases | Table recall@8 | MRR |
|---|---|---|---|
| retail | 50 | 90 of 91 (98.9%) | 0.9767 |
| saas_ops | 43 | 62 of 62 (100.0%) | 0.9884 |
| synthetic_snf | 42 | 65 of 66 (98.5%) | 0.9762 |

## Reproduce

```
uv run python -m app.evaluation.cli retrieval --dataset retail --dataset saas_ops --dataset synthetic_snf --k 8 --embeddings local
```

## Environment

```
Windows 10, Python 3.13.13
```
