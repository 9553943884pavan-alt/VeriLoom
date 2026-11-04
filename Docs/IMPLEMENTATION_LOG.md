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

## 2026-11-05 — Remaining API contracts and synthetic IndieRouter fallback

- Completed the three remaining documented API routes: source version replacement, source archive, and authorised evidence passage detail. A replacement now stores its own title/category on the immutable version; the shared source display metadata changes only when the version is approved. Added forward Alembic migration 006 for these version fields. Migration 006 generated valid offline SQL but has **not** been applied to Render or a disposable live database.
- Added a server-side IndieRouter chat adapter with bounded retries, HTTPS/Bearer authentication, strict JSON-schema requests, and safe errors. A synthetic-only failover wrapper invokes IndieRouter after an exhausted transient Sarvam failure. It is not wired into the production worker, because the user approved only synthetic transfer to IndieRouter. No private draft or guidance was sent to IndieRouter.
- A live synthetic probe using `deepseek-v4.1-flash` passed the initial-review, follow-up rewrite, and grounded-answer schemas and local validators with the actual VeriLoom prompt templates. Earlier probes showed an invalid rewrite under a simplified prompt and an incomplete review response with thinking enabled. Disabling thinking using IndieRouter's documented `chat_template_kwargs` control resolved the incomplete response at the worker's 4096-token limit. These results establish one synthetic capability check, not accuracy or reliability on real reviews.
- Added an offline BM25Plus whole-draft retrieval baseline over 45 eligible synthetic knowledge records and 12 text drafts. At the 2026-11-05 cutoff, only one development draft and five holdout drafts had expected source IDs, so the other six are unscored. Development recall@5 was 0.0 (n=1); holdout recall@5 was 0.30 and recall@8 was 0.40 (n=5). This is a weak lexical baseline, not a production hybrid-retrieval score. Holdout labels were used only after ranking and must not drive tuning. Local query timing excludes embedding, database, worker, and provider time.
- Verified all 34 documented API methods against FastAPI OpenAPI routes, 149 backend tests (one existing Starlette/httpx deprecation warning), Ruff, Alembic offline SQL through migration 006, and a local HTTP smoke (`/healthz` and `/openapi.json` 200, unauthenticated evidence 401). The local API process was stopped.

### Remaining integration gates

- Apply migration 006 and exercise replacement/archive/evidence SQL in a disposable migrated PostgreSQL database before any Render schema change. The new routes have offline HTTP/unit coverage, but not this live database check.
- Keep the worker on Sarvam for private organisation material until IndieRouter private-data transfer is explicitly approved and provider/model attribution is persisted correctly on every fallback-generated review or answer.
- Build and test the React frontend; evaluate the actual hybrid retriever and generated findings on appropriate synthetic cases before making quality claims.

## 2026-11-05 — Disposable schema checks, approved fallback and hybrid evaluation

