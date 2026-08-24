# Dependency and Licence Register

**Snapshot:** 2026-08-24T09:30Z · branch `feat/dbwhisper-v2` · HEAD `e5c89f3` (working tree dirty — all v2 work is uncommitted)
**Authority for this document:** `pyproject.toml` (128 lines, md5 `6260fd4620a1420fdb90f02195bc4fa5`) and `web/package-lock.json` (lockfileVersion 3, 589 `node_modules/` entries).
**Deliverable of:** `docs/v2/IMPLEMENTATION_ROADMAP.md:41` — "Update `DEPENDENCY_AND_LICENSES.md`".

## How to read this document

Every factual claim cites `path:line`. Two verification tags are used throughout and they mean different things:

| Tag | Meaning |
|---|---|
| **RAN** | A command was executed and its output is reported. Reproduce it with the command given. |
| **READ** | A file was opened and the cited line read. |
| **from knowledge — verify before release** | The licence was *not* read from installed package metadata or from a shipped LICENSE file. It comes from the author's background knowledge. **Treat every one of these as unverified.** They cluster in §5 (an extra that is not installed), Appendix A (packages that do not exist in this repo yet) and the two Debian packages in the Docker image. |

Licence values tagged `expr` come from the `License-Expression` metadata field (PEP 639, the most reliable source); `classifier` from a `License ::` trove classifier; `License-field` from the free-text `License` field; `LICENSE file` from reading the shipped licence text.

Reproduce any Python licence row with:

```bash
uv run python -c "
from importlib.metadata import metadata, version
m = metadata('fastapi')
print(version('fastapi'), m.get('License-Expression'), m.get_all('Classifier'))
"
```

### Caveat: the tree was being edited during this audit

**RAN**, repeatedly: `ls app/` and `md5sum pyproject.toml`. At the first listing there was no `app/llm/`, no `app/embeddings/`, no `app/retrieval/`. All three exist now. `pyproject.toml` changed once during the audit as well — it went from 127 lines (md5 `98798197f8a1fe48298b02549d7b0542`) to 128 lines (md5 `6260fd4620a1420fdb90f02195bc4fa5`) with the addition of `langgraph-checkpoint-sqlite` at `pyproject.toml:53`, **which shifted every line number after 52 by one**. A parallel agent was landing the Phase 4 model layer while this register was being compiled (`docs/v2/IMPLEMENTATION_ROADMAP.md:173` records that work).

Four conclusions changed mid-audit and are stated here in their **final, re-verified** form:

* `httpx` went from no importers to `app/llm/providers/ollama.py:22`.
* `fastembed` went from no importers to `app/embeddings/local.py:58` — it is now the default embedding provider, not dead weight.
* `langchain-huggingface` went from being the *only wired* embedding backend to having **no importers at all**, which orphans the entire `local-embeddings` extra (§5.1).
* `langgraph-checkpoint-sqlite` appeared as a new base dependency with no importers yet.

Every line number and importer count in this document is a 2026-08-24T09:30Z snapshot against md5 `6260fd4620a1420fdb90f02195bc4fa5`. **Re-run the scans in §11.4 before relying on them**, and check the md5 first.

---

## 1. Policy

### 1.1 Licence policy

1. **Runtime dependencies must be permissive.** MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, PSF, MIT-CMU, or a dual expression containing one of these. These impose attribution and nothing more.
2. **Weak copyleft (MPL-2.0, LGPL-2.1, LGPL-3.0) is permitted only as an unmodified, separately-installed library**, and must be listed in §4.1 with the obligation it creates written out. If a file from an MPL package is ever patched in-tree, that file becomes MPL and the exception ends.
3. **Strong copyleft (GPL, AGPL) is prohibited in runtime dependencies**, transitively included. **RAN** (full scan of all 172 installed distributions and all 843 `package.json` files under `web/node_modules`): there is currently **zero** GPL, AGPL, SSPL or BUSL in either tree. See §4.3 for the one false positive a naive scanner will raise.
4. **Dev-time tooling may be copyleft** where it is a CLI that is never imported and never distributed. `hypothesis` (MPL-2.0, `pyproject.toml:83`) is the only current instance.
5. **Non-OSI binaries must be declared.** The Docker image contains one (§2.3). The MIT LICENSE covers DBWhisper's source and cannot cover it.
6. **No dependency may be added without answering the four questions below in the pull request that adds it.**

### 1.2 The four questions every new dependency must answer

| # | Question | Why it is asked |
|---|---|---|
| **1** | **What does it do?** One sentence, concrete. Not "helps with SQL" — "builds a dialect-aware AST so a read-only guarantee can be a property of the parse tree rather than of a regex." | A dependency nobody can describe in one sentence is a dependency nobody can defend when it breaks. |
| **2** | **What does it replace?** Name the incumbent — existing dependency, stdlib module, or hand-written code — and say why the replacement is better. If it replaces nothing, say so explicitly and justify the net addition. | Prevents the accretion of overlapping stacks. §6.2 documents one that already accreted. |
| **3** | **What is its licence?** Read from installed metadata (`License-Expression` first), not from memory, and add the row to this document in the same pull request. | Every "from knowledge" tag in this file is a claim nobody has checked. §4.2 shows two packages whose own metadata is simply wrong. |
| **4** | **What breaks if it disappears?** Name the code path and the failure mode if the package is yanked, relicensed, or abandoned. If the answer is "nothing", it should not be a dependency. | This is the question that identified `networkx`, `pandas` and `numpy` as deletable (§3.2), and that makes the `psycopg` LGPL exposure a conscious choice rather than an accident. |

### 1.3 Standing exception

**RAN** (grep for `pip-licenses`, `license-checker`, `licensecheck`, `reuse`, `fossa`, `scancode` across `.github/`, `pyproject.toml`, `web/package.json`): **zero hits. There is no automated licence gate in CI.** The workflow has three jobs — `backend` (`.github/workflows/ci.yml:13`), `frontend` (`:46`) and `secrets-scan` (`:69`) — and none checks a licence. This register is maintained by hand, which is why §11 specifies a cadence. Adding a gate is recommendation 4 in §11.3.

---

## 2. Project licence

### 2.1 What DBWhisper is licensed under

**READ**, `LICENSE` (21 lines):

* `LICENSE:1` — `MIT License`
* `LICENSE:3` — `Copyright (c) 2026 Mubin Attar. All rights reserved.`
* `LICENSE:5-21` — the standard, unmodified MIT grant.

Two hygiene notes, both cosmetic:

* The phrase **"All rights reserved." on `LICENSE:3`** is vestigial (a Buenos Aires Convention formality) and sits oddly against the MIT grant that immediately follows. It does not restrict the grant, but it invites a reader to ask whether it was meant to. Recommend deleting the phrase.
* **`pyproject.toml` declares no licence.** **READ**, `pyproject.toml:1-6`: there is no `license` field and no `classifiers` list. A scanner reading only the package metadata would find no licence at all. `[tool.uv] package = false` (`pyproject.toml:88-90`, "Run-from-source project (uvicorn app.main:app); not a distributable package") means nothing is ever built or published, so this has no legal consequence today — but it costs one line to fix and removes a false negative from any future scan.

### 2.2 Compatibility verdict

**Distributing DBWhisper's source under MIT is clean.** Of 172 installed Python distributions (**RAN**: `uv pip list | tail -n +3 | wc -l` → `172`; `importlib.metadata.distributions()` independently enumerates 172), 165 are permissive and impose nothing beyond attribution. The 589-entry Node tree contains no copyleft at all in the shipped path except two MPL packages (§4.1).

The obligations that do exist are listed in §4.1. None reaches DBWhisper's own code, because every one is an unmodified library installed separately by pip or npm rather than a modified source file compiled in.

### 2.3 The one genuine exception — the Docker image is not a purely MIT artifact

**READ**, `Dockerfile:38`:

```dockerfile
&& ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
```

installed from the Microsoft apt repository added at `Dockerfile:33-36`. **msodbcsql18** — Microsoft ODBC Driver 18 for SQL Server — is distributed under a **proprietary Microsoft EULA, not any OSI licence** *(from knowledge — verify before release)*. The `ACCEPT_EULA=Y` on that line is the build accepting that EULA non-interactively.

**Consequence, stated plainly: redistribution of the built container image is governed by the Microsoft driver EULA, not by DBWhisper's MIT LICENSE.** The MIT licence covers the source in this repository. It does not and cannot cover a third-party proprietary binary that the image ships.

What was verified here is only the apt install at `Dockerfile:38` and the repository added at `Dockerfile:33-36`. **The EULA text itself was not read.** Before this image is published anywhere — a public registry, a customer, a demo host — someone must read the actual Microsoft EULA and record here whether redistribution in this form is permitted. Until that happens, treat the image as internal-use-only.

The image also installs **unixODBC** (**READ**, `Dockerfile:32`), the driver manager msodbcsql18 plugs into. Debian's `unixodbc` source package is **LGPL-2.1 for the `libodbc` runtime with some GPL-2.0 command-line tools** *(from knowledge — verify before release)*. The LGPL runtime is what gets linked; the GPL tools are not linked into anything DBWhisper ships. **RAN**: `docker build` was not attempted — the Docker daemon is not running on this machine — so the actual Debian dependency closure inside the image was not enumerated.

---

## 3. Python runtime dependencies

All 41 base dependencies from `pyproject.toml:9-53`. Versions and licences **RAN** via `importlib.metadata`, preferring `License-Expression`, then classifiers, then the free-text `License` field. Importers **RAN** via `grep -rn -E "^\s*(from|import)\s+<module>(\.|\s|,|$)" app/ db/ run.py`.

### 3.1 The table

