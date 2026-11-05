# VeriLoom API Contracts — Round 2 backend blueprint

**Status:** proposed contract, version `v1.0-draft`; no endpoint described here is claimed to be implemented.  
**Owners:** solo participant; this document is the shared agreement for the React frontend, FastAPI backend, retrieval worker, and Sarvam AI adapter.  
**Scope:** the Round 2 text workflow. PDF/image understanding, notifications, and analytics remain later additions.

## 1. Decisions and boundaries

| Decision | Contract |
|---|---|
| Public API | JSON over HTTPS at `/api/v1`; browser calls relative `/api/v1/...` on the Vercel origin. A Vercel rewrite forwards `/api/:path*` to FastAPI on Render. Local Vite uses the same path through its dev proxy. |
| Authentication | Email/password accounts and opaque server-side sessions in PostgreSQL. Browser receives a `Secure`, `HttpOnly`, `SameSite=Lax`, host-only `__Host-veriloom_session` cookie. The backend never puts a session token or Sarvam key in JSON. |
| Mutating requests | Browser sends `credentials: "include"`, an `X-CSRF-Token` obtained from `/auth/me`, and `Content-Type: application/json` except multipart uploads. Backend checks `Origin` and CSRF; login and registration check `Origin`. |
| Organisations | Internal `org_id` is a UUID. Every org-scoped operation derives membership from the session and path, never from an `organisation_id` supplied in draft text or AI output. The fixture slug `nook-kettle` maps to one demo org during seeding. |
| Roles | `admin`, `creator`, `reviewer`; a user can hold roles in multiple organisations. A manager/team lead is a reviewer if granted that role. Admin is not automatically a reviewer. |
| Persistence | Render PostgreSQL stores identity, membership, sources, passages, drafts, immutable submitted revisions, reviews, findings, comments, questions, decisions, and jobs. Pending source versions may already have vectors stored; only **approved, currently effective guidance passages** are eligible for pgvector and BM25 grounding searches. |
| Original files | FastAPI receives small `.txt`/`.md` uploads, writes the original bytes to private Cloudflare R2, and stores only object keys and metadata in PostgreSQL. Browser never receives R2 credentials. PDF/image extraction is outside the initial contract. |
| Long work | API commits a durable PostgreSQL job and returns `202 Accepted`; a separate Render worker claims jobs with `FOR UPDATE SKIP LOCKED`. Frontend polls the source version, review, or question resource. No request relies on in-process FastAPI `BackgroundTasks`. |
| AI boundary | Local `intfloat/multilingual-e5-small` creates embeddings. Python BM25 + pgvector ranks approved passages; RRF fuses them. A local reranker is optional after measurement. Only the backend/worker calls Sarvam AI. Selected draft text and source passages sent to Sarvam leave our storage boundary. |
| Approval | AI may flag, explain, and suggest. Only an assigned Reviewer can request changes or approve. Suggested text changes only the Creator's editable working copy after the Creator acts. |

