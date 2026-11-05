# VeriLoom

VeriLoom is a text-first brand review platform being built for the hackathon. The product workflow and interfaces are specified in [Docs/PRESENTATION.md](Docs/PRESENTATION.md) and [Docs/Product specification/README.md](Docs/Product%20specification/README.md). The backend code includes authentication, organisations, Admin guidance upload and approval, private R2 storage, draft submission, human review decisions, and a durable worker for ingestion, initial review, and follow-up answers. A synthetic end-to-end backend run against a disposable migrated PostgreSQL database, R2, local E5 embeddings, and Sarvam passed on 2026-11-04; see [Docs/IMPLEMENTATION_LOG.md](Docs/IMPLEMENTATION_LOG.md) for the exact scope and limits.

The React MVP is in `frontend/`. Its local browser check completed the Admin → Creator → Reviewer → Creator → Reviewer workflow with real PostgreSQL, R2, E5 and Sarvam, including revision history and human decision reasons. The measured retrieval and model-output limits are documented in [Docs/HYBRID_EVALUATION.md](Docs/HYBRID_EVALUATION.md); workflow completion does not establish review accuracy.

## RAG evaluation and latency

These are **local synthetic-fixture measurements from 2026-11-05**, not real-world accuracy or deployed performance. The retriever uses pinned local multilingual E5 embeddings, BM25Plus and reciprocal-rank fusion (RRF), then selects at most eight passages. The default worker uses the Stage 1 query plan and finding validator; the Stage 2 purpose-query planner and Stage 3 finding filter remain opt-in after their release checks.

| Retrieval result | Default Stage 1 | Experimental purpose queries | Interpretation |
|---|---:|---:|---|
| Development: required evidence groups in candidate pool | 23/25 | 25/25 | 20 authored cases; four unavailable groups excluded. |
| Development: required groups in final eight | 20/25 | 25/25 | The experimental gain was measured on development cases only. |
| Fresh Stage 2 holdout: candidate groups | 14/17 | 14/17 | 12 independently authored synthetic cases. |
| Fresh Stage 2 holdout: final-eight groups | **14/17** | 13/17 | The experiment lost one group, so it was not enabled by default. |