| pyproject | Package | Version | Licence | Licence source | Used at (`file:line`) | Verdict |
|---|---|---|---|---|---|---|
| `:9` | fastapi | 0.121.3 | MIT | expr | `app/api/auth.py:5` (8 app-side import sites) | **keep** |
| `:10` | uvicorn[standard] | 0.38.0 | BSD-3-Clause | expr | `run.py:4`, `app/main.py:1197` | **keep** |
| `:11` | pydantic | 2.12.4 | MIT | expr | `app/agent/chain.py:24` | **keep** |
| `:12` | pydantic-settings | 2.12.0 | MIT | expr | `app/core/config.py:13` | **keep** |
| `:13` | python-dotenv | 1.2.1 | BSD-3-Clause | expr | `app/agent/chain.py:12` (4 sites) | **keep** |
| `:15` | sqlalchemy | 2.0.44 | MIT | License-field | `app/core/retriever.py:14` (25 sites) | **keep** |
| `:16` | sqlparse | 0.5.3 | BSD | classifier only | `app/sqlpolicy/legacy_heuristics.py:19` | **keep** — deliberate, see §6.1 |
| `:17` | pyyaml | 6.0.3 | MIT | classifier | `app/main.py:511` (7 sites) | **keep** |
| `:18` | numpy | 2.3.5 | BSD-3-Clause | classifier | **no importers** | **remove the direct pin** — §3.2 |
| `:19` | pandas | 2.3.3 | BSD-3-Clause | classifier | **no importers** | **remove** — §3.2 |
| `:20` | networkx | 3.5 | BSD | classifier | **no importers, ever** | **remove** — §3.2 |
| `:21` | tiktoken | 0.12.0 | MIT | License-field | `app/schema_pipeline/schema_documenting.py:188` | **keep** |
| `:23` | langchain | 1.0.8 | MIT | License-field | `app/agent/chain.py:13` | **watch** — §10.2 |
| `:24` | langchain-core | 1.0.7 | MIT | License-field | `app/agent/chain.py:17` (8 sites) | **watch** — §10.2 |
| `:25` | langgraph | 1.0.3 | MIT | expr | `app/agent/chain.py:29` | **watch** — §10.2 |
| `:26` | langchain-groq | 1.0.1 | MIT | License-field | `app/agent/chain.py:21` | **watch** — §10.2 |
| `:27` | langchain-google-genai | 3.1.0 | MIT | License-field | `app/agent/chain.py:20`, `app/embeddings/service.py:40`, `app/llm/providers/remote.py:76` | **keep** |
| `:28` | langchain-openai | 1.0.3 | MIT | License-field | `app/agent/chain.py:22` | **watch** — §10.2 |
| `:29` | langchain-deepseek | 1.0.1 | MIT | License-field | `app/agent/chain.py:19` | **watch** — §10.2 |
| `:30` | langchain-anthropic | 1.1.0 | MIT | License-field | `app/agent/chain.py:16` | **watch** — §10.2 |
| `:31` | langsmith | 0.4.45 | MIT | License-field | `app/agent/chain.py:23` | **watch** — §10.2 |
| `:33` | langchain-postgres | 0.0.16 | MIT | expr | `app/core/retriever.py:13` | **keep** |
| `:34` | langgraph-checkpoint[postgres] | 3.0.1 | MIT | expr | `app/agent/chain.py:29-30` (`langgraph.checkpoint.base`) | **keep, drop the `[postgres]` extra** — §6.4 |
| `:35` | langgraph-checkpoint-postgres | 3.0.1 | MIT | expr | `db/langchain_memory.py:9` | **keep** |
| `:36` | psycopg[binary,pool] | 3.2.12 | **LGPL-3.0-only** | expr | no `import`; resolved by SQLAlchemy from the URL scheme | **keep + declare** — §4.1 |
| `:38` | pyodbc | 5.3.0 | MIT | License-field | no `import`; URL scheme built at `app/execution/connections.py:88` | **keep** |
| `:39` | pymysql | 1.1.2 | MIT | expr | no `import`, **and no app-code URL builder** | **watch** — §3.3 |
| `:40` | sentry-sdk[fastapi] | 2.61.1 | MIT | expr | `app/core/observability.py:22` (lazy, guarded) | **keep** |
| `:41` | argon2-cffi | 25.1.0 | MIT | expr | `app/security/passwords.py:5` | **keep** |
| `:42` | sqlglot | 30.17.0 | MIT | expr | `app/sqlpolicy/allowlist.py:7` (13 sites) | **keep** |
| `:43` | alembic | 1.19.1 | MIT | expr | `db/migrate.py:13` (6 sites) | **keep** |
| `:44` | cryptography | 50.0.0 | Apache-2.0 OR BSD-3-Clause | expr | `app/platform/secrets.py:17` | **keep** |
| `:45` | httpx | 0.28.1 | BSD-3-Clause | classifier | `app/llm/providers/ollama.py:22` | **keep; delete the duplicate at `:75`** — §6.3 |
| `:46` | prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | expr | **no importers** | **watch** — declared ahead of use, §3.4 |
| `:47` | opentelemetry-api | 1.44.0 | Apache-2.0 | expr | **no importers** | **watch** — §3.4 |
| `:48` | opentelemetry-sdk | 1.44.0 | Apache-2.0 | expr | **no importers** | **watch** — §3.4 |
| `:49` | opentelemetry-exporter-otlp-proto-http | 1.44.0 | Apache-2.0 | expr | **no importers** | **watch** — §3.4 |
| `:50` | opentelemetry-instrumentation-fastapi | 0.65b0 | Apache-2.0 | expr | **no importers** | **watch** — §3.4 |
| `:51` | fastembed | 0.8.0 | **Apache-2.0** — LICENSE file read; classifier is wrong | LICENSE file | `app/embeddings/local.py:58` | **keep + allowlist** — §4.2 |
| `:52` | langchain-text-splitters | 1.0.0 | MIT | License-field | `app/schema_pipeline/embedding_pipeline.py:11` | **keep** |
| `:53` | langgraph-checkpoint-sqlite | 3.0.3 | MIT | expr | **no importers** — added mid-audit | **watch** — §3.5 |

### 3.2 Remove now — three dependencies with no importers and no plan

These fail question 4 of §1.2 outright: nothing breaks if they disappear.

**`networkx` (`pyproject.toml:20`) — never used, in any version of this codebase.** **RAN**: `git grep -l networkx HEAD` across the entire committed tree returns exactly two files, `pyproject.toml` and `uv.lock`. It has no importer now and never had one. **RAN** reverse-dependency scan: no installed package requires it outside an extra. Delete the line.

**`pandas` (`:19`) — orphaned by the v2 rewrite.** At `HEAD` it was imported by `app/core/query_executor.py` and `app/core/result_formatter.py`. **READ**, `app/core/result_formatter.py:1-7` documents the replacement in its own module docstring: the numbers now come from `app.execution.results` rather than from a pandas `describe()`. Nothing installed requires it. Delete the line.

*Two surviving mentions of pandas are prose, not code, and must not be counted as importers:* `app/agent/prompt.py:93` still describes result statistics as a "pandas describe", which is now **inaccurate prose in a shipped prompt** and should be corrected in the same change; and `eval/spider/**` is vendored Spider dataset content, gitignored at `.gitignore:45`.

**`numpy` (`:18`) — delete the direct pin, but it cannot leave the environment.** Also orphaned by the `result_formatter` rewrite. **RAN** reverse-dependency scan: numpy is a hard (non-extra) requirement of `langchain-postgres`, `pgvector`, `pandas`, `fastembed` and `onnxruntime`. So it stays installed regardless — but it should be *transitive*, not declared. Declaring it implies DBWhisper's own code uses it, which is no longer true, and pins a floor nobody is maintaining.

### 3.3 Watch — `pymysql`

**RAN**: `pymysql` is never imported, and — unlike `psycopg` and `pyodbc`, which are genuinely used through SQLAlchemy's URL-scheme resolution — **no code in `app/` or `db/` constructs a `mysql+pymysql://` URL.** The only two occurrences of the string anywhere are `app/sqlpolicy/types.py:35` (a dialect-name alias, so a *user-supplied* MySQL connection string is classified correctly) and `tests/test_network_policy.py:30` (a test fixture).

So MySQL support today is: the policy engine will correctly classify a MySQL dialect if a user enrols one, and the driver is installed so SQLAlchemy could open it. That is a defensible reason to keep it — but it is a thinner justification than the other two drivers have, and it should be either exercised by an enrolment path or dropped.

**Do not classify the three drivers as "unused".** `psycopg`, `pyodbc` and `pymysql` are used via **dialect resolution**: SQLAlchemy imports them by name from the connection URL at `app/execution/connections.py:122`, `app/schema_pipeline/introspector.py:58`, `db/database_manager.py:69` and `db/migrate.py:41`. A grep for `import psycopg` will always come back empty and that proves nothing.

### 3.4 Watch — five dependencies declared ahead of their wiring

`prometheus-client` and the four `opentelemetry-*` packages have no importers. **This is deliberate pre-provisioning, not an accident**, and the evidence is that the configuration surface already exists:

