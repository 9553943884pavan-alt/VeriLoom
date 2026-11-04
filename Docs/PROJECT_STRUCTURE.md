# VeriLoom project structure

**Status:** Round 2 destination map with implemented slices recorded below. The backend and initial React frontend now exist; unimplemented entries in the original tree remain planned. Keep the existing `Docs/` and `data/` files in place. Build the text-only workflow first, then add later formats only when the core flow works.

This is a destination map, not a request to create every empty file at once. Add a module when its vertical slice is implemented, and merge very small modules if separate files would add navigation cost for a solo builder.

## 1. Repository tree

### Implemented compact MVP modules

Small related screens are deliberately grouped within feature modules, following the modularity rule below. Split them further when their size or reuse warrants it.

| Path | Responsibility |
|---|---|
| `frontend/src/main.tsx`, `app/App.tsx`, `app/context.ts` | Application providers, routing, authentication and organisation context, membership/invitation screens. |
| `frontend/src/features/auth/Auth.tsx` | Login/register forms with React Hook Form and Zod. |
| `frontend/src/features/sources/Sources.tsx` | Text guidance upload, processing, approval and archive. |
| `frontend/src/features/drafts/Drafts.tsx` | Creator working copy, immutable history, reviewer inbox. |
| `frontend/src/features/reviews/Reviews.tsx` | Findings, citations, questions, comments, human decisions. |
| `frontend/src/components/ui.tsx` | Shared forms, error/status rendering, cursor pagination and polling. |
| `frontend/src/lib/api/client.ts`, `lib/types.ts`, `lib/anchors.ts` | Same-origin HTTP/CSRF, contract types and code-point suggestion guards; adjacent `*.test.ts` files. |
| `frontend/src/style.css` | Tailwind import and responsive visual styles. |
| `frontend/{package.json,package-lock.json,tsconfig.json,vite.config.ts,eslint.config.js,index.html}` | Pinned frontend toolchain and Vite API proxy. |
| `backend/scripts/evaluate_hybrid_mvp.py`, `Docs/HYBRID_EVALUATION.md` | Reproducible E5 hybrid evaluation with explicit SQL simulation and optional bounded synthetic generation. |
| `backend/tests/integration/live_source_sql_smoke.py` | Disposable database checks for migrations 006/007, source lifecycle/evidence and provider provenance. |
| `backend/tests/unit/test_review_decision_read.py` | Participant access, nullable human decision summary and decision query scope. |

`[existing]` means the file is already in the workspace. `[planned]` means it belongs to the implementation plan; every child of a planned directory is also planned. Unmarked children of an existing directory already exist unless labelled otherwise. Descriptive Alembic revision names below may gain generated revision IDs.

