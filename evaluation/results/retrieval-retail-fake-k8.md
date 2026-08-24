# Retrieval evaluation - retrieval-retail-fake-k8

**Retrieval only.** No model call, no policy engine, no execution. This measures one thing: whether
the context pack handed to the model contained the tables the reference query actually needed.

Fake embeddings are deterministic vectors, so this measures the lexical and fusion path, not semantic similarity. It is a regression gate, not a statement about retrieval quality.

| Field | Value |
|---|---|
| Measured | 2026-08-24T13:42:13+00:00 |
| k | 8 |
| Embeddings | `fake` |
| Embedding model | `fake:hashed-tokens:128:n:1` |
| Cases scored | 50 |
| Table recall@8 | **87 of 91 (95.6%)** |
| Mean reciprocal rank | **0.9183** |
| Measures model quality | no |

## Per dataset

| Dataset | Cases | Table recall@8 | MRR |
|---|---|---|---|
| retail | 50 | 87 of 91 (95.6%) | 0.9183 |

## Reproduce

```
uv run python -m app.evaluation.cli retrieval --dataset retail --k 8 --embeddings fake
```

## Environment

```
Windows 10, Python 3.13.13
```