* **READ**, `app/core/config.py:52-55` — `otel_enabled`, `otel_exporter_otlp_endpoint`, `otel_service_name`, `metrics_enabled`. All four settings are defined and **none is read by any code**.
* **READ**, `.env.example:67-71` — the matching `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `METRICS_ENABLED` keys under an `OPENTELEMETRY / METRICS` heading.
* The wiring is scheduled: **READ**, `docs/v2/IMPLEMENTATION_ROADMAP.md:137` — "OTel spans per node/service, Prometheus `/metrics`, compose `observability` profile"; the module is designed at `docs/v2/TARGET_ARCHITECTURE.md:187` (`otel.py — tracer/meter setup; OTLP exporter optional; no-op default`) and the compose profile at `:421`.
* **READ**, `app/core/observability.py:22` — the only third-party import in the observability module today is `sentry_sdk`. Nothing is instrumented with OTel yet.

The correct status for all five is **"declared ahead of use, wiring scheduled"**. They are cheap: all five are pure-Python with small dependency tails, and all are Apache-2.0 or Apache/BSD dual. They should nonetheless carry a deadline — if the phase that `docs/v2/IMPLEMENTATION_ROADMAP.md:137` schedules slips past two review cycles (§11.2), remove them and re-add when the code lands.

**`fastembed` is no longer in this category.** It was unwired at the start of this audit and is wired now, at `app/embeddings/local.py:58`. Its transitive tail is still the heaviest in the tree and §9.1 quantifies the cost.

### 3.5 Watch — `langgraph-checkpoint-sqlite`, added during this audit

**READ**, `pyproject.toml:53`: `"langgraph-checkpoint-sqlite>=2.0"`. **RAN**: installed at 3.0.3, licence `MIT` from `License-Expression`. **RAN**: **no importers** — grep for `langgraph.checkpoint.sqlite`, `SqliteSaver` and `aiosqlite` across `app/`, `db/` and `tests/` returns nothing.

This is almost certainly the SQLite counterpart to `langgraph-checkpoint-postgres` (`:35`, imported at `db/langchain_memory.py:9`), added in support of the "SQLite for demos" path at `docs/v2/TARGET_ARCHITECTURE.md:26` and `:68`. It appeared minutes before this register was written, so it has been recorded rather than judged. **Confirm at the next review (§11.2) that it acquired an importer**; if it has not, it belongs in §3.2 rather than here.

---

## 4. Non-permissive licences, in full

### 4.1 The complete inventory and the obligation each one creates

This is every non-permissive licence in either dependency tree. Nothing else exists in the direct or transitive closure of either language.

| Package | Licence | Verified how | Where it reaches | Obligation it actually creates |
|---|---|---|---|---|
| **msodbcsql18** | **Proprietary Microsoft EULA** *(from knowledge — verify before release)* | `Dockerfile:38` **READ** (the install line, not the EULA) | Docker image only | **Redistribution of the image is governed by the EULA, not by MIT.** Read the EULA before publishing the image. §2.3. |
| **psycopg** 3.2.12 | LGPL-3.0-only | **RAN** (`License-Expression`); **READ** `.venv/Lib/site-packages/psycopg-3.2.12.dist-info/licenses/LICENSE.txt:1-2` — "GNU LESSER GENERAL PUBLIC LICENSE Version 3" | pip install + Docker image | Unmodified, separately-installed library, so nothing flows to DBWhisper's MIT code. **But the image redistributes the binary**, which engages LGPL §4/§6: the recipient must be able to replace the library and obtain its source. Satisfied in practice by pip-installability — **state it, do not leave it implicit.** |
| **psycopg-binary** 3.2.12 | LGPL-3.0-only | **RAN** | same | same |
| **psycopg-pool** 3.2.7 | LGPL-3.0-only | **RAN** | same | same |
| **unixODBC** | LGPL-2.1 (libodbc); some tools GPL-2.0 *(from knowledge — verify before release)* | `Dockerfile:32` **READ** | Docker image only | Weak copyleft on the driver manager. The GPL tools are not linked into anything shipped. Not verified which components msodbcsql18 links against — `docker build` was not run. |
| **@img/sharp-win32-x64** 0.34.5 | `Apache-2.0 AND LGPL-3.0-or-later` | **RAN**, full `web/node_modules` scan | transitive: `next` → `sharp` → prebuilt libvips | Weak copyleft on the libvips binary. Only reaches an end user if the Next.js image optimizer ships. Unmodified prebuilt binary. |
| **certifi** 2025.11.12 | MPL-2.0 | **RAN** (classifier) | pip install | File-level copyleft. Obligations attach only to *modified MPL files*. Unmodified → nothing. |
| **pathspec** 1.1.1 | MPL-2.0 | **RAN** | pip install | same |
| **tqdm** 4.67.1 | `MPL-2.0 AND MIT` | **RAN** | pip install (via fastembed) | same |
| **hypothesis** 6.165.10 | MPL-2.0 | **RAN** (`License-Expression`) | dev only | same, and dev-only — never distributed |
| **@vercel/og** 0.7.2 | MPL-2.0 | **RAN**, full-tree scan | reached via `next/og`, used at `web/app/apple-icon.tsx:1` | same |
| **axe-core** 4.12.0 | MPL-2.0 | **RAN** | dev, via `eslint-plugin-jsx-a11y` | same, dev-only |

**Zero GPL. Zero AGPL. Zero SSPL. Zero BUSL.** **RAN**: full scan of 172 Python distributions and 843 `package.json` files under `web/node_modules`.

**Blunt summary of the copyleft position: DBWhisper ships LGPL code and always has.** The `psycopg2-binary` → `psycopg` 3 migration did not change that (§7.2). This is fine and normal, but the project should not be described as having no copyleft exposure.

### 4.2 Metadata defects a licence scanner will trip on

**`fastembed` 0.8.0 — its own metadata says proprietary, and it is wrong.** **RAN**: the free-text `License` field reads "Apache License" while the trove classifier reads `License :: Other/Proprietary License`. **READ**, `.venv/Lib/site-packages/fastembed-0.8.0.dist-info/licenses/LICENSE:1-2` — "Apache License / Version 2.0, January 2004". **The package is Apache-2.0**; the classifier is an upstream packaging bug. Any FOSSA / pip-licenses / licensecheck gate will report a proprietary runtime dependency until this is explicitly allowlisted. Because `fastembed` is now the *default embedding provider* (`app/embeddings/local.py:58`), this will fire on the very first licence gate anyone adds. **Allowlist it with a comment pointing at this section.**

**`py_rust_stemmers` 0.1.8 — no licence metadata at all.** Transitive via `fastembed` → `py-rust-stemmers`. **RAN**: the only distribution of 172 with an empty licence field and no classifier. **READ**, `.venv/Lib/site-packages/py_rust_stemmers-0.1.8.dist-info/licenses/LICENSE:1-3` — "MIT License / Copyright (c) 2024 qdrant". It is MIT and fine, but invisible to any metadata-only tool, which will report it as unknown.

### 4.3 False positive to pre-empt — numpy is not GPL

A naive regex over installed metadata flags **numpy 2.3.5** for the strings "GPL", "GPLv3" and "lgpl". **RAN**, cause extracted from the metadata: numpy's `License` field embeds its entire `LICENSE.txt`, including the bundled-component notices, and one of those components is **libgfortran under `GPL-3.0-or-later WITH GCC-exception-3.1`** — the GCC Runtime Library Exception, which exists precisely to permit linking into non-GPL and proprietary programs. numpy itself is BSD-3-Clause (**RAN**, classifier). **There is no obligation here.** Documented so it is not re-raised at every review.

---

## 5. Python optional extra: `local-embeddings`

**READ**, `pyproject.toml:56-69`. **RAN**: none of the seven packages is installed — `langchain-huggingface`, `sentence-transformers`, `torch`, `transformers`, `einops`, `hf-xet` all raise `PackageNotFoundError`. Only `huggingface-hub` 0.36.0 is present, and only because `fastembed` requires it, which is why its licence is the single datum in this table that comes from real metadata.

The extra is documented as installable with `uv sync --extra local-embeddings` (`pyproject.toml:60`). **That command was not run**, so nothing here could be verified against installed metadata.

| pyproject | Package | Version | Licence | Licence source | Used at | Verdict |
|---|---|---|---|---|---|---|
| `:62` | langchain-huggingface | not installed | MIT | **from knowledge — verify before release** | **no importers** | **remove** — §5.1 |
| `:63` | sentence-transformers | not installed | Apache-2.0 | **from knowledge — verify before release** | none | **remove** |
| `:64` | torch | not installed | BSD-3-Clause + large bundled-component NOTICE | **from knowledge — verify before release** | none | **remove** |
| `:65` | transformers | not installed | Apache-2.0 | **from knowledge — verify before release** | none | **remove** |
| `:66` | einops | not installed | MIT | **from knowledge — verify before release** | none | **remove** |
| `:67` | huggingface-hub[hf-xet] | 0.36.0 (transitive) | Apache Software License | **classifier — the one verified row** | transitive via fastembed | keep as transitive |
| `:68` | hf-xet | not installed | Apache-2.0 | **from knowledge — verify before release** | none | **remove** |

**`torch` deserves a specific warning if this extra is ever revived.** PyTorch's own licence is BSD-3-Clause, but the wheels ship a large bundled-component NOTICE, and the **CUDA-enabled wheels additionally include proprietary NVIDIA runtime libraries under the NVIDIA Software Licence**. Which wheel variant `uv` would resolve to on a given platform was not determined. If a torch-bearing image is ever distributed, that resolution must be pinned and its licence set audited — a materially different question from the CPU wheel.

### 5.1 This extra is now dead and should be deleted

**RAN**, final scan: `langchain_huggingface`, `sentence_transformers`, `torch`, `transformers`, `einops`, `hf_xet` have **zero importers anywhere in `app/`, `db/`, `tests/` or `run.py`.**

This changed during the audit. At the start, `langchain-huggingface` was the *only* embedding backend actually wired, which produced an architectural inversion worth flagging: the wired provider sat in an optional extra while `fastembed`, a mandatory base dependency, was unwired. **The Phase 4 work resolved it in the correct direction.** **READ**, `app/embeddings/service.py:1-6`:

> `EMBEDDING_PROFILE` selects: `auto` (local, falling back to a configured hosted provider), `fake` (CI), `local-*` or `google`. The default is local — the point of the v2 program is that a working install needs no API key.

**READ**, `app/embeddings/service.py:66-70` — the builder dispatches to `FakeEmbeddingProvider`, `GoogleEmbeddingProvider` (which lazily imports `langchain_google_genai` at `:40`) or `LocalEmbeddingProvider` (fastembed). **HuggingFace is not one of the options.** `app/core/embeddings.py:69-76` retains `EMBEDDING_PROVIDER` as a deprecated alias mapped onto `EMBEDDING_PROFILE` with a warning, and `.env.example:34-36` documents the deprecation.

**Recommendation: delete `pyproject.toml:56-69` in full.** The extra now installs seven multi-gigabyte packages that no code path can reach. Its stated purpose — a local embedding path that needs no API key — is served by `fastembed`, which is lighter and already the default. Keeping it is a maintenance liability and a licence surface (torch's NVIDIA components) for zero function.

---

## 6. Python dev dependencies

**READ**, `pyproject.toml:71-86` — 13 packages in the `dev` dependency group. Versions and licences **RAN**.

| pyproject | Package | Version | Licence | Licence source | Used at (`file:line`) | Verdict |
|---|---|---|---|---|---|---|
| `:73` | pytest | 9.0.1 | MIT | expr | `.github/workflows/ci.yml:37` | **keep** |
| `:74` | pytest-asyncio | 1.4.0 | Apache-2.0 | expr | `pyproject.toml:127` (`asyncio_mode = "auto"`) | **keep** |
| `:75` | httpx | 0.28.1 | BSD-3-Clause | classifier | `tests/llm/test_providers.py:12`; required by Starlette's `TestClient` | **remove this row** — duplicate of `:45`, §6.3 |
| `:76` | ruff | 0.15.16 | MIT | expr | `.github/workflows/ci.yml:31,34`; `.pre-commit-config.yaml` | **keep** |
| `:77` | mypy | 2.1.0 | MIT | expr | `.github/workflows/ci.yml:44` — `uv run mypy app db \|\| true`, informational | **keep** |
| `:78` | vulture | 2.14 | MIT | classifier | **nowhere** — no config section, absent from CI and pre-commit | **wire or remove** — §6.5 |
| `:79` | pre-commit | 4.6.0 | MIT | License-field | `.pre-commit-config.yaml` | **keep** |
| `:80` | types-pyyaml | 6.0.12.20260518 | Apache-2.0 | expr | mypy stubs | **keep** |
| `:81` | pytest-cov | 7.1.0 | MIT | expr | `.github/workflows/ci.yml:37` — `--cov-fail-under=35` | **keep** |
| `:82` | pip-audit | 2.10.0 | Apache Software License | classifier | `.github/workflows/ci.yml:40-41`, informational | **keep** |
| `:83` | hypothesis | 6.165.10 | **MPL-2.0** | expr | `tests/sqlpolicy/test_properties.py:6-7` | **keep** — dev-only copyleft, §4.1 |
| `:84` | import-linter | 2.13 | BSD | classifier | **nowhere** — zero contracts | **wire or remove** — §6.5 |
| `:85` | pytest-xdist | 3.8.0 | MIT | expr | **nowhere** — no `-n` flag | **wire or remove** — §6.5 |

### 6.1 Deliberate overlap: two SQL parsers. Do not "clean this up."

`sqlparse` (`pyproject.toml:16`) and `sqlglot` (`:42`) coexist **by design**, and a future maintainer who deletes one will remove a security control. **READ**, `app/sqlpolicy/legacy_heuristics.py:1-11`:

> The v1 `sqlparse`/regex validator, retained as an *independent second opinion*. It runs after the AST engine; when it denies something the engine allowed, the stricter answer wins and the disagreement is recorded (that is how we notice gaps in either layer).

The same docstring (`:5-11`) enumerates the five false positives of the original heuristic layer that had to be fixed so it could not poison the new engine — substring matching inside identifiers (`grant_total`), `REPLACE(...)` the function versus MySQL `REPLACE INTO`, leading comments, `EXTRACT(YEAR FROM col)` read as a table reference, and quoted/bracketed identifiers.

This is defence-in-depth with a documented disagreement channel. It is the one overlap in this repository that is unambiguously correct.

### 6.2 Deliberate overlap: four observability stacks — assign ownership

Four separate stacks are declared. Two are wired, two are not:

| Stack | Status | Declared purpose |
|---|---|---|
| `sentry-sdk` | **wired** — `app/core/observability.py:22` | Error capture and Sentry performance tracing |
| `langsmith` | **wired** — `app/agent/chain.py:23` (`traceable`) | LLM call and agent-step tracing |
| `opentelemetry-*` ×4 | **not wired** | Vendor-neutral distributed traces → OTLP collector |
| `prometheus-client` | **not wired** | `/metrics` scrape endpoint |

This is not necessarily redundant — errors, LLM traces, distributed traces and metrics are four genuinely different concerns — but **nothing in the repository states which stack owns which concern**, so the overlap reads as accidental. Assign each a written owner in `app/core/observability.py` when the OTel wiring lands, or drop `prometheus-client` in favour of OTel metrics exported through the collector's Prometheus exporter (which would delete a dependency; the collector is already in the planned compose profile at `docs/v2/TARGET_ARCHITECTURE.md:421`).

**A related overlap that cannot be fixed by removing anything: two HTTP clients.** `httpx` (`pyproject.toml:45`, used at `app/llm/providers/ollama.py:22`) and `requests` 2.32.5 (transitive) are both in the image. **RAN** reverse-dependency scan: `requests` is required by `langsmith`, `tiktoken`, `google-api-core`, `huggingface-hub`, `fastembed`, `CacheControl`, `requests-toolbelt` and `pip_audit`. It is structural. Do not plan around removing it.

### 6.3 Accidental overlap: `httpx` is declared twice

**READ**, `pyproject.toml:45` (base) and `pyproject.toml:75` (dev). Harmless to resolution — uv resolves one version, 0.28.1 — but it is now actively misleading: the dev-group entry implies `httpx` is test-only, when as of `app/llm/providers/ollama.py:22` it is **production transport for the local Ollama provider**. **READ**, `app/llm/providers/ollama.py:4`, which states the design intent: the provider depends on nothing beyond `httpx` — "no vendor SDK, no LangChain, nothing that could pull a cloud client into the" local path.

**Delete `pyproject.toml:75`.** The base declaration covers both uses, and Starlette's `TestClient` requirement is satisfied either way.

### 6.4 Accidental: `langgraph-checkpoint[postgres]` requests an extra that does not exist

**READ**, `pyproject.toml:34`: `"langgraph-checkpoint[postgres]>=3.0.1"`.

**RAN**: `langgraph-checkpoint` 3.0.1's `METADATA` contains **no `Provides-Extra` lines at all** — its only requirements are `langchain-core>=0.2.38` and `ormsgpack>=1.12.0`. The `[postgres]` extra does not exist and resolves to nothing. `uv.lock` faithfully records `extras = ["postgres"]` and it has no effect.

It is harmless **only because** `pyproject.toml:35` separately declares `langgraph-checkpoint-postgres`, which is what actually provides the `PostgresSaver` imported at `db/langchain_memory.py:9`. Drop the `[postgres]` marker — it currently implies a dependency relationship that does not exist.

### 6.5 Three dev tools installed but buying nothing

* **`import-linter` (`:84`)** — **RAN**: there is no `[tool.importlinter]` section in `pyproject.toml`, no `.importlinter` file, no `setup.cfg`, no `tox.ini`. **Zero contracts are defined.** The only occurrence of the string "import-linter" in the entire repository configuration is `pyproject.toml:84` itself. This is the most consequential of the three, because two design documents promise it as an *enforcement mechanism*: `docs/v2/TARGET_ARCHITECTURE.md:98` — "Arrows show the only allowed import direction (enforced by an import-linter contract in CI)" — and `docs/v2/IMPLEMENTATION_ROADMAP.md:69` — "no code path executes SQL outside the service (import-linter rule)". **Both statements are currently false.** The layering is enforced by review, not by CI. Either write the contracts or amend the two documents.
* **`pytest-xdist` (`:85`)** — **READ**, `pyproject.toml:126`: `addopts = "-q"`, no `-n`. **READ**, `.github/workflows/ci.yml:37`: no `-n` either. Parallelism is never engaged. With the suite at 955 tests (§10.5) this is now leaving real wall-clock time on the table.
* **`vulture` (`:78`)** — **RAN**: no config section, absent from `.github/workflows/ci.yml`, absent from `.pre-commit-config.yaml`. Never invoked.

---

## 7. Node dependencies

**READ**, `web/package.json:13-29` — 4 dependencies, 9 devDependencies, 13 total.

**Versions below are from `web/package-lock.json`, which is authoritative.** **RAN**: the on-disk `web/node_modules` is **stale** for three of the thirteen — `@sentry/nextjs` 10.56.0 on disk vs 10.57.0 in the lock, `autoprefixer` 10.4.20 vs 10.5.0, `postcss` 8.5.10 vs 8.5.15. `package.json` and `package-lock.json` agree with each other; the tree simply predates the last install. All three are MIT at both versions, so the licence table is unaffected — but §10.4's `npm audit` result was produced against the stale tree and may shift after a fresh `npm ci`.

| package.json | Package | Version (lock) | Licence | Licence source | Used at (`file:line`) | Verdict |
|---|---|---|---|---|---|---|
| `:14` | @sentry/nextjs | 10.57.0 | MIT | lock `license` field | `web/app/error.tsx:3`, `web/app/global-error.tsx:3`, `web/instrumentation.ts:1`, `web/instrumentation-client.ts:1` | **keep** |
| `:15` | next | 15.5.19 | MIT | lock | 72 references, e.g. `web/app/(marketing)/page.tsx:1-2`, `web/app/apple-icon.tsx:1` (`next/og`) | **keep** |
| `:16` | react | 18.3.1 | MIT | lock | 48 references, e.g. `web/app/(app)/app/page.tsx:3` | **keep** |
| `:17` | react-dom | 18.3.1 | MIT | lock | `web/app/components/MobileNav.tsx:4` (`createPortal`) | **keep** |
| `:20` | @types/node | 20.17.16 | MIT | lock | compile-time only, erased from output | **keep** |
| `:21` | @types/react | 18.3.18 | MIT | lock | compile-time only | **keep** |
| `:22` | @types/react-dom | 18.3.5 | MIT | lock | compile-time only | **keep** |
| `:23` | autoprefixer | 10.5.0 | MIT | lock | `web/postcss.config.mjs:5` | **keep** |
| `:24` | eslint | 8.57.1 | MIT | lock | `web/.eslintrc.json`; `.github/workflows/ci.yml:60` | **keep** |
| `:25` | eslint-config-next | 15.5.19 | MIT | lock | `web/.eslintrc.json` (`next/core-web-vitals`) | **keep** |
| `:26` | postcss | 8.5.15 | MIT | lock | `web/postcss.config.mjs:1` | **keep** — but see §10.4 |
| `:27` | tailwindcss | 3.4.17 | MIT | lock | `web/tailwind.config.ts:1`, `web/postcss.config.mjs:4` | **keep** |
| `:28` | typescript | 5.7.3 | **Apache-2.0** | lock | `web/tsconfig.json`; `web/package.json:11` (`npm run typecheck`) | **keep** |

**All 13 direct Node dependencies are used. None is unused.** TypeScript is the only non-MIT direct Node dependency, and Apache-2.0 is permissive.

**Tree size** — **RAN**: `package-lock.json` is lockfileVersion 3 with **589** `node_modules/` entries; `npm audit` metadata independently reports prod 128 / dev 339 / optional 96 / peer 53 = 589. The on-disk walk found 843 `package.json` files across 502 unique `name@version` pairs (stale, and the optional platform packages are not all installed on Windows).

**Transitive copyleft** — **RAN**, full-tree scan of every `license` field in all 843 `package.json` files: exactly three hits, all listed in §4.1 (`@img/sharp-win32-x64`, `@vercel/og`, `axe-core`). Zero packages with a missing `license` field.

**Limitation of that scan, stated so it is not over-trusted:** it read `package.json` `license` fields only. A package that declares its licence solely in a `LICENSE` file, or that bundles differently-licensed code without declaring it — **exactly what numpy does on the Python side (§4.3)** — would not be caught. A real scanner should confirm before anyone asserts the Node tree is clean of GPL.

---

## 8. Removed on 2026-08-21

**RAN**, verified two independent ways. `git diff pyproject.toml | grep "^-"` shows exactly five removed dependency lines and no others:

```
-    "matplotlib>=3.10.7",
-    "langchain-community>=0.4.1",
-    "groq>=0.33.0",
-    "psycopg2-binary>=2.9.11",
-    "mysql-connector-python>=9.5.0",
```

And **RAN**, `importlib.metadata.version`: `matplotlib`, `psycopg2-binary`, `mysql-connector-python` and `langchain-community` all raise `PackageNotFoundError`. `groq` does **not** — see the table.

| Package | Old licence | Reason for removal | Residue in the tree | Was it a licence win? |
|---|---|---|---|---|
| **mysql-connector-python** | **GPL-2.0 with the Universal FOSS Exception** *(from knowledge — verify before release)* | Replaced by **pymysql** (MIT, `pyproject.toml:39`), a drop-in alternative for the same SQLAlchemy dialect | **RAN**: none. No importers, no residual references anywhere | **Yes — the only one.** §8.1 |
| **psycopg2-binary** | LGPL-3.0 | Redundant second Postgres driver; `psycopg` 3 (`pyproject.toml:36`) covers the same ground with a pool extra | **RAN**: no importers. Only hits are *dialect-name strings* kept for back-compat with user-supplied connection strings: `app/sqlpolicy/types.py:33` accepts `"psycopg2"` as a Postgres alias; `app/agent/chain.py:255` and `app/security/db_readonly_checker.py:71` match `"psycopg"` in a dialect string | **No.** §8.2 |
| **matplotlib** | PSF-based, permissive | Charting moved out of the backend; nothing imported it. Removed for image size and attack surface | **RAN**: one *string* at `app/utils/logger.py:141` — a logger name in a `noisy_libs` list, not an import, harmless. **And one live defect: `Dockerfile:28` still sets `MPLCONFIGDIR=/tmp/mpl`**, a matplotlib-only cache-directory variable that is now dead | No — it was already permissive |
| **langchain-community** | MIT | Large grab-bag package; nothing imported it. Removed for dependency-surface reduction | **RAN**: none at all | No |
| **groq** (raw SDK) | Apache-2.0 | De-duplication: the SDK was declared directly *and* pulled in by `langchain-groq` | **RAN**: **`groq` 0.36.0 is still installed**, transitively, because `langchain-groq>=1.0.0` remains at `pyproject.toml:26` and is imported at `app/agent/chain.py:21` | No — it was Apache-2.0 |

### 8.1 mysql-connector-python was the only real licence win — and the framing matters

Oracle ships `mysql-connector-python` under **GPL-2.0 with the Universal FOSS Exception**. That exception does list MIT among the permitted licences, so combining it with an MIT application was *arguably* lawful. But "arguably lawful, subject to a legal argument about the scope of a vendor-specific exception" is a materially worse position than "MIT, no argument required."

**pymysql is MIT and a drop-in replacement for the same SQLAlchemy dialect.** Removing the need for the legal analysis — rather than winning it — is the correct engineering call, and that is how this removal should be described.

### 8.2 psycopg2-binary was not a licence win — do not let a reader infer otherwise

`psycopg2-binary` is LGPL-3.0. Its replacement, **`psycopg` 3, is also LGPL-3.0-only** (**RAN**, `License-Expression`; **READ**, its shipped `LICENSE.txt:1-2`, "GNU LESSER GENERAL PUBLIC LICENSE Version 3"). That change eliminated a *redundant second Postgres driver*. It did not eliminate a copyleft obligation.

**DBWhisper still ships LGPL code and still redistributes it in the Docker image.** §4.1 records the obligation that creates. Anyone reading the 2026-08-21 removal list without this paragraph would reasonably conclude the project's copyleft exposure improved. It did not.

### 8.3 Do not claim groq is gone

**RAN**: `importlib.metadata.version('groq')` returns **0.36.0**. The raw SDK was removed as a *direct* dependency; it remains in the environment transitively via `langchain-groq` (`pyproject.toml:26`), which is imported at `app/agent/chain.py:21`. groq is Apache-2.0, so there was never a licence motive here — this was de-duplication. The accurate statement is **"removed as a direct dependency; still present transitively."**

### 8.4 Related deletions of dead code, same date

Not dependency changes, but the same cleanup pass, and they explain some of the "no importers" findings above:

* `app/schema_pipeline/user_database_manager.py` — deleted; it imported a non-existent module `db.connection`.
* `app/utils/token_tracker.py` and `tests/test_token_tracker.py` — deleted; no importers.
* 335 lines of commented-out legacy MSSQL extractor removed from the top of `app/schema_pipeline/introspector.py`.

---

## 9. Added for v2

Every dependency added on this branch, with what it enables and what was considered instead. **All licences in this table were RAN from installed metadata** except where a row says otherwise.

**Licence verdict for the whole set: every addition is permissive or file-level copyleft. None creates an obligation on DBWhisper's MIT code.** The single copyleft addition, `hypothesis` (MPL-2.0), is dev-only and unmodified.

| pyproject | Package | Licence | What it enables | Alternative considered, and why it lost |
|---|---|---|---|---|
| `:42` | **sqlglot** 30.17.0 | MIT | A real multi-dialect SQL AST, so the read-only guarantee becomes a property of the parse tree rather than of a regex. `docs/v2/TARGET_ARCHITECTURE.md:50`; live at `app/sqlpolicy/allowlist.py:7` and 12 other sites | Staying on **sqlparse** (BSD): it tokenizes but builds no semantic AST, so it cannot distinguish `REPLACE(...)` the function from `REPLACE INTO` — one of five false positives enumerated at `app/sqlpolicy/legacy_heuristics.py:5-11`. Kept alongside as a second opinion rather than deleted (§6.1) |
| `:43` | **alembic** 1.19.1 | MIT | Versioned schema migration, so v1 data is migrated rather than dropped (`docs/v2/IMPLEMENTATION_ROADMAP.md:9,35`). Baseline at `db/migrations/versions/0001_baseline.py`; config at `alembic.ini`; driver at `db/migrate.py:13` | The incumbent SQLAlchemy `create_all()`, which cannot alter or downgrade. Alembic shares SQLAlchemy's author and was already an indirect dependency |
| `:44` | **cryptography** 50.0.0 | Apache-2.0 OR BSD-3-Clause | Fernet envelope encryption for stored target-database credentials. `app/platform/secrets.py:17` imports `Fernet, InvalidToken, MultiFernet` — **`MultiFernet` is the operative one**, because it provides key rotation | Hand-rolled AES via `hashlib`/`hmac`. Not acceptable for credential storage, and no rotation primitive |
| `:45` | **httpx** 0.28.1 | BSD-3-Clause | Async-capable HTTP for provider clients; now the Ollama transport (`app/llm/providers/ollama.py:22,49,53,58`), deliberately chosen so the local path pulls in no vendor SDK (`app/llm/providers/ollama.py:4`) | **requests** (Apache-2.0), which has no async support. httpx would also have arrived transitively regardless — **RAN**: `langgraph-sdk` requires `httpx>=0.25.2` — and Starlette's `TestClient` needs it |
| `:46` | **prometheus-client** 0.26.0 | Apache-2.0 AND BSD-2-Clause | The reference client for a `/metrics` scrape endpoint (`docs/v2/IMPLEMENTATION_ROADMAP.md:137`). **Not yet wired** (§3.4) | Emitting OTel metrics only and relying on the collector's Prometheus exporter — viable, and would delete a dependency. Both stacks are currently declared; §6.2 asks for a decision |
| `:47-50` | **opentelemetry-api / -sdk / -exporter-otlp-proto-http / -instrumentation-fastapi** 1.44.0 / 0.65b0 | Apache-2.0 (all four) | Vendor-neutral distributed tracing into the planned compose `observability` profile — otel-collector, jaeger, prometheus, grafana (`docs/v2/TARGET_ARCHITECTURE.md:76,187,421`). **Not yet wired** (§3.4) | **Sentry performance tracing alone**, already present (`app/core/observability.py`) but vendor-locked and hosted-only. OTel preserves the self-host path, which is the project's stated "No required purchase" principle (`docs/v2/TARGET_ARCHITECTURE.md:26`). Four packages because the API/SDK split lets libraries depend on the API alone |
| `:51` | **fastembed** 0.8.0 | Apache-2.0 (**LICENSE file read**; classifier wrongly says proprietary — §4.2) | CPU ONNX embeddings with no torch, so the no-API-key local path works offline. Now the default provider: `app/embeddings/local.py:58`, selected by `app/embeddings/service.py:70`. `docs/v2/TARGET_ARCHITECTURE.md:26,72,123` | **sentence-transformers + torch** — exactly what the `local-embeddings` extra holds and what `pyproject.toml:57-59` says is too heavy for the image. `app/embeddings/local.py:1-8` records the reasoning: ONNX Runtime instead of PyTorch, "tens of megabytes rather than a multi-gigabyte CUDA-capable stack". **Honest trade-off in §9.1** |
| `:52` | **langchain-text-splitters** 1.0.0 | MIT | `RecursiveCharacterTextSplitter` for chunking schema documentation before embedding — `app/schema_pipeline/embedding_pipeline.py:11` | Hand-rolled chunking, or pulling back the whole of `langchain-community`. Small, single-purpose package extracted from langchain |
| `:53` | **langgraph-checkpoint-sqlite** 3.0.3 | MIT | Presumed SQLite counterpart to the Postgres checkpointer, supporting the "SQLite for demos" path (`docs/v2/TARGET_ARCHITECTURE.md:26,68`). **Added mid-audit; no importers yet** (§3.5) | The Postgres checkpointer alone (`:35`), which requires a Postgres server for local development |
| `:83` | **hypothesis** 6.165.10 | **MPL-2.0** | Property-based SQL mutation testing, so the policy engine is tested against generated adversarial input rather than a fixed list. `docs/v2/IMPLEMENTATION_ROADMAP.md:54`; live at `tests/sqlpolicy/test_properties.py:6-7` | More hand-written cases — which cannot find what the author did not think of. **The only copyleft addition**; dev-only and unmodified, so MPL imposes nothing (§4.1) |
| `:84` | **import-linter** 2.13 | BSD-2-Clause | Machine-enforced layering, so the dependency arrows in `docs/v2/TARGET_ARCHITECTURE.md:98` cannot silently rot and nothing executes SQL outside the execution service (`docs/v2/IMPLEMENTATION_ROADMAP.md:69`) | A custom AST script, or trusting review. **Currently unconfigured — zero contracts** (§6.5) |
| `:85` | **pytest-xdist** 3.8.0 | MIT | Parallel test execution as the suite grows — now 955 tests (§10.5) | Accepting the wall-clock cost. **Currently not wired** (§6.5) |

### 9.1 The honest trade-off on fastembed

fastembed successfully avoids torch, and that was the point. But it is not free, and the cost should be recorded rather than discovered later. **RAN**, its declared requirements: `huggingface-hub`, `loguru`, `mmh3`, `numpy`, `onnxruntime`, `pillow`, `py-rust-stemmers`, `requests`, `tokenizers`, `tqdm` — all pulled into the **base** install.

Two consequences:

* **`pillow` 12.0.0 is in the image solely because of fastembed.** **RAN** reverse-dependency scan: fastembed is the only non-extra requirer. Pillow accounts for **27 of the 86 pip-audit rows** (§10.2) — by a wide margin the single largest contributor.
* **`onnxruntime` 1.29.0** (MIT) is also fastembed-only, and it is not a small package. The justification at `pyproject.toml:57-59` for excluding torch was image size; onnxruntime plus pillow plus tokenizers recovers some of that ground.

This is now a *justified* cost — fastembed is wired and is the default. It was not justified at the start of this audit. The number to watch is pillow's advisory count; if fastembed's pillow floor keeps the image on a vulnerable version, that becomes a reason to reconsider.

**Correction to a claim that circulated earlier in this audit:** dropping fastembed would **not** remove `requests` from the tree. See §6.2 — `requests` has eight other requirers and is structural.

---

## 10. Vulnerability posture

### 10.1 pip-audit — RAN 2026-08-24, exit code 1

Command: `uv run pip-audit`, run from the repository root.

Verbatim — the warning and the summary line, in the order the tool printed them, followed by the first data row so the format is unambiguous:

```
WARNING:pip_audit._dependency_source.pip:pip-audit will run pip against D:\Software\__stashed\AI_PROJECTS\dbwhisper\.venv\Scripts\python.exe, but you have a virtual environment loaded at d:\Software\__stashed\AI_PROJECTS\dbwhisper\.venv. This may result in unintuitive audits, since your local environment will not be audited. You can forcefully override this behavior by setting PIPAPI_PYTHON_LOCATION to the location of your virtual environment's Python interpreter.
Found 86 known vulnerabilities in 26 packages
Name                          Version ID                  Fix Versions
----------------------------- ------- ------------------- -------------
click                         8.3.1   PYSEC-2026-2132     8.3.3
```

**The warning qualifies the result and is reproduced deliberately.** pip-audit ran pip against `.venv\Scripts\python.exe` while a virtual environment was already loaded. The run was **not** repeated with `PIPAPI_PYTHON_LOCATION` set, so it was not confirmed that the two paths resolve to the same environment. `86 / 26` is what the tool printed; treat it as indicative, and set `PIPAPI_PYTHON_LOCATION` before quoting the figure anywhere consequential.

### 10.2 Where the 86 findings sit

**Derived, not verbatim** — produced by counting rows in the pip-audit output (`awk 'NF>=3 && $1!~/^-/ {print $1}' | sort | uniq -c | sort -rn`). Sums to exactly 86 rows across 26 packages, matching the tool's own summary.

| Advisories | Package | Note |
|---|---|---|
| 27 | pillow | **Pulled in solely by fastembed** (§9.1). Largest single contributor by far |
| 8 | pyasn1 | transitive |
| 7 | starlette | transitive via fastapi |
| 5 | sqlparse | **direct** — `pyproject.toml:16` |
| 5 | langchain-core | **direct** — `pyproject.toml:24` |
| 4 | urllib3 | transitive |
| 4 | langsmith | **direct** — `pyproject.toml:31` |
| 2 each | orjson, langgraph-sdk, langgraph-checkpoint, langgraph, langchain-text-splitters, langchain-openai, idna | |
| 1 each | requests, python-dotenv, pytest, pygments, pydantic-settings, protobuf, pip, msgpack, langgraph-checkpoint-postgres, langchain-anthropic, langchain, click | |

Two structural observations:

1. **22 of the 86 rows are LangChain-ecosystem packages** (**RAN**: rows whose package name starts with `lang`), spread across 8 distinct distributions. This is the largest concentration after pillow, and it is the strongest evidence-based argument for the v2 programme's direction of reducing LangChain surface area — already visible at `app/llm/providers/ollama.py:4`, where the local provider deliberately takes a dependency on nothing but `httpx`.
2. **pip-audit prints duplicate rows.** **RAN**: `PYSEC-2026-165` appears twice for pillow, because pip-audit reports the same advisory from more than one source. So "86" counts advisory-source pairs, not 86 distinct vulnerabilities. **Do not quote it as a count of distinct CVEs.**

### 10.3 The full affected-package list

The 26 packages pip-audit flagged, **RAN**: `click`, `idna`, `langchain`, `langchain-anthropic`, `langchain-core`, `langchain-openai`, `langchain-text-splitters`, `langgraph`, `langgraph-checkpoint`, `langgraph-checkpoint-postgres`, `langgraph-sdk`, `langsmith`, `msgpack`, `orjson`, `pillow`, `pip`, `protobuf`, `pyasn1`, `pydantic-settings`, `pygments`, `pytest`, `python-dotenv`, `requests`, `sqlparse`, `starlette`, `urllib3`.

Six are **direct** base dependencies: `langchain` (`:23`), `langchain-core` (`:24`), `langsmith` (`:31`), `sqlparse` (`:16`), `python-dotenv` (`:13`), `pydantic-settings` (`:12`). One is a direct dev dependency: `pytest` (`:73`). The remaining nineteen are transitive.

### 10.4 npm audit — RAN 2026-08-24

Command: `npm audit`, run from `web/`. Network was available; the run succeeded.

```
10 vulnerabilities (3 moderate, 7 high)
```

Metadata: `{info: 0, low: 0, moderate: 3, high: 7, critical: 0, total: 10}`.

Direct dependencies affected (**RAN**, `isDirect: true`): **next** and **postcss**. Highlights:

* **postcss** `<=8.5.22` — 4 high-severity advisories, including GHSA-qx2v-qp2m-jg93 (XSS via unescaped `</style>`) and GHSA-6g55-p6wh-862q (arbitrary file read via `sourceMappingURL`).
* **sharp** `<0.35.0` — high, GHSA-f88m-g3jw-g9cj, inherited libvips CVE-2026-33327 / 33328 / 35590 / 35591.
* **@opentelemetry/core** `<2.8.0` — moderate, GHSA-8988-4f7v-96qf.

npm reports the fix requires `next@15.5.23`, which npm itself notes is "outside the stated dependency range" (`web/package.json:15` pins `next` at exactly `15.5.19`).

**Caveat:** this ran against the on-disk `node_modules`, which is stale for 3 of 13 direct dependencies (§7). A fresh `npm ci` could change the count. Licence conclusions are unaffected — all three are MIT at both versions.

### 10.5 What CI does about it

| Check | Where | Blocking? | Exact behaviour |
|---|---|---|---|
| pip-audit | `.github/workflows/ci.yml:39-41` | **No** | `uv run pip-audit` with `continue-on-error: true`, labelled "Dependency vulnerability audit (informational; Dependabot drives the fixes)" |
| npm audit | `.github/workflows/ci.yml:65-67` | **No** | `npm audit --audit-level=high` with `continue-on-error: true`, labelled "(informational)" |
| mypy | `.github/workflows/ci.yml:43-44` | **No** | `uv run mypy app db \|\| true`, labelled "informational — type-debt burn-down in progress" |
| Ruff lint | `.github/workflows/ci.yml:30-31` | **Yes** | `uv run ruff check app db tests run.py` |
| Ruff format | `.github/workflows/ci.yml:33-34` | **Yes** | `uv run ruff format --check app db tests run.py` |
| Tests + coverage | `.github/workflows/ci.yml:36-37` | **Yes** | `uv run pytest --cov=app --cov=db --cov-report=term-missing --cov-fail-under=35` |
| Secret scan | `.github/workflows/ci.yml:69-81` | **Yes** | gitleaks over full history |
| **Licence scan** | — | — | **Does not exist** (§1.3) |

Both vulnerability audits are therefore **advisory only**; Dependabot is the stated remediation mechanism. That is a defensible posture for a project at this stage — a blocking gate on 86 mostly-transitive advisories would block every pull request — but it should be a *recorded decision*, which is what this section makes it, rather than a default. §11.3 recommends the narrower gate that would be worth blocking on.

### 10.6 Two baseline figures that did not reproduce

The 2026-08-21 baseline was spot-checked. Two numbers have moved, both because work landed after that date — including work that landed *during* this audit. Neither indicates a defect.

* **Test count is 955, not 734.** **RAN**: `uv run pytest --collect-only` → `955 tests collected in 39.45s`. `docs/v2/IMPLEMENTATION_ROADMAP.md:173` records `tests/llm/` alone contributing 102 tests. **Use 955, dated 2026-08-24.**
* **`ruff check .` is not clean — 15 errors, 4 fixable.** **RAN**, by file: 13 in `eval/rescore.py`, 1 in `eval/harness.py`, 1 in `app/retrieval/memory_index.py:36` (SIM905). **CI is unaffected by the first fourteen**: `.github/workflows/ci.yml:31` runs the *scoped* `ruff check app db tests run.py`, which excludes `eval/`. **RAN**, the CI-scoped command: **1 error**, the `app/retrieval/memory_index.py:36` SIM905 — in a module that appeared during this session and is presumably still being written. Minutes earlier the same scoped command returned `All checks passed!`. **RAN**, `ruff format --check app db tests run.py`: `120 files already formatted`, clean.
  **Do not repeat "ruff clean" unqualified.** The accurate statement is: clean under the CI-scoped invocation, modulo in-flight edits; `eval/` is unlinted and outside CI's scope.

Not re-verified, and no reason to doubt: the 82 mypy errors across 20 files (`.github/workflows/ci.yml:44` runs it with `|| true`), the 41% coverage figure (gated at 35%, `.github/workflows/ci.yml:37`), and the frontend lint / typecheck / build. **`docker build` was not attempted** — the Docker daemon is not running on this machine — so nothing about the built image was verified beyond reading the `Dockerfile`.

---

## 11. Review procedure and cadence

### 11.1 On every pull request that touches `pyproject.toml`, `uv.lock`, `web/package.json` or `web/package-lock.json`

The author must, in that same pull request:

1. Answer the **four questions** (§1.2) in the description.
2. Read the licence from **installed metadata**, not from memory:
   ```bash
   uv run python -c "
   from importlib.metadata import metadata, version
   name = 'NEW_PACKAGE'
   m = metadata(name)
   print(version(name), '|', m.get('License-Expression'), '|',
         [c for c in (m.get_all('Classifier') or []) if c.startswith('License')], '|',
         (m.get('License') or '')[:80])
   "
   ```
   If `License-Expression` is absent, open the shipped `LICENSE` file under `.venv/Lib/site-packages/<dist>.dist-info/` and cite it. §4.2 shows two packages where the metadata is simply wrong.
3. **Add or update the row in this document**, with the `file:line` of the first importer. A dependency with no importer must be justified in writing as pre-provisioning, with the roadmap line that schedules its wiring (the model is §3.4).
4. If the licence is not on the §1.1 permissive list, it needs an explicit exception in §4.1 stating **the obligation it creates** — not just the licence name.
5. If any `pyproject.toml` line numbers shift, **re-run the line-number audit** — this document cites 60+ `pyproject.toml` line references and one insertion invalidated all of them past line 52 during this very audit.

### 11.2 Cadence

| Cadence | Action | Command |
|---|---|---|
| **Every PR touching a manifest** | §11.1 in full | — |
| **Weekly** | Review Dependabot pull requests. Both audits are non-blocking (§10.5), so Dependabot is the only thing actually moving versions | — |
| **Monthly** | Re-run both audits; update §10 | `uv run pip-audit` · `cd web && npm ci && npm audit` |
| **Monthly** | Re-run the importer scan; move anything newly orphaned into §3.2 and anything newly wired out of §3.4/§3.5 | see §11.4 |
| **Each phase boundary** (`docs/v2/IMPLEMENTATION_ROADMAP.md`) | Re-check every **watch** verdict. A package that has been "declared ahead of use" across two consecutive phase boundaries gets removed and re-added when the code lands | — |
| **Before any release or image publication** | Resolve **every** "from knowledge — verify before release" tag in this document. §11.3 item 2 lists them | — |
| **Before any release or image publication** | Read the **msodbcsql18 EULA** and record whether the image may be redistributed (§2.3) | — |

### 11.3 Open items, in priority order

**Must be resolved before a release or public image:**

1. **Read the msodbcsql18 EULA** (§2.3). Until then the Docker image is internal-use-only. This is the single highest-consequence unknown in this register.
2. **Verify every "from knowledge" licence.** They are: msodbcsql18 and unixODBC (§2.3, §4.1); the six uninstalled `local-embeddings` packages (§5); `mysql-connector-python`'s historical GPL-2.0-with-FOSS-Exception (§8); and every row in Appendix A. None has been confirmed against installed metadata.
3. **Write the LGPL notice** for `psycopg` / `psycopg-binary` / `psycopg-pool` and `unixODBC` into whatever accompanies a distributed image (§4.1). The obligation is easy to satisfy and easy to forget.

**Should be resolved in the normal course of work:**

4. **Add a licence gate to CI.** None exists (§1.3). `pip-licenses` for Python and `license-checker` for Node, run with `continue-on-error: false` on a **deny-list of GPL/AGPL/SSPL/BUSL only** — narrow enough never to block spuriously, strict enough to catch the one thing policy actually prohibits. Allowlist `fastembed` and `py_rust_stemmers` with a comment pointing at §4.2, and `numpy` with one pointing at §4.3.
5. **Delete `networkx`, `pandas`, and `numpy`'s direct pin** (`pyproject.toml:18-20`), and correct the now-inaccurate "pandas describe" prose at `app/agent/prompt.py:93` in the same change (§3.2).
6. **Delete the `local-embeddings` extra** (`pyproject.toml:56-69`) — zero importers since the Phase 4 embeddings work landed (§5.1).
7. **Delete the duplicate `httpx`** at `pyproject.toml:75` (§6.3) and the non-existent `[postgres]` extra at `pyproject.toml:34` (§6.4).
8. **Remove `MPLCONFIGDIR=/tmp/mpl` from `Dockerfile:28`** — matplotlib has been gone since 2026-08-21 (§8).
9. **Wire or remove `import-linter`, `pytest-xdist` and `vulture`** (§6.5). import-linter is the urgent one: `docs/v2/TARGET_ARCHITECTURE.md:98` and `docs/v2/IMPLEMENTATION_ROADMAP.md:69` both describe enforcement that does not exist.
10. **Confirm `langgraph-checkpoint-sqlite` acquired an importer** (§3.5); if not, delete `pyproject.toml:53`.
11. **Add `license = "MIT"` to `pyproject.toml`** and drop "All rights reserved." from `LICENSE:3` (§2.1).
12. **Decide the observability ownership question** (§6.2) before the OTel wiring lands, or drop `prometheus-client`.
13. **Refresh `web/node_modules`** with `npm ci` so it matches the lock, then re-run §10.4.

### 11.4 Reproducing this document's scans

```bash
# 0. Check the manifest has not moved under you
md5sum pyproject.toml     # expected at time of writing: 6260fd4620a1420fdb90f02195bc4fa5