```text
VeriLoom/
├── AGENTS.md                                      [existing] project working rules
├── .gitignore                                     [existing] local secrets and generated files
├── .env.example                                   [existing] configuration checklist, no secrets
├── README.md                                      [planned] setup, demo accounts, run commands
├── Docs/                                          [existing] challenge and design documents
│   ├── PROJECT_STRUCTURE.md                       [existing] this proposed layout
│   ├── Problem_statement.md                       [existing] challenge brief
│   ├── PRESENTATION.md                            [existing] product intent and round plan
│   ├── PDF.md                                     [existing] Round 1 submission source
│   ├── VeriLoom_Round1_THE_KING.pdf               [existing] Round 1 artifact
│   ├── IMPLEMENTATION_LOG.md                      [planned] dated build progress and decisions
│   ├── DEPLOYMENT.md                              [planned] Render, R2, and Vercel setup
│   ├── EVALUATION.md                              [planned] actual test results and limitations
│   └── Product specification/                     [existing] implementation contracts
│       ├── README.md                              [existing] specification index
│       ├── API Contracts.md                       [existing] public API and states
│       ├── Database Design and ERD.md             [existing] tables and migrations
│       ├── Frontend Architecture Specification.md [existing] UI routes and components
│       ├── RAG Architecture Specification.md      [existing] ingestion and retrieval
│       └── Prompt Engineering Specification.md    [existing] versioned model instructions
├── data/                                          [existing] synthetic seed and offline evaluation
│   ├── README.md
│   ├── AUDIT.md
│   ├── manifest.json
│   ├── validation_report.json
│   ├── validate_data.py
│   ├── refresh_manifest.py
│   ├── research_sources.md
│   ├── brand_sources/
│   │   ├── brand_guidelines.md
│   │   ├── approved_facts.md
│   │   ├── terminology.md
│   │   └── nook_kettle_master.svg
│   ├── knowledge/
│   │   ├── records.jsonl                         approved demo seed input
│   │   ├── isolation_fixtures.jsonl              scope tests only
│   │   ├── isolation_fixture_sources.md
│   │   └── build_records.py
│   ├── evaluation/
│   │   ├── drafts.jsonl
│   │   ├── drafts.md
│   │   ├── followup_cases.jsonl
│   │   ├── followup_cases.jsonl.md
│   │   ├── scope_cases.jsonl
│   │   └── answer_key.md
│   ├── schemas/
│   │   ├── knowledge.schema.json
│   │   ├── draft.schema.json
│   │   ├── followup.schema.json
│   │   ├── scope_case.schema.json
│   │   ├── public_reference.schema.json
│   │   └── problem_evidence.schema.json
│   ├── public_references/
│   │   ├── references.jsonl
│   │   ├── problem_evidence.jsonl
│   │   ├── SOURCES.md
│   │   └── PROBLEM_EVIDENCE.md
│   └── research/
│       ├── interview_guide.md
│       ├── survey.md
│       ├── consent_privacy.md
│       ├── rag_evaluation_template.md
│       └── research_log_template.md
├── backend/                                       [planned] FastAPI and one Render worker codebase
│   ├── requirements.txt                          pinned runtime dependencies
│   ├── requirements-dev.txt                      test and lint dependencies
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   │       ├── 001_extensions.py
│   │       ├── 002_identity.py
│   │       ├── 003_knowledge.py
│   │       ├── 004_review.py
│   │       ├── 005_jobs.py
│   │       ├── 006_source_version_metadata.py     snapshot replacement metadata per version
│   │       ├── 007_provider_attribution.py       persist winning review/answer provider
│   │       └── 008_vector_index_optional.py      add only after measurement
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                                FastAPI creation and router mounting
│   │   ├── core/
│   │   │   ├── config.py                          typed environment settings
│   │   │   ├── security.py                        sessions, passwords, CSRF, role checks
│   │   │   ├── errors.py                          stable API error mapping
│   │   │   └── logging.py                         request IDs and safe diagnostics
│   │   ├── api/
│   │   │   ├── dependencies.py                    session and organisation access
│   │   │   ├── health.py                          /healthz and /readyz
│   │   │   └── v1/
│   │   │       ├── auth.py
│   │   │       ├── organisations.py               orgs, members, reviewer picker
│   │   │       ├── invitations.py
│   │   │       ├── sources.py                     upload, versions, approval, archive
│   │   │       ├── evidence.py                    authorised passage detail
│   │   │       ├── drafts.py                      working copy and submissions
│   │   │       └── reviews.py                     findings, questions, comments, decisions
│   │   ├── schemas/                              Pydantic public request/response types
│   │   │   ├── common.py
│   │   │   ├── auth.py
│   │   │   ├── organisations.py
│   │   │   ├── sources.py
│   │   │   ├── evidence.py                   authorised passage detail types
│   │   │   ├── drafts.py
│   │   │   └── reviews.py
│   │   ├── db/
│   │   │   ├── session.py                         PostgreSQL engine and transactions
│   │   │   ├── models/
│   │   │   │   ├── identity.py                    users, orgs, membership, sessions
│   │   │   │   ├── knowledge.py                   sources, versions, passages
│   │   │   │   ├── workflow.py                    drafts, reviews, evidence, decisions
│   │   │   │   └── operations.py                  jobs and idempotency records
│   │   │   └── repositories/
│   │   │       ├── identity.py
│   │   │       ├── sources.py
│   │   │       ├── evidence.py                scoped passage and citation reads
│   │   │       ├── drafts.py
│   │   │       ├── reviews.py
│   │   │       └── jobs.py
│   │   ├── services/                             authorisation and state transitions
│   │   │   ├── authentication.py
│   │   │   ├── organisations.py
│   │   │   ├── sources.py
│   │   │   ├── evidence.py                    authorised historical evidence
│   │   │   ├── drafts.py
│   │   │   ├── reviews.py
│   │   │   └── questions.py
│   │   ├── rag/
│   │   │   ├── ingestion.py                      UTF-8 extraction and source version checks
│   │   │   ├── chunking.py                       heading/paragraph/sentence splits
│   │   │   ├── embeddings.py                     local E5 query/passages
│   │   │   ├── retrieval/
│   │   │   │   ├── dense.py                      scoped pgvector search
│   │   │   │   ├── bm25.py                       scoped lexical cache/search
│   │   │   │   ├── fusion.py                     RRF and deduplication
│   │   │   │   └── service.py                    eligible corpus and candidate limits
│   │   │   ├── reranking.py                      optional local cross-encoder
│   │   │   ├── followup.py                       reuse/rewrite/fresh/clarify/abstain
│   │   │   └── grounding.py                      citation and exact-quote validation
│   │   ├── prompts/
│   │   │   ├── catalog.py                        prompt IDs, versions, template loading
│   │   │   ├── output_schemas.py                 private structured model results
│   │   │   └── v2_0_0/
│   │   │       ├── shared_system.txt
│   │   │       ├── initial_review.txt
│   │   │       ├── rewrite_followup.txt
│   │   │       └── grounded_answer.txt
│   │   ├── integrations/
│   │   │   ├── r2.py                             private object storage adapter
│   │   │   ├── sarvam.py                         server-side V2 chat adapter
│   │   │   ├── openai_compatible.py              configurable verified fallback adapter
│   │   │   └── failover.py                       Sarvam-first provider failover chain
│   │   └── jobs/
│   │       ├── queue.py                          PostgreSQL job claiming and retries
│   │       ├── worker.py                         separate Render worker entry point
│   │       └── handlers/
│   │           ├── ingest_source.py
│   │           ├── review_revision.py
│   │           └── answer_question.py
│   ├── scripts/
│   │   ├── seed_demo.py                          idempotent approved demo data seed
│   │   ├── smoke_indierouter.py                 opt-in synthetic-only provider probe
│   │   └── evaluate_text_mvp.py                  offline text-only evaluation
│   └── tests/
│       ├── conftest.py                           isolated test DB and test clients
│       ├── unit/
│       │   ├── test_chunking.py
│       │   ├── test_fusion.py                    bounded reciprocal-rank fusion
│       │   ├── test_r2.py                        private object adapter contract
│       │   ├── test_openai_compatible.py         synthetic IndieRouter wire tests
│       │   ├── test_failover.py                  synthetic-only provider failover
│       │   ├── test_evidence.py                  scoped historical passage detail
│       │   ├── test_evaluate_text_mvp.py         offline text evaluation metrics
│       │   ├── test_ingestion.py                 text upload and normalization rules
│       │   ├── test_job_queue.py                 durable job claim and retry rules
│       │   ├── test_retrieval_scoping.py
│       │   ├── test_finding_anchors.py
│       │   └── test_prompt_validation.py
│       └── integration/
│           ├── live_workflow_smoke.py            opt-in disposable DB, R2, Sarvam workflow smoke
│           ├── test_health.py                   local liveness/readiness contract
│           ├── test_auth_and_tenancy.py
│           ├── test_organisations.py             organisation roles and invitations
│           ├── test_source_lifecycle.py
│           ├── test_review_workflow.py
│           └── test_job_retries.py
└── frontend/                                      [planned] React application on Vercel
    ├── package.json
    ├── package-lock.json                          if npm is selected at setup
    ├── index.html
    ├── tsconfig.json
    ├── vite.config.ts                            local /api proxy
    ├── vercel.json                               /api rewrite then SPA fallback
    ├── src/
    │   ├── main.tsx
    │   ├── styles.css                            Tailwind entry and app styles
    │   ├── app/
    │   │   ├── router.tsx                        organisation routes and role guards
    │   │   ├── providers.tsx                     TanStack Query and router setup
    │   │   ├── AppShell.tsx
    │   │   └── OverviewPage.tsx
    │   ├── lib/
    │   │   ├── api/
    │   │   │   ├── client.ts                     same-origin cookie/CSRF/error client
    │   │   │   └── contracts.ts                  types matching API JSON
    │   │   ├── queryKeys.ts                      organisation-scoped cache keys
    │   │   ├── codepoints.ts                     NFC and Unicode span conversion
    │   │   └── errors.ts                         typed API error display
    │   ├── components/
    │   │   ├── StatusBadge.tsx
    │   │   ├── EvidenceCitation.tsx
    │   │   ├── EvidencePanel.tsx
    │   │   ├── InlineError.tsx
    │   │   └── ConfirmDialog.tsx
    │   ├── features/
    │   │   ├── auth/
    │   │   │   ├── api.ts
    │   │   │   ├── LoginPage.tsx
    │   │   │   ├── RegisterPage.tsx
    │   │   │   ├── OrganisationPage.tsx
    │   │   │   ├── InvitationAcceptPage.tsx
    │   │   │   ├── MembersPage.tsx
    │   │   │   └── MemberInvitationForm.tsx
    │   │   ├── sources/
    │   │   │   ├── api.ts
    │   │   │   ├── SourceLibraryPage.tsx
    │   │   │   ├── SourceVersionPage.tsx
    │   │   │   └── SourceUploadForm.tsx
    │   │   ├── drafts/
    │   │   │   ├── api.ts
    │   │   │   ├── DraftListPage.tsx
    │   │   │   ├── DraftEditorPage.tsx
    │   │   │   ├── RevisionTimeline.tsx
    │   │   │   ├── ReviewerPicker.tsx
    │   │   │   └── SuggestedEditAction.tsx
    │   │   └── reviews/
    │   │       ├── api.ts
    │   │       ├── ReviewerInboxPage.tsx
    │   │       ├── ReviewWorkspacePage.tsx
    │   │       ├── FindingList.tsx
    │   │       ├── FollowUpThread.tsx
    │   │       ├── CommentThread.tsx
    │   │       └── DecisionForm.tsx
    │   └── tests/
    │       ├── codepoints.test.ts
    │       └── apiErrors.test.ts
    └── e2e/
        └── reviewFlow.spec.ts                     deployed or local full-loop smoke test
```

