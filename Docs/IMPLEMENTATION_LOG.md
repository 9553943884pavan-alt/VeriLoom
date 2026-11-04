# VeriLoom implementation log

## 2026-11-04 — Backend foundation

- Created the project-local Python environment under `backend/.venv` and expanded `.gitignore` so it and `.env` stay untracked.
- Added a FastAPI app with `/healthz`, `/readyz`, request IDs, no-store responses, and safe error envelopes.
- Added register/login/me/logout routes with Argon2id password hashes, opaque hash-stored sessions, cookie and CSRF checks, Origin checks, and a per-process authentication rate limit.
- Added organisation creation/listing, scoped member and reviewer lists, role changes that preserve an active Admin, and email-bound single-use invitations.
- Added deterministic source chunking with exact offsets and bounded reciprocal-rank fusion for the later retrieval pipeline.
- Added Alembic migrations 001–005 for the planned 21-table PostgreSQL schema and `vector` extension. No migration has been applied to Render.
- Added pinned backend dependencies, local setup instructions, and an integration test for readiness success and failure.
- Verified 17 backend tests and generated Alembic's offline SQL through the full migration chain. `pip check` found no dependency conflicts. Ruff was installed and passed in the next backend slice.
- Started the API locally with network access: `/healthz` and `/readyz` both returned `200`; the latter made a read-only database connection. The sandboxed run correctly returned a safe `503` when network access was unavailable. No schema change was made.

### Still to verify

- Apply migrations to a disposable PostgreSQL database and inspect the resulting schema and constraints.
- Apply the schema to Render only after an explicit review and approval of that live change.
- Test authentication and organisation SQL against a disposable migrated database, then implement source ingestion, draft/review, worker, the remaining RAG pipeline, and frontend slices.

The database and provider credentials in the ignored `.env` have not been printed in logs or copied into documentation.

## 2026-11-04 — Source ingestion infrastructure

- Added a private Cloudflare R2 adapter with bounded 2 MiB reads/writes, explicit timeouts and retries, injectable test client, and safe storage errors.
- Added PostgreSQL job queue operations for enqueue, `SKIP LOCKED` claim, lease renewal, completion, failure, and bounded retry scheduling. These operations use caller-owned transactions and have not yet been exercised against a migrated PostgreSQL test database.
- Added strict `.txt`/`.md` UTF-8 guidance validation, raw-byte SHA-256, and NFC/LF text normalisation for exact-span chunking.
- Added the first Admin source upload route: authorise, validate, store the private original, insert a pending source version and ingestion job in one database transaction, then return `202`. A failed database write attempts to remove the private orphan object. Upload idempotency keys replay the matching result and reject changed or expired requests.
- Added Admin approval of an ingested source version. The transaction locks the organisation and source, checks the corpus version, ready/pending status, passages, and organisation-local effective dates, then archives the previous approved version and increments the corpus version.
- Verified the upload route with an offline HTTP test and fake database/storage dependencies. This does not establish that live R2 or PostgreSQL ingestion works.
- Verified 55 backend tests, Ruff across app/tests/migrations, and `pip check`. The only test warning is a Starlette/httpx deprecation notice from the installed test client.

### Next backend work

- Verify migrations and queue/source SQL on a disposable PostgreSQL database before using Render.
- Add the worker ingestion handler, local E5 embeddings, passage persistence, and retrieval eligibility checks; current approval requires ready passages and therefore cannot complete a real upload until ingestion exists.
- Complete draft submission, review findings, reviewer follow-ups, and frontend integration.
