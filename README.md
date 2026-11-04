# VeriLoom

VeriLoom is a text-first brand review platform being built for the hackathon. The product workflow and interfaces are specified in [Docs/PRESENTATION.md](Docs/PRESENTATION.md) and [Docs/Product specification/README.md](Docs/Product%20specification/README.md). The current implementation is an early backend foundation; the complete review workflow is not yet available.

## Local backend setup

Use Python 3.12. If the Windows `py` launcher says no Python is installed, locate the installed `python.exe` and use its full path for the first command.

```powershell
# From the repository root
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-dev.txt
Copy-Item .env.example .env   # Only if you have not already created .env
```

Set `DATABASE_URL` to a PostgreSQL test database URL and `SESSION_SECRET` to a long random value in the ignored `.env`. Other provider settings are needed when their integrations are implemented. Never put real credentials in `.env.example` or browser variables.

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

`GET /healthz` checks that the API process responds. `GET /readyz` checks a short PostgreSQL connection; it returns `503` if the database is unavailable. API documentation is at `/docs` while the server runs. The first `/api/v1/auth` routes are implemented, but a real database-backed account flow needs the schema migration and an integration check.

## Schema and tests

Alembic migrations are under `backend/alembic/versions/`. Before applying them, verify the target database: migrations create the schema and the `vector` extension. Do not point development tests at a database containing real organisational data. From `backend/`, run:

```powershell
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
.\.venv\Scripts\python.exe -m pytest tests
```

The migration command above changes the database configured by `DATABASE_URL`. Only use it for a disposable local database unless the live Render migration has been separately reviewed and approved. The initial migration has no destructive rollback.
