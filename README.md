# VeriLoom

VeriLoom is a text-first brand review platform being built for the hackathon. The product workflow and interfaces are specified in [Docs/PRESENTATION.md](Docs/PRESENTATION.md) and [Docs/Product specification/README.md](Docs/Product%20specification/README.md). The backend code includes authentication, organisations, Admin guidance upload and approval, private R2 storage, draft submission, human review decisions, and a durable worker for ingestion, initial review, and follow-up answers. A synthetic end-to-end backend run against a disposable migrated PostgreSQL database, R2, local E5 embeddings, and Sarvam passed on 2026-11-04; see [Docs/IMPLEMENTATION_LOG.md](Docs/IMPLEMENTATION_LOG.md) for the exact scope and limits.

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

## Optional IndieRouter synthetic probe

The worker currently sends review and follow-up requests to Sarvam. IndieRouter is a fallback candidate. Its adapter has offline tests, and a live synthetic probe passed the three product JSON schemas and local validators on 2026-11-05 with thinking disabled. Put `INDIEROUTER_API_KEY` in the ignored local `.env`, then run from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_indierouter --schemas
```

This probe sends a canned greeting and invented draft/guidance text, forces a synthetic Sarvam transient failure, and checks the initial-review, follow-up rewrite, and grounded-answer outputs. It does not load organisation data. IndieRouter's [model notes](https://indierouter.ai/blog/deepseek-v4-1-flash-1017/) document the thinking control used by the probe. Automatic failover for private organisation content remains disabled until that transfer and persisted provider/model attribution are approved and verified.

For an offline lexical retrieval baseline over the synthetic text fixtures, run `.\backend\.venv\Scripts\python.exe -m scripts.evaluate_text_mvp` from the repository root, or `.\.venv\Scripts\python.exe -m scripts.evaluate_text_mvp` from `backend/`. This measures BM25Plus whole-draft ranking only; it does not evaluate the live dense/hybrid retriever or generated findings.

The migration command above changes the database configured by `DATABASE_URL`. Only use it for a disposable local database unless the live Render migration has been separately reviewed and approved. The initial migration has no destructive rollback.
