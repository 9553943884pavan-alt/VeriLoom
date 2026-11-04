# VeriLoom implementation log

## 2026-11-04 — Backend foundation

- Created the project-local Python environment under `backend/.venv` and expanded `.gitignore` so it and `.env` stay untracked.
- Added a FastAPI app with `/healthz`, `/readyz`, request IDs, no-store responses, and safe error envelopes.
- Added register/login/me/logout routes with Argon2id password hashes, opaque hash-stored sessions, cookie and CSRF checks, Origin checks, and a per-process authentication rate limit.
- Added organisation creation/listing, scoped member and reviewer lists, role changes that preserve an active Admin, and email-bound single-use invitations.
- Added deterministic source chunking with exact offsets and bounded reciprocal-rank fusion for the later retrieval pipeline.
- Added Alembic migrations 001–005 for the planned 21-table PostgreSQL schema and `vector` extension. No migration has been applied to Render.
- Added pinned backend dependencies, local setup instructions, and an integration test for readiness success and failure.
- Verified 17 backend tests and generated Alembic's offline SQL through the full migration chain. `pip check` found no dependency conflicts. Ruff lint remains unchecked because its wheel download did not complete.
- Started the API locally with network access: `/healthz` and `/readyz` both returned `200`; the latter made a read-only database connection. The sandboxed run correctly returned a safe `503` when network access was unavailable. No schema change was made.

### Still to verify

- Apply migrations to a disposable PostgreSQL database and inspect the resulting schema and constraints.
- Apply the schema to Render only after an explicit review and approval of that live change.
- Test authentication and organisation SQL against a disposable migrated database, then implement source ingestion, draft/review, worker, the remaining RAG pipeline, and frontend slices.

The database and provider credentials in the ignored `.env` have not been printed in logs or copied into documentation.