**Why the worker exists:** ingestion, model loading, and provider calls can outlast an HTTP request. A PostgreSQL job table avoids a second queue product for the small hackathon workload. Run one worker process initially; add more only after observing queue delay. Render documents background workers for long tasks. [Render background workers](https://render.com/docs/background-workers).

## 2. Transport rules shared by every client

- UUIDs are lowercase canonical UUID strings; timestamps are UTC ISO 8601 with `Z`; calendar dates use `YYYY-MM-DD`.
- Single-resource responses return the resource directly. List responses return `{ "items": [...], "next_cursor": string|null }`; default page size 20, maximum 100. `next_cursor` is opaque.
- Request JSON uses `snake_case`, UTF-8, and `Content-Type: application/json`. Unknown request fields are rejected with `422`. Optional fields are explicitly nullable; omission is distinct from `null` on PATCH.
- API responses include `X-Request-ID` and `Cache-Control: private, no-store`. The frontend records the request ID when showing a support/error message. Never log passwords, session cookies, full confidential drafts, R2 credentials, or Sarvam prompts/responses by default.
- The client supplies `Idempotency-Key` (a new UUID) on submission, follow-up question, review retry, and decision POSTs. For a given user, endpoint, and key, the same body returns the original result for 24 hours; a different body returns `409 idempotency_key_reused`.
- Concurrency is explicit: editable draft PATCH requires `expected_working_copy_version`; decisions require `expected_review_version` and `expected_revision_id`. A mismatch returns `409 stale_version` with the current version. The frontend refetches before retrying.
- The MVP accepts text drafts up to 20,000 Unicode code points, questions up to 2,000, comments up to 4,000, and source files up to 2 MiB. File type is checked from extension and content; unsupported input returns `415`, oversized input `413`. These are chosen MVP limits, not provider limits.
- A `429` includes `Retry-After`. Transient `502/503/504` responses may be retried with the **same** idempotency key. The frontend must not retry a non-idempotent POST with a new key without checking its resource first.

### Error format

```json
{
  "error": {
    "code": "stale_version",
    "message": "This review changed. Refresh before deciding.",
    "details": { "current_review_version": 4 },
    "request_id": "01JABC..."
  }
}
```

| HTTP | When | Frontend behavior |
|---|---|---|
| `400` / `422` | Bad value, missing required field, or invalid transition payload | Show field-level validation; do not retry automatically. |
| `401` | No valid session | Send user to sign-in; preserve unsent working text locally only if explicitly designed. |
| `403` | Authenticated but role lacks permission | Show access denied. |
| `404` | Resource absent **or belongs to another organisation** | Show not found; do not reveal cross-org existence. |
| `409` | Stale working copy/review, invalid state, or reused idempotency key | Refetch the resource and ask the user to review the current state. |
| `413` / `415` | Upload too large / unsupported format | Show the accepted format and limit. |
| `429` | Rate limit | Honor `Retry-After`. |
| `502` / `503` / `504` | Dependency unavailable or API temporarily unhealthy | Show retry; preserve the request ID. Async job failures appear on their resources, not as an old HTTP response. |

## 3. Browser session and team setup

**Browser topology:** the Vercel frontend sends `/api/v1/...` to its own origin. The rewrite forwards the call to Render. This supports a host-only first-party cookie; no browser request calls Render, PostgreSQL, R2, or Sarvam directly. Set `Cache-Control: private, no-store` on all authenticated API responses and test cookie forwarding on the deployed Vercel/Render pair. Local HTTP development uses a separate non-`Secure` cookie name and the Vite proxy; never deploy that cookie setting. [Vercel external rewrites](https://vercel.com/docs/routing/rewrites).

| Method and path | Who | Request | Success |
|---|---|---|---|
| `POST /auth/register` | Public | `{email, password, display_name}` | `201 {user_id,email,display_name}`; creates identity only. |
| `POST /auth/login` | Public | `{email,password}` | `200 {user_id, memberships}`; sets session cookie. |
| `GET /auth/me` | Signed in | — | `200 {user_id,email,display_name,memberships,csrf_token}`. |
| `POST /auth/logout` | Signed in | `{}` | `204`; revokes session and clears cookie. |
| `GET /organisations` | Signed in | — | `200 {items:[{org_id,name,roles,corpus_version}],next_cursor}`. |
| `POST /organisations` | Signed in | `{name,time_zone:"UTC"}`; `time_zone` is an IANA zone and defaults to `UTC` | `201 {org_id,name,time_zone,roles:["admin"],corpus_version}`; caller becomes first admin. |
| `GET /organisations/{org_id}/members` | Admin | Pagination | `200` member list. |
| `GET /organisations/{org_id}/reviewers` | Creator or Admin | Cursor, limit | `200 {items:[{user_id,display_name}],next_cursor}` of active Reviewers in the org, excluding the current user; lets a Creator choose `reviewer_user_id` without exposing the full member list. |
| `POST /organisations/{org_id}/invitations` | Admin | `{email:"creator@example.com",role:"creator",expires_in_hours:24}` or role `reviewer` | `201 {invitation_token,expires_at}`; token shown once, shared manually with that email's owner. No email delivery integration in MVP. |
| `POST /invitations/accept` | Signed in | `{invitation_token}` | `200 {org_id,roles}`; token is single-use and the signed-in user's normalized email must match the invited email. |
| `PATCH /organisations/{org_id}/members/{user_id}` | Admin | `{roles:[...]}` | `200` updated membership; cannot remove the last admin. |

Passwords are hashed with a current password hash such as Argon2id; store only the hash. Session identifiers are random and stored hashed server-side with expiry and revocation. Rate-limit login and registration. No public endpoint can assign `admin` or `reviewer` without the org creation/invitation rules. Password reset and email verification are later features; seed separate demo accounts through a documented setup command during the build window, with no shared production password.

### Permission matrix

| Operation | Admin | Creator | Assigned Reviewer |
|---|:---:|:---:|:---:|
| Manage sources, versions, roles, invitations | Yes | No | No |
| View approved source evidence | Yes | Yes, for accessible review | Yes, for assigned review |
| Create/edit/submit own draft | No | Yes | No |
| View draft/revision | No, unless separately assigned Reviewer | Own drafts | Assigned drafts |
| Ask reviewer follow-up and decide | No | No | Yes |
| Add comment | No | On own draft | On assigned draft |

**No self-approval:** if the same user has both creator and reviewer roles, a review assigned to that user cannot approve their own submitted revision. An admin who also needs to review must be explicitly granted `reviewer` and assigned.

## 4. Source ingestion and evidence contracts

Organisation create/list responses expose the current `corpus_version` for source approval/archive optimistic concurrency. Refresh the organisation list after source mutations or a `409`; never guess or increment the expected version in the browser.

One `source` is a logical document; a `source_version` is an immutable uploaded snapshot. An admin upload creates a **pending** version, never searchable until ingestion succeeds and an admin approves it. Replacing an approved source creates a new version; approval atomically archives the previous active version, activates the new one, and increments `organisation.corpus_version`. For the MVP, approval requires `effective_from <= today` in the organisation's configured time zone; future versions stay pending until their effective date. Validity uses the half-open interval `[effective_from, effective_to)`. Old passages remain readable for historical citations but are excluded from new retrieval.

Upload `category` is one of `guidelines`, `facts`, `terminology`, or `messaging`; the worker initially assigns it to the document's passages. The source library may show more specific categories from the seeded atomic records. `source_version` is a backend-assigned display string such as `1.0`; the first version is assigned when its source is created, and later versions are allocated while locking that source row. `version_id` is the UUID used for immutable joins. A provided `effective_to` must be later than `effective_from`.

| Method and path | Who | Request | Success |
|---|---|---|---|
| `POST /organisations/{org_id}/sources` | Admin | `multipart/form-data`: `file` (`.txt`/`.md`), `title`, `category`, `effective_from`, optional `effective_to`; optional `Idempotency-Key` | `202 {source_id,version_id,ingestion_status:"queued"}`. |
| `POST /organisations/{org_id}/sources/{source_id}/versions` | Admin | Same multipart fields | `202` new pending version. |
| `GET /organisations/{org_id}/sources` | Admin | `status`, `cursor`, `limit` query parameters | `200` source summaries with active version and ingestion state. |
| `GET /organisations/{org_id}/sources/{source_id}/versions/{version_id}` | Admin | — | `200` version metadata, extraction errors, passage count, R2 original download availability. |
| `GET /organisations/{org_id}/sources/{source_id}/versions/{version_id}/original` | Admin | — | `200` streamed original from private R2 with `Content-Disposition: attachment`; never expose the bucket key. |
| `POST /organisations/{org_id}/sources/{source_id}/versions/{version_id}/approve` | Admin | `{expected_corpus_version}` | `200 {source_id,approved_version_id,corpus_version}`; only if ingestion is `ready` and effective dates are valid. |
| `POST /organisations/{org_id}/sources/{source_id}/archive` | Admin | `{expected_corpus_version}` | `200`; no active version remains; historical citations persist. |
| `GET /organisations/{org_id}/evidence/{passage_id}` | Admin or permitted review participant | — | `200` exact passage and immutable source version; may show `current_status:"archived"` or `"expired"`. A Creator/Reviewer may read a passage only if it is cited by a review or chat turn they may access; the Admin may read an approved or historical passage in their organisation. Pending versions are inspected through Admin source-version/original routes, not presented as approved evidence. |

**Ingestion path:** API checks role and file, computes SHA-256, uploads to `org/{org_id}/sources/{source_id}/versions/{version_id}/original` in private R2, commits metadata and a job, then responds. The worker extracts UTF-8 text, rejects empty content, splits within the E5 512-token input limit including the `passage:` prefix, generates 384-d vectors, and stores passages with `org_id`, `source_id`, `version_id`, `source_ref`, category, and offsets. Approval state and effective dates belong to the immutable source version and are joined during retrieval. Uploaded documents receive generated passage references such as `DOC-{source_id}#P0001`; seeded atomic records retain their canonical `record_id`. If R2 succeeds but the DB transaction fails, remove the orphan object or let a reconciliation task identify it; never mark a version ready before passages commit. [E5 model card](https://huggingface.co/intfloat/multilingual-e5-small), [Cloudflare R2 uploads](https://developers.cloudflare.com/r2/objects/upload-objects/).

**Evidence object (shared by findings and chat):**

```json
{
  "passage_id": "uuid",
  "source_ref": "NK-FACTS-001#FACT-06",
  "source_id": "uuid",
  "source_version": "1.0",
  "title": "Cardamom Chai price",
  "excerpt": "The approved passage text used for this decision.",
  "effective_from": "2026-11-03",
  "current_status": "approved"
}
```

`source_ref` is the data pack's document-qualified record identifier, **not** a database primary key. In the seeded demo, map its `organisation_id` slug to the demo org UUID, preserve `record_id` as `source_ref`, and ingest only `data/knowledge/records.jsonl` where `ingestible=true`, `status=approved`, and the effective dates apply. Do not also ingest the human-readable mirrors: that would duplicate rules. `data/evaluation/*`, isolation fixtures, and public references are never normal grounding inputs. [Data pack README](../../data/README.md).

`current_status` is a **read-time display value** with enum `approved | archived | expired`, evaluated for today's date in the organisation time zone. It is `archived` if the source/version was manually archived or replaced; otherwise `expired` if an approved version is outside its effective interval; otherwise `approved` when the version is currently eligible. An old citation retains the original approved source/version and excerpt even when this display value changes. Neither archived nor expired passages are used for a new review or follow-up.

## 5. Drafts, revisions, and review contracts

The Creator edits a **working copy**. Saving it does not call Sarvam. Each submission freezes its content into an immutable numbered `revision` and starts an AI review; review findings and chat belong to that exact revision. `drafts.current_revision_id` and `drafts.state` summarise the latest submission. An approved revision is never edited; a later change starts a new submission.

| Method and path | Who | Request | Success |
|---|---|---|---|
| `POST /organisations/{org_id}/drafts` | Creator | `{title,channel,content_format:"text",content}` | `201 {draft_id,state:"editing",working_copy_version:1}`. |
| `GET /organisations/{org_id}/drafts` | Creator / Reviewer | `view=mine` or `view=assigned`, cursor, status | `200` scoped list; `assigned` means current user is assigned reviewer. |
| `GET /organisations/{org_id}/drafts/{draft_id}` | Owner Creator / assigned Reviewer | — | `200` current state, latest review summary, assignment, working copy only for owner. |
| `PATCH /organisations/{org_id}/drafts/{draft_id}` | Owner Creator | `{expected_working_copy_version,title?,channel?,content?}` | `200` updated working copy and incremented version; only in `editing` or `changes_requested`. |
| `POST /organisations/{org_id}/drafts/{draft_id}/submissions` | Owner Creator | `{expected_working_copy_version,reviewer_user_id}` + `Idempotency-Key` | `202 {revision_id,revision_number,review_id,draft_state:"processing",review_state:"queued"}`; assigned user must have reviewer role in org and cannot be the Creator. |
| `GET /organisations/{org_id}/drafts/{draft_id}/revisions` | Owner Creator / assigned Reviewer | Cursor | `200` revision timeline, each with review state and decision. |
| `GET /organisations/{org_id}/drafts/{draft_id}/revisions/{revision_number}` | Owner Creator / assigned Reviewer | — | `200` immutable content snapshot, submitted timestamp, review ID. |
| `GET /organisations/{org_id}/reviews/{review_id}` | Owner Creator / assigned Reviewer | — | `200` review state, revision ID, findings, citations, coverage, review version, timestamps, and failure summary if applicable. |
| `POST /organisations/{org_id}/reviews/{review_id}/retry` | Owner Creator / assigned Reviewer | `{}` + `Idempotency-Key` | `202`; only for a failed review, same revision, limited retry count. |

**Submission example:**

```http
POST /api/v1/organisations/ORG_UUID/drafts/DRAFT_UUID/submissions
Content-Type: application/json
X-CSRF-Token: SESSION_CSRF
Idempotency-Key: 3b41ba7c-ef06-4e32-b470-145c0b81a693

{"expected_working_copy_version":2,"reviewer_user_id":"REVIEWER_UUID"}
```

```json
{
  "revision_id": "REVISION_UUID",
  "revision_number": 1,
  "review_id": "REVIEW_UUID",
  "draft_state": "processing",
  "review_state": "queued",
  "status_url": "/api/v1/organisations/ORG_UUID/reviews/REVIEW_UUID"
}
```

`GET /reviews/{review_id}` returns `state: "queued" | "processing" | "ready_for_review" | "failed" | "changes_requested" | "approved"`. A review may be `ready_for_review` with `coverage: "insufficient"` and no cited findings if the approved corpus cannot support a check; the UI must display **Needs human review**. Provider or extraction failure is `failed`, not an empty pass. The API never treats an AI result as approval.

**Review response example:**

```json
{
  "review_id": "REVIEW_UUID",
  "draft_id": "DRAFT_UUID",
  "revision_id": "REVISION_UUID",
  "revision_number": 1,
  "assigned_reviewer_user_id": "REVIEWER_UUID",
  "state": "ready_for_review",
  "coverage": "sufficient",
  "coverage_reason": null,
  "review_version": 1,
  "reviewed_against_corpus_version": 3,
  "findings": [],
  "decision": null,
  "failure": null,
  "created_at": "2026-11-04T08:00:00Z",
  "completed_at": "2026-11-04T08:00:09Z"
}
```

`coverage` is `sufficient | partial | insufficient`; `coverage_reason` is null for sufficient coverage and a nonempty explanation for partial or insufficient coverage. A nonempty `findings` array uses the Finding shape below. `failure` is null except when `state="failed"`, where it contains a safe `{code,message,retryable}` object. The review stores the source corpus version used at job start; a source approval during processing does not silently change that review's evidence snapshot.

`decision` is null before a human decision. Afterwards it contains `{decision_id,action,comment,override_reason,decided_by_user_id,decided_at}` for this review's latest human decision, where `action` is `request_changes` or `approve`. The owner Creator and assigned Reviewer can read it through the same participant-authorised review detail route. Show the change-request comment and any recorded approval override with actor/time so the Creator can act on the reviewer's actual feedback; they remain attached to the immutable revision. This field does not merge decisions into the separate comment conversation.

**Finding shape:** for the illustrative immutable revision text `Try our best Jaggery Cold Coffee in India today!`, the anchor points to `best Jaggery Cold Coffee in India` and the proposed replacement yields `Try our Jaggery Cold Coffee today!`.

```json
{
  "finding_id": "uuid",
  "category": "promotional_claim",
  "severity": "high",
  "evidence_status": "supported",
  "anchor": {"start_codepoint": 8, "end_codepoint": 41, "quote": "best Jaggery Cold Coffee in India"},
  "explanation": "The approved voice rule disallows an unsupported 'best' claim.",
  "suggested_edit": "Jaggery Cold Coffee",
  "citations": [{"passage_id":"uuid","source_ref":"NK-BRAND-001#VOICE-03","source_id":"uuid","source_version":"1.0","title":"Grounded, not boastful","excerpt":"Do not describe the café or its menu as “the best,” “number one,” “award-winning,” “world-famous,” or “unmatched” unless a corresponding approved fact is supplied.","effective_from":"2026-11-03","current_status":"approved"}]
}
```

`category` is `tone | terminology | factual_claim | promotional_claim | accessibility | visual_asset | other`; only categories supported by the submitted format and approved evidence should be emitted. `severity` is `low | medium | high`. For an unverified claim, use `evidence_status:"insufficient"`, `citations:[]`, and a human-review explanation; never invent a `source_ref`. `anchor` may be null for a whole-draft issue. Offsets count Unicode code points in the **NFC-normalised immutable revision text**, end exclusive; React must convert code points when highlighting because JavaScript string offsets are UTF-16 code units. The server verifies that `quote` equals the slice before storing a finding. Suggested edits are proposals, not automatic replacements.

For a one-click suggestion, React may replace the anchored text **only when** the Creator's working copy still matches the submitted revision at that span. It then sends the new content through `PATCH /drafts/{draft_id}` with `expected_working_copy_version`. If the copy has changed, show the suggestion for manual editing. The Reviewer never edits the Creator's copy.

## 6. Reviewer follow-up, comments, and decisions

| Method and path | Who | Request | Success |
|---|---|---|---|
| `POST /organisations/{org_id}/reviews/{review_id}/questions` | Assigned Reviewer | `{revision_id,finding_id?,question}` + `Idempotency-Key` | `202 {turn_id,state:"queued",status_url}`. Current review must be `ready_for_review`; old revisions are read-only. |
| `GET /organisations/{org_id}/reviews/{review_id}/questions` | Owner Creator / assigned Reviewer | Cursor | `200` chat turns in creation order. Creator may read reviewer Q&A but cannot submit questions in MVP. |
| `GET /organisations/{org_id}/reviews/{review_id}/questions/{turn_id}` | Owner Creator / assigned Reviewer | — | `200` turn with one of `queued`, `processing`, `completed`, or `failed`; route, answer/status, citations, and timestamps. |
| `POST /organisations/{org_id}/reviews/{review_id}/comments` | Owner Creator / assigned Reviewer | `{revision_id,body}` | `201` comment with actor and timestamp; append-only. |
| `GET /organisations/{org_id}/reviews/{review_id}/comments` | Owner Creator / assigned Reviewer | Cursor, limit | `200 {items:[{comment_id,revision_id,body,actor_user_id,created_at}],next_cursor}` in creation order for this review's immutable revision. A server-side membership and review-participant check applies to every page. |
| `POST /organisations/{org_id}/reviews/{review_id}/decisions` | Assigned Reviewer | `{expected_revision_id,expected_review_version,action:"request_changes",comment?,override_reason?}` or action `approve`; `Idempotency-Key` | `201` decision; atomically changes review and draft state. |

**Question result example:**

```json
{
  "turn_id": "uuid",
  "revision_id": "uuid",
  "finding_id": "uuid",
  "question": "Why was that claim flagged?",
  "state": "completed",
  "route": "rewrite_retrieve",
  "rewritten_query": "Which approved Nook & Kettle rule governs 'best Jaggery Cold Coffee in India' in this draft?",
  "answer_status": "answered",
  "answer": "The approved voice rule prohibits 'best' claims without a supporting approved fact.",
  "citations": [{"passage_id":"uuid","source_ref":"NK-BRAND-001#VOICE-03","source_id":"uuid","source_version":"1.0","title":"Grounded, not boastful","excerpt":"Do not describe the café or its menu as “the best,” “number one,” “award-winning,” “world-famous,” or “unmatched” unless a corresponding approved fact is supplied.","effective_from":"2026-11-03","current_status":"approved"}],
  "created_at": "2026-11-04T09:00:00Z"
}
```

`route` is `reuse_evidence | rewrite_retrieve | fresh_retrieve | clarify | abstain`. `answer_status` is `answered | clarification_needed | insufficient_evidence`. For the latter two, `citations` may be empty and the answer must say what is missing. Store the raw question, rewritten query if used, route, retrieved passage IDs, model version, answer, and citation snapshots for traceability; do not expose hidden model reasoning. Source approval can change later, so historical turns retain the source version used at answer time.

If a previously cited passage is now archived or outside its effective window, the follow-up router must **not** reuse it as current authority. It performs fresh retrieval against the current approved corpus and explains the change. The original review and old answer still display their historical citations with read-time `current_status:"archived"` or `"expired"`, as applicable.

**Decision rules:** `request_changes` requires a non-empty comment and moves the draft to `changes_requested`; Creator may then edit the working copy and resubmit. `approve` requires the review still be `ready_for_review`, the revision still be current, and the assigned reviewer to differ from the Creator. If any high-severity finding or insufficient-coverage warning remains, `approve` requires `override_reason` (minimum 15 characters) and records it in the decision. The server increments `review_version` atomically and returns `409 stale_version` on a concurrent decision. No AI call occurs during the decision itself.

**Decision response example:**

```json
{
  "decision_id": "DECISION_UUID",
  "review_id": "REVIEW_UUID",
  "revision_id": "REVISION_UUID",
  "action": "request_changes",
  "comment": "Please remove the unsupported ranking claim.",
  "override_reason": null,
  "decided_by_user_id": "REVIEWER_UUID",
  "decided_at": "2026-11-04T09:15:00Z",
  "review_version": 2,
  "draft_state": "changes_requested"
}
```

Comments are accepted while a review is `ready_for_review` or `changes_requested`; an approved review is read-only. A retry is allowed only when the failed review is still the draft's current revision.

## 7. State machines and frontend behavior

```text
Draft: editing --submit--> processing --review complete--> ready_for_review
                                      \--job failure--> review_failed --retry--> processing
ready_for_review --request_changes--> changes_requested --Creator edit+submit--> processing
ready_for_review --approve--> approved

Source version: queued --> processing --> ready --Admin approve--> approved
                               \--> failed
Approved source version --new version approved--> archived (historical citations remain)

Question: queued --> processing --> completed | failed
```

| Screen/action in React | API calls | UI handling |
|---|---|---|
| Login and organisation switch | `/auth/login`, `/auth/me`, `/organisations` | Keep cookie automatic; selected org is a UI choice, server rechecks membership on every request. |
| Admin source library | `POST /sources`, `GET /sources`, `GET /sources/{id}/versions/{id}`, approve/archive | Poll ingestion every 2 seconds while pending, then show version/approval state and any error. Never label a pending file “approved.” |
| Creator editor | `POST /drafts`, `PATCH /drafts/{id}`, `POST /submissions`, `GET /reviews/{id}` | Keep an editable working copy; show `processing`, `ready`, or `failed`; poll every 2 seconds with backoff up to 10 seconds. A 409 refreshes the working-copy version. |
| Reviewer inbox and detail | `GET /drafts?view=assigned`, `GET /reviews/{id}`, `GET /revisions/{n}` | Show immutable submitted text, finding anchors, source IDs/versions, unsupported warnings, and review history. |
| Reviewer chat | `POST /questions`, `GET /questions/{turn_id}` | Show queued/processing state; render only completed answer and citations. Display clarification/abstention explicitly. |
| Change request/approval | `POST /comments`, `POST /decisions` | Require comment or override reason as applicable. On 409, refresh before deciding. Creator sees feedback in app; no email dependency. |

The frontend must treat all user, source, model, and Markdown text as untrusted. Render findings and chat as plain text or with a sanitised Markdown renderer; never inject raw HTML. Links to evidence call the authorised evidence endpoint, not a public R2 URL. Polling stops when the component unmounts or a terminal state appears.

## 8. Internal backend, retrieval, and provider interfaces

These are **Python service contracts**, not public HTTP routes:

```python
ingest_source(version_id: UUID) -> IngestionResult
retrieve_evidence(org_id: UUID, as_of: date, query: str, category: str | None,
                  corpus_version: int, top_k: int = 8) -> list[EvidenceCandidate]
review_revision(review_id: UUID) -> ReviewResult
answer_question(turn_id: UUID) -> QuestionResult
```

1. **Ingest:** load the private R2 object, extract supported text, normalise to NFC, chunk with stable `source_ref` and passage IDs, embed each passage as `passage: <text>`, and store `vector(384)`. Ingestion never reads evaluation labels as knowledge.
2. **Select candidates:** filter by session-derived `org_id`, `approved` status, version effective on the review date, and source category when relevant. Search pgvector using E5 `query: <question>`. Search the **same filtered corpus** with Python `rank-bm25`. For a small demo corpus, cache BM25 per `(org_id, corpus_version, as_of_date)` and rebuild when approval or the effective date changes. Capture the selected passage IDs, exact text, source versions, and corpus version before the external model call so an approval during generation cannot change that review's evidence snapshot.
3. **Fuse:** apply Reciprocal Rank Fusion to the dense and lexical ranked lists. Rerank only a bounded fused set if the local cross-encoder is enabled and measured benefit outweighs latency/memory. If unavailable, use RRF results and record `reranker_used:false`.
4. **Ground:** send only selected passages plus the necessary draft/question context to Sarvam. The worker uses an environment-selected chat model and Sarvam V2 chat completion with a strict JSON schema where the chosen model supports it. Parse and validate the returned JSON again server-side. No request contains R2 credentials, passwords, another organisation's material, evaluation answer keys, or complete unrelated chat history. [Sarvam Chat Completion V2](https://docs.sarvam.ai/api-reference/chat/chat-completions-v2).
5. **Verify:** map each model citation to an actually retrieved `passage_id` and approved source version, verify quote offsets, and reject fabricated IDs. If verification fails, retry a bounded number of times or return `coverage:"insufficient"` for human review. A provider timeout/error marks the job failed with a safe message; retries use the same revision and job identity, never create duplicate findings.
6. **Follow-up routing:** load the active revision, selected finding and its proposed edit, stored review coverage diagnostic, revalidated current citations, and a bounded recent chat window. Reuse cited evidence only if it still answers the direct question; rewrite ambiguous references into a standalone query; otherwise run fresh scoped hybrid retrieval. Ask for clarification or abstain when the question cannot be resolved or approved evidence is inadequate. A narrow question about why the review's coverage was marked insufficient or partial may return its stored reason as an `insufficient_evidence` diagnostic with no source citation; it is not a brand-policy answer. Suggested-edit refinements remain grounded proposals in the answer text and never change the submitted revision or finding. Save the route and both raw/rewritten query where applicable.

**Internal job table:** `job_id`, `kind` (`ingest_source|review_revision|answer_question`), `resource_id`, `org_id`, `state` (`queued|running|succeeded|failed`), `attempts`, `run_after`, `lease_until`, `last_error_code`, `created_at`, `updated_at`. Claim via `SELECT ... FOR UPDATE SKIP LOCKED`; workers renew leases, use bounded retries/backoff for transient errors, and make each handler idempotent. Source and review resources expose job progress without exposing internal stack traces. Use a single worker instance first to control local model memory.

## 9. Minimum PostgreSQL record and integrity contract

| Table | Key fields / required constraint |
|---|---|
| `users`, `sessions` | Unique normalised email; password hash; hashed session token, expiry, revocation. |
| `organisations`, `memberships`, `invitations` | `org_id`, IANA `time_zone`, `corpus_version`; unique `(org_id,user_id)` membership; role set; hashed single-use invitation token. |
| `sources`, `source_versions` | Org FK; logical source ID; immutable version ID, R2 key, SHA-256, effective dates, ingestion/approval state; at most one active approved version per logical source. |
| `source_passages` | Composite org/source/version FK; unique `(version_id,source_ref,chunk_index)`; text, offsets, `vector(384)`; never store draft chunks here. |
| `drafts` | Org and creator FKs; working copy and integer working-copy version; current revision ID; state. The assigned Reviewer belongs to the current revision's `reviews` row. |
| `draft_revisions` | Unique `(draft_id,revision_number)`; immutable NFC content snapshot, channel, submission time, creator. |
| `reviews`, `review_evidence`, `findings`, `finding_citations` | One review per submitted revision; state, version, coverage; exact prompt evidence snapshot; finding anchor/suggestion; citation FK to immutable passage and source version. |
| `chat_turns`, `chat_evidence`, `chat_citations`, `comments`, `decisions` | Review/actor FKs; turns and comments belong to the review's immutable revision; exact prompt evidence snapshot; append-only conversation and human decisions; at most one terminal decision per review. |
| `jobs`, `idempotency_records` | Durable async work, lease/retry data; unique `(user_id,endpoint,key)` and request-body hash, including decision replay keys. |

Use transactions for source activation, draft submission + job creation, and reviewer decisions. Database FKs and org-scoped joins enforce the same boundaries as API permissions. Avoid `ON DELETE CASCADE` for reviewed content or cited source versions; use archive/revoke states to preserve history. Secrets live in Render environment variables; R2 objects stay private. `GET /healthz` reports API process liveness without secrets; `GET /readyz` checks API database connectivity for operator use, not browser dashboards. Monitor worker health separately from its service status and queued-job age; the MVP schema does not include a worker-heartbeat table.

### Deployment configuration contract

| Setting | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | API + worker | Render PostgreSQL connection; configure a modest connection pool for both processes. |
| `SESSION_SECRET` | API | Session/CSRF signing material; rotate with an explicit session invalidation plan. |
| `APP_ORIGIN` | API | Exact Vercel production origin for unsafe-request `Origin` checks; local origin is separate. |
| `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | API + worker | Private original-file writes/reads; never prefixed with `VITE_` or sent to the browser. |
| `SARVAM_API_KEY`, `SARVAM_MODEL` | Worker | Provider authentication and selected chat model; chosen model must pass a structured-output smoke test. |
| `EMBEDDING_MODEL` | Worker | Pin `intfloat/multilingual-e5-small` and its revision in the deployment lockfile/configuration. |
| `RERANKER_ENABLED`, `RERANKER_MODEL` | Worker | Optional reranking flag and pinned model revision. |
| `VITE_API_BASE` | Frontend | `/api/v1`; no secret value. |

Render web service runs the FastAPI app; a separate Render background worker runs the PostgreSQL job poller; Vercel hosts the React build. Add the Vercel rewrite for `/api/:path*` before browser integration and test login, upload, polling, and logout through the deployed origin. Keep API and worker on the same schema migration version. Health endpoints expose no organisation data or provider credentials.

## 10. Acceptance scenarios before frontend integration

1. Admin registers, creates an org, invites one Creator and one Reviewer, uploads `.md`, waits for ingestion, approves it, and can open its passages. Before approval, retrieval returns none of those passages.
2. Creator creates a text draft and submits revision 1. Frontend receives `202`, polls review, then displays cited findings or an explicit insufficient-evidence state. Reloading the page preserves the result.
3. Reviewer asks a direct follow-up and a new-topic follow-up. The first reuses valid cited evidence; the second retrieves only this org's approved passages. Ambiguous questions store the rewrite. Unsupported questions clarify or abstain.
4. Reviewer requests changes with a comment; Creator edits and submits revision 2; Reviewer approves revision 2. Revision 1 findings and chat remain attached to revision 1. Approval of revision 1 now returns `409`.
5. Another organisation's user cannot read the draft, passages, chat, or R2 original, including by guessing UUIDs. Browser requests return `404` without existence leakage. A Creator cannot approve; a Reviewer cannot edit the working copy.
6. A duplicate submission with the same idempotency key creates one revision/job. A failed Sarvam call yields `failed` and a safe retry path; it never appears as a clean review.
7. Run the supplied development/holdout and scope fixtures **offline** against actual outputs; report source recall, citation validity, abstention, false positives/misses, and latency. Never put answer keys or holdout labels into prompts or the approved corpus.

## 11. Implementation order and open implementation choices

1. Build FastAPI app skeleton, migrations, users/sessions, org membership, and one seeded demo organisation. Generate OpenAPI from Pydantic models and compare it with this contract.
2. Add R2 source upload, source versions, PostgreSQL job worker, extraction, E5 vectors, and admin approval. Verify org/status/effective-date filtering before any model call.
3. Add draft working copy, immutable submissions, async review, structured findings, evidence endpoint, and reviewer inbox.
4. Add reviewer questions, adaptive routing, comments, decisions, retry paths, and revision timeline. Connect React screens only after each endpoint has a stable response shape.

**Decisions to confirm through a deployed smoke test:** Vercel rewrite forwards the host-only session cookie as expected; the selected Sarvam model accepts the required structured-output schema; local E5/reranker memory fits Render; the worker and API share the chosen PostgreSQL plan without exhausting connections. If the reranker is too costly, keep hybrid RRF and record the fallback. These checks may alter deployment settings, not the public JSON shapes.

## Reference boundaries

- This file owns public routes, roles, states, response shapes, and error behavior. [Database Design and ERD.md](Database%20Design%20and%20ERD.md) implements persistence and migrations; [Frontend Architecture Specification.md](Frontend%20Architecture%20Specification.md) implements the browser use of these routes.
- [RAG Architecture Specification.md](RAG%20Architecture%20Specification.md) owns ingestion, retrieval, reranking, and evidence selection. [Prompt Engineering Specification.md](Prompt%20Engineering%20Specification.md) owns private model-facing templates and output validation. Neither may broaden access or alter public JSON without an explicit contract update.
- Project intent and roles: [PRESENTATION.md](../PRESENTATION.md), [problem statement](../Problem_statement.md).
- Data fixture contract and exclusions: [data README](../../data/README.md), [knowledge schema](../../data/schemas/knowledge.schema.json), [draft schema](../../data/schemas/draft.schema.json).
- External behavior consulted: [Sarvam V2 chat](https://docs.sarvam.ai/api-reference/chat/chat-completions-v2), [Vercel rewrites](https://vercel.com/docs/routing/rewrites), [Render workers](https://render.com/docs/background-workers), [Cloudflare R2 uploads](https://developers.cloudflare.com/r2/objects/upload-objects/), [E5 model card](https://huggingface.co/intfloat/multilingual-e5-small), [pgvector](https://github.com/pgvector/pgvector).