# Installed distribution count (expect 172 as of this snapshot)
uv pip list | tail -n +3 | wc -l

# Licence for a direct dependency, preferring License-Expression
uv run python -c "
from importlib.metadata import metadata, version
for p in ['fastapi','sqlglot','psycopg','fastembed']:   # extend as needed
    m = metadata(p)
    print(p, version(p), m.get('License-Expression'),
          [c for c in (m.get_all('Classifier') or []) if c.startswith('License')])
"

# Importer scan for a single package (the test behind every 'no importers' verdict)
grep -rn -E "^\s*(from|import)\s+networkx(\.|\s|,|$)" app/ db/ tests/ run.py

# Reverse dependencies: who actually requires a transitive package
uv run python -c "
from importlib.metadata import distributions
import re
target = 'pillow'
for d in distributions():
    for r in (d.requires or []):
        if re.split(r'[\[\s<>=!;()]', r)[0].strip().lower().replace('_','-') == target:
            print(d.metadata['Name'], '->', r)
"

# Node copyleft scan (reads package.json license fields only - see §7 limitation)
cd web && find node_modules -name package.json | python -c "
import sys, json
for line in sys.stdin:
    try: d = json.load(open(line.strip(), encoding='utf-8'))
    except Exception: continue
    s = json.dumps(d.get('license') or d.get('licenses') or '')
    if any(k in s.upper() for k in ['GPL','MPL','SSPL','BUSL']):
        print(d.get('name'), d.get('version'), s)
