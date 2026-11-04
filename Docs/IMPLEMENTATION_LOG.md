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

## 2026-11-04 — Round 2 backend workflow code

- Added a polling worker with separate handlers for source ingestion, initial review, and reviewer follow-up questions. Exhausted job leases now mark their resource failed instead of leaving it processing indefinitely.
- Added local multilingual E5 embedding code with a pinned model revision, tenant-scoped approved-source dense and BM25 retrieval, reciprocal-rank fusion, and deterministic follow-up routing. The model weights have not been downloaded or run in this environment.
- Added Creator draft/revision routes, reviewer review/comment/question/decision routes, and retry handling. Human decisions remain separate from model output.
- Added a server-side Sarvam V2 structured JSON adapter. Initial review and follow-up handlers snapshot selected evidence before answer generation, validate exact passage IDs and source quotes, and persist citations only after validation. The user explicitly approved sending bounded private draft, guidance, finding, and recent chat context to Sarvam for these review and follow-up calls. No live Sarvam request was made during this implementation.
- Corrected worker and citation-read SQL to use the migration's `organisations.time_zone` column. Citation status now uses the organisation's local date.
- Verified 110 backend tests, Ruff across `app` and `tests`, `pip check`, and `git diff --check`. A local API smoke test returned `200` for `/healthz` and `/openapi.json`, and `401` for an unauthenticated review request. The local API process was stopped after testing.

### Remaining integration gates

- Run Alembic migrations and the Creator → Reviewer → worker flow against a disposable PostgreSQL database with pgvector; this environment has no running disposable PostgreSQL instance. Do not use Render production for this test.
- Install and exercise the pinned E5 model, and run a bounded structured-output smoke test against the selected Sarvam model. Test private R2 access with a test bucket or disposable object.
- Build the frontend and verify the full browser journey. No deployment or live Render migration has been performed.

## 2026-11-04 — Live backend integration verification

- Created a separate disposable PostgreSQL database named `veriloom_live_test_1104` on the configured instance, verified it was distinct from the configured application database, and applied Alembic migrations 001–005 there. The test database supported `pgvector`; no migration was applied to the configured application database.
- Installed and exercised the pinned local `intfloat/multilingual-e5-small` model: English and Hindi synthetic passage/query embeddings were 384-dimensional and normalized. A synthetic Cloudflare R2 object passed put/get/delete; the full workflow also uploaded, downloaded, ingested, and approved `.md` guidance from R2.
- Ran an opt-in live workflow using FastAPI requests, the real PostgreSQL queue, local E5 retrieval, R2, and Sarvam with synthetic accounts/content: Admin created an organisation and invited Creator/Reviewer; Origin and CSRF rejection passed; Admin uploaded and approved guidance; Creator submitted a draft; the worker produced a validated review; Reviewer asked a question that returned `answered` with one exact citation, requested changes, and approved the Creator's second immutable revision. Source polling, original download, role restrictions, and revision history passed. The live script is `backend/tests/integration/live_workflow_smoke.py` and requires an explicitly named, already migrated disposable database via `VERILOOM_LIVE_TEST_DATABASE`.
- The live run exposed and fixed registration's response field mismatch, PostgreSQL nullable-bind type errors in source listing and retrieval, decision idempotency serialization of a datetime, and incomplete Sarvam JSON responses. Sarvam's default reasoning consumed its output budget; the adapter now disables reasoning for `sarvam-105b`, and the handlers use a 4096-token limit with a 90-second read timeout. One review call retried successfully after a transient provider error. A narrow grounded-answer prompt revision (`grounded_answer/2.0.1`) produced a cited answer to a direct rule question. A broader question safely returned `insufficient_evidence`; this is an observed answer-quality limit, not a failed request.
- Verified 117 backend tests, Ruff on app/tests/Alembic, `pip check`, and `git diff --check`. A real localhost Uvicorn process returned `200` for `/healthz`, `/readyz`, and `/openapi.json`, and `401` for an unauthenticated organisation request; the process was stopped. The test client's installed Starlette/httpx combination emitted one deprecation warning.
- Deleted all four synthetic R2 originals recorded in the disposable database, then dropped that exact test database. No test object or test database was left behind.

### Remaining project work

- Build the planned React frontend and verify a real browser journey. No frontend directory or deployed end-to-end test exists yet.
- Complete the remaining API contract routes for source version replacement, source archive, and evidence detail; the verified Round 2 text review loop does not depend on them.
- Run the planned text-only development/holdout evaluation and latency measurements before claiming retrieval accuracy or production reliability. Recheck Sarvam answer quality across those cases. No production deployment or schema migration was performed.