## 2. Why these boundaries matter

| Location | Owns | Must not do |
|---|---|---|
| `backend/app/api/` and `schemas/` | Public HTTP validation, authentication dependencies, status codes, and JSON shapes | Call Sarvam inside the request for a long review or trust browser role claims. |
| `backend/app/services/` and `db/repositories/` | Business transitions and scoped persistence | Duplicate approval rules inside routers or the frontend. |
| `backend/app/jobs/` | Durable ingestion, initial review, and follow-up execution after the API returns `202` | Approve content or create a second submitted revision on retry. |
| `backend/app/rag/` | Approved-source ingestion, scoped hybrid retrieval, optional reranking, follow-up routing, and evidence validation | Put drafts or evaluation answer keys into the approved corpus. |
| `backend/app/prompts/` | Versioned system/task instructions and private model output schemas | Define public API response shapes or replace server-side validation. |
| `frontend/src/` | Screens, form state, polling, user-controlled suggested edits, and accessible feedback | Call PostgreSQL, R2, or Sarvam directly, or treat a route guard as authorisation. |
| `data/` | Synthetic seed input and offline test fixtures | Become runtime authority without organisation approval. |

The API and worker share `backend/app/` code and one database schema, but run as **separate Render services**. The worker loads local E5 once and processes PostgreSQL jobs. The browser reaches only same-origin `/api/v1` through the Vite development proxy or Vercel rewrite. The local `backend/.venv`, `frontend/node_modules`, build output, downloaded model weights, and real `.env` are generated or private; add the corresponding `.gitignore` rules before creating them so they stay out of commits.