"

# Vulnerability audits
uv run pip-audit
cd web && npm ci && npm audit
```

---

## Appendix A. Still planned — NOT INSTALLED

> **Every package in this appendix is absent from this repository.** **RAN**, verified 2026-08-24: `duckdb`, `redis`, `rq`, `arq`, `dramatiq`, `bandit`, `semgrep` all raise `PackageNotFoundError` in the venv; `zod`, `@tanstack/react-query`, `@tanstack/react-table`, `recharts`, `@xyflow/react`, `@monaco-editor/react`, `@radix-ui/*`, `vitest`, `@playwright/test` and `@codemirror/*` are all absent from `web/node_modules`. None appears in `uv.lock` or `web/package-lock.json`.
>
> **Every licence below is "from knowledge — verify before release."** Not one was read from installed metadata, because none of these packages exists here to read. Re-verify each against its current release at the moment it is added, using the §11.1 procedure. Licences change: `semgrep` in particular has reorganised its OSS/Pro split more than once, and Redis relicensed its server twice in three years.

### A.1 Python and tooling

| Package | Licence *(from knowledge — verify before release)* | What it would enable | Alternative considered |
|---|---|---|---|
| **bandit** | Apache-2.0 | AST-level Python security linting — hardcoded secrets, weak crypto, subprocess misuse. Planned for the CI security workflow: `docs/v2/IMPLEMENTATION_ROADMAP.md:125,151`, `docs/v2/TARGET_ARCHITECTURE.md:425` | **ruff's `S` rules (flake8-bandit), which need zero new dependencies.** ruff is already present and **READ**, `pyproject.toml:99`: `select = ["E","F","I","UP","B","C4","SIM","RUF"]` — `S` is not yet enabled. **Recommend this instead of adding bandit** |
| **semgrep** | LGPL-2.1 for the OSS CLI; registry/Pro features are commercial | Pattern-based static analysis beyond what an AST linter catches | **CodeQL**, already planned at `docs/v2/IMPLEMENTATION_ROADMAP.md:151` — free for public repositories but proprietary and GitHub-only. **semgrep would be the only copyleft item still planned**; it is safe because it is a dev-time CLI, never imported and never distributed, but that reasoning must be recorded in §4.1 when it lands |
| **trivy** | Apache-2.0 | Container and filesystem CVE scanning — `docs/v2/IMPLEMENTATION_ROADMAP.md:151`, `docs/v2/TARGET_ARCHITECTURE.md:425` | Grype (Apache-2.0) or Docker Scout (proprietary). **Note: trivy is a standalone Go binary invoked in CI, not a dependency of anything.** It never enters the image or either lockfile and carries no licence obligation at all |
| **duckdb** | MIT | Embedded analytical engine for demo and eval fixtures with no server — `docs/v2/TARGET_ARCHITECTURE.md:26,68,403` | **SQLite** (public domain), already usable and already the Alembic offline URL in `alembic.ini`. DuckDB earns its place only for columnar/analytical eval workloads |
| **redis-py** | **MIT** — and the distinction below matters | Distributed rate limiting and caching. Groundwork exists: **READ**, `app/core/config.py:105-106` (`upstash_redis_rest_url`, `upstash_redis_rest_token`) and `.env.example:109-111`, headed "OPTIONAL UPSTASH/REDIS (future distributed rate-limit/cache)"; `app/security/ratelimit.py:5` anticipates an Upstash/Redis-backed limiter behind the same interface | **The licence question here attaches to the server, not the client.** redis-py the client is MIT and always has been; the **Redis server** relicensed to RSALv2/SSPLv1 at 7.4 and added AGPLv3 as an option at 8.0. Using the MIT client against a managed endpoint creates no obligation; **bundling a modern Redis server image would.** **Valkey** (BSD-3-Clause, the Linux Foundation fork) is the clean alternative if a server is ever shipped. Also note: Upstash's REST API needs only an HTTP client, so `httpx` may make redis-py unnecessary |

### A.2 Job queue — this decision is already closed

The queue candidates are recorded for completeness, but **the architecture has already chosen, and it is none of them.** **READ**, `docs/v2/TARGET_ARCHITECTURE.md:175`:

```
queue.py                   DB-backed queue (SKIP LOCKED on Postgres; polling on SQLite)
```

with `app/jobs/` laid out at `docs/v2/TARGET_ARCHITECTURE.md:173-177`. **RAN**: the string "redis" appears **nowhere** in `docs/v2/`, `compose.yaml`, `render.yaml` or `Dockerfile`.

| Candidate | Licence *(from knowledge — verify before release)* | Why it lost |
|---|---|---|
| **rq** | BSD-3-Clause | Requires a Redis server — infrastructure the DB-backed design deliberately avoids |
| **arq** | MIT | Same: requires Redis |
| **dramatiq** | **LGPL-3.0** | Same infrastructure objection, **and it is the only copyleft option on the table.** Usable as an unmodified library, but the DB-backed choice makes the question moot |

Record this as a **closed decision**, with the licence note as supporting rationale rather than as an open choice. Re-opening it would need an argument against `SKIP LOCKED`, not an argument about licences.

### A.3 Frontend — none installed

All from `docs/v2/TARGET_ARCHITECTURE.md:410-413`. **READ**, `:410`: "Keep Next.js + TypeScript + Tailwind + the existing component style."

| Package | Licence *(from knowledge — verify before release)* | What it would enable | Alternative considered |
|---|---|---|---|
| **@tanstack/react-query** | MIT | Server-state caching and invalidation for the query console (`:410`) | Hand-rolled `useEffect` fetching — which is what `web/app` does today |
| **@tanstack/react-table** | MIT | Headless table for result grids (`:411`) | **AG Grid** — community build is MIT but enterprise features are commercial. Headless TanStack keeps the licence story simple |
| **recharts** | MIT | Charts from deterministic specs (`:411`) | Chart.js (MIT), or D3 (ISC) directly |
| **@xyflow/react** (React Flow) | MIT | Schema and join-graph visualization (`:412`) | Raw D3-force, or Cytoscape.js (MIT). **Note: the Pro examples and templates are commercially licensed; the library itself is MIT** |
| **@codemirror/\*** | MIT (every package in the CodeMirror 6 family) | SQL editor with syntax highlighting | **Already decided against Monaco, and for a non-licence reason.** **READ**, `docs/v2/TARGET_ARCHITECTURE.md:411-412`: "CodeMirror SQL editor (lighter than Monaco)". `@monaco-editor/react` is MIT and Monaco itself is MIT — **this was a bundle-size decision, not a licence one.** Recorded so it is not re-litigated |
| **@radix-ui/react-\*** | MIT | Accessible unstyled dialog/menu/tab primitives (`:412`) | Headless UI (MIT), or shadcn/ui — which is MIT and itself built on Radix |
| **zod** | MIT | Runtime schema validation at the API boundary, mirroring Pydantic server-side | Valibot (MIT, smaller), or TypeScript types alone — which vanish at runtime and therefore cannot validate an API response |
| **vitest** | MIT | Unit tests sharing the Vite/esbuild pipeline (`:413`, `docs/v2/IMPLEMENTATION_ROADMAP.md:145`) | Jest (MIT), slower on ESM/TS |
| **@playwright/test** | Apache-2.0 | E2E against the fake-provider backend, plus axe accessibility checks (`:413`, `docs/v2/IMPLEMENTATION_ROADMAP.md:145`) | **Cypress** — MIT core, but the Cloud dashboard is commercial. Note Playwright will pull **axe-core (MPL-2.0)** for the a11y assertions; already present transitively via `eslint-plugin-jsx-a11y`, and dev-only either way |

**Net licence position of everything still planned: `semgrep` (LGPL-2.1) and `dramatiq` (LGPL-3.0) are the only copyleft items.** Both are dev-time or already-avoided. `redis-py` is MIT, and its licence question attaches to the server, not the client.

---

## Appendix B. Provenance and known limits of this register

**Written 2026-08-24, branch `feat/dbwhisper-v2`, HEAD `e5c89f3`, working tree dirty.**

What was verified by **running** a command: all installed Python versions and licences; all importer scans; reverse-dependency scans; `pip-audit`; `npm audit`; `ruff check` (both scoped and unscoped) and `ruff format --check`; `pytest --collect-only`; the `git diff` of `pyproject.toml`; `git grep` over committed history for `networkx`; the full `web/node_modules` licence scan; distribution and lock-entry counts; `md5sum pyproject.toml`.

What was verified by **reading** a file: `LICENSE`; `pyproject.toml` in full; `Dockerfile`; `.github/workflows/ci.yml`; `.env.example` (key names only, via `sed -E "s/=.*/=<redacted>/"` — **no secret value was read or printed**); `web/package.json` and `web/package-lock.json`; the cited lines of `docs/v2/TARGET_ARCHITECTURE.md` and `docs/v2/IMPLEMENTATION_ROADMAP.md`; the shipped LICENSE files of `fastembed`, `py_rust_stemmers` and `psycopg`; and the cited source lines in `app/` and `db/`.