- Verified migrations through 006 and then 007 in a newly created disposable PostgreSQL database. The source SQL smoke covers immutable replacement metadata, activation/archive, historical evidence access and tenant isolation. The provider extension checks conservative historical backfills, all provider constraints, and actual review/answer publication SQL, including distinct rewrite and answer providers. The test database was dropped and its absence confirmed. The application database schema was not changed.
- The project owner explicitly approved private IndieRouter fallback. The worker now uses Sarvam first, with configured IndieRouter fallback only after exhausted transient retries. Permanent errors and malformed output do not trigger fallback. Both providers pass through existing output, citation and anchor validation. Migration 007 persists the provider/model that actually generated the review/answer and independent rewrite attribution. No-generation paths keep answer/review attribution null. Configuration and product specifications now disclose the transfer.
- Added `corpus_version` to organisation create/list responses and coordinated the API contract and frontend usage so source approval/archive can use optimistic concurrency.
- Persisted `backend/scripts/evaluate_hybrid_mvp.py` and [HYBRID_EVALUATION.md](HYBRID_EVALUATION.md). Actual pinned E5 embeddings and production Python query planning/BM25/RRF ran over 45 eligible knowledge records and 12 text drafts. The SQL boundary uses local exact cosine, so this does not measure live pgvector or deployed latency. Development recall@8 was 0 (one scoreable case); holdout recall@8 was 0.40 (five cases). Holdout labels were used after ranking, with no tuning.
- A bounded approved network generation probe used two synthetic cases and actual hybrid snapshots. Development NK-16 returned three structurally valid findings: one expected unsupported claim issue and two unwanted findings on manual audit. Holdout NK-21 was rejected by validation and was not retried. These results expose a retrieval and semantic usefulness gap; they do not establish reliable automated review quality. See the evaluation report for exact scope, timing and provenance.
- At this checkpoint, all 153 backend tests passed, Ruff passed for app/tests/Alembic/scripts, and `pip check` found no broken requirements. The initial root pytest invocation stalled with the cache plugin and was stopped; the completed run used `backend/` and `-p no:cacheprovider`. One existing Starlette/httpx deprecation warning remains.
- Added worker lease renewal during slow provider work. Before each handler commit, the worker checks and locks the still-owned, unexpired job using PostgreSQL wall-clock time. It rolls back failed transactions before stopping the renewal thread, and abandons an attempt after known/uncertain lease loss. Focused worker tests cover renewal, loss, commit fencing and thread cleanup.
- Real browser integration exposed untyped nullable SQL parameters in draft lists, revision history, comments and questions. Added explicit text/UUID/integer/timestamp casts. The disposable PostgreSQL smoke now executes every affected repository query with null and typed cursors; it passed and its test database was removed.
- Added nullable typed `decision` to participant-authorised review detail, including the change request/approval comment, override reason, actor and timestamp. This lets the Creator read human feedback after reload while retaining the separate comment conversation. Updated the API contract and frontend/database specifications together. The backend suite now has 166 passing tests, with the existing deprecation warning; Ruff passes.

## 2026-11-05 — React MVP and real browser workflow

- Built the React/TypeScript/Vite/Tailwind client with React Router, TanStack Query, React Hook Form and Zod. Screens cover authentication, organisation selection/creation, invitations and member roles, source upload/replacement/approval/archive, Creator working copies and revision history, Reviewer inbox/findings/citations/follow-ups/comments/decisions. Shared HTTP code sends same-origin cookies and CSRF; no provider keys enter the browser. Polling respects pending/terminal states and Retry-After; stale-copy/version errors preserve unsaved text. Suggestion application verifies NFC code-point anchors and requires Creator action.
- Passed `npm.cmd run typecheck`, `npm.cmd run lint`, `npm.cmd run test` (19 tests), and `npm.cmd run build`. Production JS was 384.09 kB (118.62 kB gzip). Tests cover request fields/cookies/CSRF/idempotency, safe errors, polling and Unicode/stale suggestion guards.
- Using the `playwright-cli` skill, verified a real localhost browser against a newly migrated disposable PostgreSQL database, the API, worker, private R2 originals, real E5 and Sarvam. Admin uploaded `.md` guidance, observed queued/processing/ready and approved it. Creator saved/submitted revision 1. Reviewer opened the inbox, immutable revision, anchored finding and authorised evidence, asked a follow-up, posted a comment and requested changes. Creator read feedback, revised/saved/submitted revision 2 and reloaded it. Reviewer approved revision 2. Creator saw Approved/read-only while revision 1 text and its decision reason remained available. The workspace had no horizontal overflow at a 390 px viewport. No browser errors remained after the SQL fixes and API restart.
- The follow-up completed as `insufficient_evidence` in this run. The model also proposed an unchanged edit and flagged compliant text; the UI displayed those proposals faithfully. These are observed model-quality limitations, not evidence of reliable suggestions. The persisted gate contained two Sarvam/sarvam-105b reviews, one completed Sarvam chat and four succeeded jobs. The final exceptional-path worker rollback change has offline coverage; the live worker exercised the ownership fence and successful handler commits.
- Stopped the owned browser/Vite, APIs and worker. Deleted the one exact test R2 original; dropped the browser database and confirmed its absence. The separate migration/cursor regression database was also dropped and confirmed absent. An unrelated existing process on port 8000 was preserved. No application database migration, production deployment or GitHub push was performed.
- Remaining verification: dedicated source-version page is planned (current details are inline); new replacement/member-role controls have static/build coverage but were not exercised in the live browser gate; production Vercel/HTTPS-cookie behavior needs deployment testing. Improve retrieval coverage and reduce unwanted findings using development cases before expanding quality claims. Preserve holdout separation.