## 3. Build order for a solo Round 2 implementation

1. Create the Git repository, root `README.md`, backend/frontend skeletons, pinned dependencies, `.gitignore` rules for generated files, local configuration, and a simple health route during the official build window.
2. Add Alembic identity and knowledge migrations, sessions/roles, private R2 upload, durable job claiming, text extraction, E5 embeddings, and Admin source approval. Verify tenant and approval filtering before model calls.
3. Add Creator working copies, immutable revisions, Reviewer assignment, source-grounded initial review, citations, and status polling. Generate OpenAPI and reconcile it with the API contract.
4. Add reviewer follow-up routing, comments, change requests, resubmissions, approval, and historical source/revision display. Connect React screens slice by slice to real endpoints.
5. Run text-only development cases, preserve holdout until final evaluation, document actual results, and smoke-test the deployed Vercel → Render → PostgreSQL/R2/Sarvam loop. Add optional reranking only after measuring its benefit and resource cost.

## Reference boundaries

- [Problem_statement.md](Problem_statement.md) and [PRESENTATION.md](PRESENTATION.md) define the challenge, product scope, and round plan. This file proposes locations for implementation; it does not expand the text MVP.
- [API Contracts.md](<Product specification/API Contracts.md>) owns public routes and state names. [Database Design and ERD.md](<Product specification/Database Design and ERD.md>) owns tables and migration order. [Frontend Architecture Specification.md](<Product specification/Frontend Architecture Specification.md>) owns UI behavior. [RAG Architecture Specification.md](<Product specification/RAG Architecture Specification.md>) owns retrieval. [Prompt Engineering Specification.md](<Product specification/Prompt Engineering Specification.md>) owns private prompts and output constraints.
- [data/README.md](../data/README.md) defines seed eligibility and evaluation boundaries. [AGENTS.md](../AGENTS.md) owns coding, Git, Windows virtual environment, and verification rules. [`.env.example`](../.env.example) lists configuration names only.