Known limits, stated so nobody over-trusts this document:

1. **The tree was being edited concurrently.** `app/llm/`, `app/embeddings/` and `app/retrieval/` all appeared during the audit, and `pyproject.toml` gained a line, shifting every reference past line 52. Every importer count and line number is a 2026-08-24T09:30Z snapshot against md5 `6260fd4620a1420fdb90f02195bc4fa5`.
2. **No `docker build` was run** — the Docker daemon is not running on this machine. Nothing about the built image was verified beyond reading the `Dockerfile`. The Debian dependency closure that `msodbcsql18` and `unixodbc` actually pull in was not enumerated.
3. **`uv sync --extra local-embeddings` was never run**, so §5's licences are unverified except `huggingface-hub`.
4. **pip-audit emitted an environment-mismatch warning** (§10.1) and was not re-run with `PIPAPI_PYTHON_LOCATION` set.
5. **`npm audit` ran against a stale `node_modules`** (§7, §10.4).
6. **The Node copyleft scan read `package.json` `license` fields only** (§7) and would miss a bundled-but-undeclared component of the kind numpy has on the Python side.
7. **The msodbcsql18 EULA text was not read** (§2.3). This is the most consequential gap in the register.
8. **No legal advice is given or implied here.** This is an engineering register of what is installed and what each package's own metadata says. Licence *interpretation* — particularly the LGPL §4/§6 analysis in §4.1 and the FOSS Exception discussion in §8.1 — should be confirmed by counsel before any commercial distribution.

**No file in this repository was modified in the course of this audit except this document.**