Both variants used the same 45 approved fictional knowledge records and pinned E5 model. The development and holdout evaluations ran the production Python retrieval logic with exact-cosine SQL **simulated locally**; they did not measure PostgreSQL network or deployed latency. A separate disposable, migrated PostgreSQL/pgvector test verified Markdown ingestion, stored vectors, scoped SQL retrieval, and exclusion of ineligible source versions and other tenants. See [the full retrieval methods and case results](Docs/HYBRID_EVALUATION.md#stage-1-development-only-retrieval-baseline-2026-11-05).

| Measured time or resource | Observation | Boundary |
|---|---:|---|
| Default Stage 1 retrieval | 43.879 ms mean; 49.670 ms p95 | One local 20-case serial development run; includes query embedding, simulated cosine, BM25 and fusion. |
| Purpose-query retrieval | 55.0 ms mean vs 38.5 ms default | One warm local comparison; extra searches added cost. A second run measured 55.6 vs 40.9 ms. |
| E5 model load and 45 passage embeddings | 12.612 s and 1.113 s | Separate from the Stage 1 retrieval timing above. |
| E5-loaded Windows process working set | About 807 MB | Local process observation, not a Render worker peak. |
| Sarvam generation | 3.291 s mean | Three recorded synthetic `sarvam-105b` completions; excludes retrieval, database and worker time. |

Finding quality was evaluated separately. On a frozen 16-case **authored candidate replay**, the default validator had 9 true positives, 5 false positives and 2 false negatives; an optional filter reduced false positives to 1 but also lost a genuine finding (8 true positives, 3 false negatives). In the three live synthetic Sarvam cases, both validators retained the same outputs, including an unwanted finding on compliant text. The filter therefore remains off by default. These small tests do not establish general finding accuracy, citation meaning, follow-up quality or full end-to-end latency. [Finding-quality results and release decision](Docs/HYBRID_EVALUATION.md#stage-3-independent-release-gate-assessment-2026-11-05).

## Local backend setup

Use Python 3.12. If the Windows `py` launcher says no Python is installed, locate the installed `python.exe` and use its full path for the first command.

```powershell
# From the repository root
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-dev.txt
Copy-Item .env.example .env   # Only if you have not already created .env
```

Set `DATABASE_URL` to a PostgreSQL test database URL and `SESSION_SECRET` to a long random value in the ignored `.env`. The source upload route also needs the `R2_*` settings listed in `.env.example`. Never put real credentials in `.env.example` or browser variables.

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In another terminal, start the separate worker after pointing `.env` at a **disposable migrated test database** and configuring private R2 and Sarvam credentials:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m app.jobs.worker
```

`GET /healthz` checks that the API process responds. `GET /readyz` checks a short PostgreSQL connection; it returns `503` if the database is unavailable. API documentation is at `/docs` while the server runs. A real database-backed account and source upload flow needs the schema migration and an integration check against a disposable PostgreSQL database.

## Schema and tests

Alembic migrations are under `backend/alembic/versions/`. Before applying them, verify the target database: migrations create the schema and the `vector` extension. Do not point development tests at a database containing real organisational data. From `backend/`, run:

```powershell
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
.\.venv\Scripts\python.exe -m pytest tests
```

For local pytest runs where its cache plugin stalls on Windows, use `-p no:cacheprovider`. No production migration or deployment has been performed by these setup instructions.

## Local frontend setup

Use Node.js 20.19+ or a compatible current Node release. Keep the API and worker running against the same disposable migrated database. With the default `APP_ORIGIN=http://localhost:5173`, start the frontend in a third terminal:

```powershell
Set-Location frontend
npm ci
npm run dev -- --port 5173 --strictPort
```

Open `http://localhost:5173`. Browser requests use the Vite same-origin proxy to the API on port 8000; session cookies remain HttpOnly. If that API port is occupied, set the frontend terminal's server-only `VERILOOM_API_PROXY_TARGET` to your local API URL before starting Vite. If the frontend port changes, set backend `APP_ORIGIN` to the exact browser origin. Never put provider credentials in frontend variables.

Run `npm run typecheck`, `npm run lint`, `npm test`, and `npm run build` from `frontend/`. Source uploads support `.txt`/`.md`; submitted drafts are text. Human reviewers make approval decisions. Retrieval and generated findings have observed quality limits in [the hybrid evaluation report](Docs/HYBRID_EVALUATION.md).

## Synthetic evaluation

From `backend/`, run `.\.venv\Scripts\python.exe scripts/evaluate_hybrid_mvp.py` with cached E5 weights for a provider-free retrieval evaluation. Add `--generate-smoke` only when you intend to send the two documented synthetic cases to configured providers. The script simulates the SQL cosine boundary and must not be presented as live pgvector benchmarking. The opt-in disposable SQL check is `.\.venv\Scripts\python.exe -m tests.integration.live_source_sql_smoke`; it creates and removes its own separate database using the configured server account's database-creation permission.

## Optional IndieRouter synthetic probe

The worker sends review and follow-up requests to Sarvam first. If Sarvam exhausts transient retries and `INDIEROUTER_API_KEY` is configured, IndieRouter receives the needed draft, approved evidence and bounded chat context. This private-data transfer was approved by the project owner. Migration 007 records the actual provider/model for reviews, answers and query rewrites; apply it to an approved test database before running the updated worker. A live synthetic probe passed the three product JSON schemas and local validators on 2026-11-05 with thinking disabled. To repeat it, run from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_indierouter --schemas
```

This probe sends a canned greeting and invented draft/guidance text, forces a synthetic Sarvam transient failure, and checks the initial-review, follow-up rewrite, and grounded-answer outputs. It does not load organisation data. IndieRouter's [model notes](https://indierouter.ai/blog/deepseek-v4-1-flash-1017/) document the thinking control used by the probe. Permanent Sarvam errors and malformed output do not trigger fallback. Provider/schema capability checks do not establish review quality.

For an offline lexical retrieval baseline over the synthetic text fixtures, run `.\backend\.venv\Scripts\python.exe -m scripts.evaluate_text_mvp` from the repository root, or `.\.venv\Scripts\python.exe -m scripts.evaluate_text_mvp` from `backend/`. This measures BM25Plus whole-draft ranking only; it does not evaluate the live dense/hybrid retriever or generated findings.

The migration command above changes the database configured by `DATABASE_URL`. Only use it for a disposable local database unless the live Render migration has been separately reviewed and approved. The initial migration has no destructive rollback.
