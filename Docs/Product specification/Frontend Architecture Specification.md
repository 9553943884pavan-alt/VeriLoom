# VeriLoom Frontend Architecture Specification

**Status:** core Round 2 React MVP implemented and verified locally on 2026-11-05. The sections below retain the destination architecture; current implementation limits are stated here.
**Audience:** the solo builder implementing the React interface.  
**Scope:** one organisation at a time, text guidance and text drafts, grounded review, reviewer follow-up, revision request, resubmission, and human approval. The API contract defines the server behavior; this file defines how the browser uses it.

## 1. Mental model and decisions

The frontend has three jobs: let the right person perform an action, show the current server state, and preserve the distinction between a Creator's editable **working copy** and an immutable **submitted revision**. The server is the authority for permissions, review states, citations, and decisions. Route guards and hidden buttons improve the experience but never replace backend authorization.

| Concern | Round 2 choice | Reason |
|---|---|---|
| App | React + TypeScript + Vite | Type the API boundary and build a small client-side application. |
| Styling | Tailwind CSS plus a small set of reusable components | Keep layouts consistent while building the solo MVP. |
| Navigation | React Router in declarative SPA mode | Nested organisation routes and explicit role gates. |
| Server state | TanStack Query | Cache by organisation and resource ID, invalidate after mutations, and poll pending jobs. |
| Form state | React Hook Form + Zod schemas | Field-level errors and shared limits at the UI boundary; the API still validates. |
| Local UI state | React component state or context | Modal visibility, current finding, selected organisation, and unsaved form values; no global store is needed for the MVP. |

These are implementation choices, not additions to the [technology commitments in PRESENTATION.md](../PRESENTATION.md). Keep versions pinned in the eventual package lockfile and verify current setup instructions when the app is initialized. [React Router routing](https://reactrouter.com/start/declarative/routing), [TanStack Query polling](https://tanstack.com/query/latest/docs/framework/react/guides/polling), [Vite development proxy](https://vite.dev/config/server-options).

## 2. Route map and permission gates

The current MVP opens source-version detail inline within `/admin/sources`; the dedicated version-detail URL in the destination map remains planned. Member-role and source-replacement controls are implemented and pass static/build checks, but were not exercised during the recorded live browser journey. Deployed Vercel rewrites and production HTTPS cookies remain deployment verification work.

Use `orgId` from the URL, but confirm the signed-in user's membership from `GET /auth/me` before rendering an organisation route. On a cold load, show a neutral loading screen until that request resolves. An unauthenticated user goes to `/login`; a signed-in user without access sees an access-denied page. Backend `403` or cross-organisation `404` still governs every request.

```text
/
├─ /login                                      public
├─ /register                                   public
├─ /organisations                              signed in; choose or create an organisation
├─ /invitations/accept                         signed in; paste invitation token
└─ /org/:orgId                                 membership required; shared app shell
   ├─ /overview                                role-aware entry page
   ├─ /admin/sources                           admin
   ├─ /admin/sources/:sourceId/versions/:versionId  admin
   ├─ /admin/members                           admin
   ├─ /creator/drafts                          creator; own drafts
   ├─ /creator/drafts/new                      creator
   ├─ /creator/drafts/:draftId                 creator; editable working copy and feedback
   ├─ /reviewer/inbox                          reviewer; assigned drafts
   └─ /reviewer/reviews/:reviewId              reviewer; immutable revision and decision
```

`RequireSession` reads `/auth/me`; `RequireMembership` checks that `orgId` is among `memberships`; `RequireRole` checks `admin`, `creator`, or `reviewer` for the current org. A reviewer route still fetches the review because assignment is more specific than role. A user with multiple roles sees the corresponding navigation sections, but an admin does not gain draft access solely from being an admin. An assigned reviewer cannot approve their own submitted revision. The backend enforces both rules. `GET /organisations` is the source for the switcher; selecting an org changes the URL and clears org-specific view state.

## 3. Component hierarchy

```text
App
├─ QueryClientProvider / Router
├─ PublicLayout
│  ├─ LoginForm / RegisterForm
│  └─ InvitationAcceptForm
└─ RequireSession → RequireMembership → AppShell
   ├─ Header / OrganisationSwitcher / RoleNavigation / SignOutButton
   ├─ AdminSourceLibrary
   │  ├─ SourceUploadForm / SourceList / SourceVersionStatus
   │  └─ SourceVersionDetail / ApproveArchiveActions / MemberInvitationForm
   ├─ CreatorDraftArea
   │  ├─ DraftList / DraftEditor / ReviewerPicker / SubmitAction
   │  └─ RevisionTimeline / ReviewStatus / FindingList / SuggestedEditAction
   └─ ReviewerArea
      ├─ AssignedInbox
      └─ ReviewWorkspace
         ├─ ImmutableRevisionViewer / FindingList / EvidencePanel
         ├─ FollowUpThread / QuestionForm / CommentThread
         └─ DecisionForm / RevisionTimeline
```

Use small shared primitives: `PageHeader`, `StatusBadge`, `InlineError`, `EmptyState`, `LoadingSkeleton`, `ConfirmDialog`, and `EvidenceCitation`. For a Creator or Reviewer, the `EvidencePanel` opens only a passage **cited** in an accessible review or chat turn, then loads `GET /organisations/{org_id}/evidence/{passage_id}` on demand; a retrieved-but-uncited passage is not generally readable through that route. It never links directly to private R2. Source original download uses the admin-only API route. Show the source title, version, `source_ref`, effective date, and `current_status` (`approved`, `archived`, or `expired`) beside evidence. Display archived or expired historical citations as historical, not as a currently approved rule.

The editor and review workspace should be usable without a diagram or complex rich-text editor. A textarea for text content and plain-text finding highlights are sufficient for this round. Do not display visual-asset checks as if image pixels were analyzed; the initial API accepts `content_format:"text"` only.

`MemberInvitationForm` collects the invited person's email, Creator or Reviewer role, and expiry hours. The Admin shares the returned token manually with that person. `InvitationAcceptForm` requires sign-in as the same normalized email before submitting the token; an email mismatch is shown as an access error.

## 4. State ownership and data flow

| State | Owner | Examples and rule |
|---|---|---|
| Authentication and memberships | `GET /auth/me` in TanStack Query | Keep `csrf_token` in memory; refetch after login, invitation acceptance, and role change. Registration creates an identity but does not sign in under the API contract, so take the user to login afterward. Clear query cache on logout. |
| Selected organisation | URL path, with optional non-sensitive preference | Never use client selection as authorization. Include `orgId` in every organisation-scoped query key. |
| Lists and resources | TanStack Query | Keys such as `['org', orgId, 'drafts', 'mine', filters]`, `['org', orgId, 'review', reviewId]`, `['org', orgId, 'question', turnId]`. Use cursor pagination from `{items,next_cursor}`. |
| Editable fields | Form state | Title, channel, content, question, comment, upload metadata. Submit only on explicit save/submit; no AI call while typing. |
| Temporary UI | Component state | Selected finding, modal, tab, upload progress. Do not persist confidential draft or source text in browser storage. |
| Review/revision history | Backend | The current draft and each numbered revision have separate resources; never infer a new review state from old chat or findings. |

After a mutation, invalidate only the affected org/resource keys: source upload or approval refreshes source list/version; draft save refreshes draft detail; submission refreshes draft, revision list, inbox, and new review; a completed question refreshes its thread; a decision refreshes review, draft, inbox, and timeline. Keep the returned `review_id`, `revision_id`, and `status_url` from submission. Do not construct a review ID from a draft ID.

## 5. Typed API client and browser security

Centralize requests in `src/lib/api/client.ts` rather than placing `fetch` calls in components. The client adds `/api/v1`, `credentials: 'include'`, `X-CSRF-Token` on unsafe requests after `/auth/me`, and `Idempotency-Key` where the contract requires it. JSON requests use `Content-Type: application/json`; multipart source upload lets the browser set its own boundary. Parse API errors into a typed `ApiError {status, code, message, details, requestId, retryAfter?}` using the body and `X-Request-ID`. Do not expose raw server stack traces to the page.

Define TypeScript types from the contract for `Session`, `Membership`, `SourceVersion`, `Draft`, `Revision`, `Review`, `Finding`, `Evidence`, `QuestionTurn`, `Decision`, `Paginated<T>`, and `ApiError`. Exact server field names stay `snake_case`; map to display labels in components, not by changing wire names. Represent status and action fields as literal unions. Keep optional versus `null` distinct in PATCH payloads. A generated client from FastAPI OpenAPI can replace hand-maintained types after endpoints stabilize, but review the generated diffs against [API Contracts.md](<API Contracts.md>).

The production browser calls same-origin `/api/v1/...` through the Vercel rewrite to Render. It must not call Render, Sarvam, PostgreSQL, or R2 directly. The session is an `HttpOnly`, `Secure`, `SameSite=Lax`, host-only cookie named `__Host-veriloom_session`; JavaScript never reads it. CSRF is returned by `/auth/me` and kept in memory. The backend checks `Origin` and CSRF; the UI cannot make those checks authoritative. Local HTTP uses the separate development cookie behavior specified by the API contract. Do not put provider keys in `VITE_*` variables: Vite exposes those values to browser code.

For `POST /submissions`, `POST /questions`, `POST /reviews/{review_id}/retry`, and `POST /decisions`, generate one UUID idempotency key per user action and reuse it for network retries of that same action. Disable duplicate submission while pending. If a response is lost after a browser refresh, refetch the draft/review/question state before a new action; do not blindly generate a new key and repeat the POST. `409 stale_version` requires a refetch and explicit user review, not an automatic overwrite.

## 6. Screen behavior and polling

| Screen | Core API calls | State shown to the user |
|---|---|---|
| Source library | `POST /organisations/{org_id}/sources`, `GET /sources`, `GET /sources/{source_id}/versions/{version_id}`, approve/archive | Upload `queued`, ingestion `processing`, `ready` for admin approval, `approved`, `failed`, or archived history. `ready` is never labeled approved. |
| Creator editor | `POST /drafts`, `GET/PATCH /drafts/{draft_id}`, `POST /submissions`, `GET /revisions`, `GET /reviews/{review_id}` | Saved working-copy version, current revision, processing/failed/ready review, findings, comments, change request, approval. |
| Reviewer inbox | `GET /drafts?view=assigned` | Assigned items with draft/review state and revision number; filters are server-backed. |
| Review workspace | `GET /reviews/{review_id}`, `GET /drafts/{draft_id}/revisions/{revision_number}`, `GET /questions`, `POST /questions`, `GET /comments`, `POST /comments`, `POST /decisions` | Immutable submitted text, evidence, warnings, grounded follow-up, paginated comment history, and human decision. |

Poll a pending source version, review, or question after 2 seconds. Increase the interval after repeated unchanged responses or transient failures, capped at 10 seconds. Stop when its status becomes terminal or the page unmounts; TanStack Query's query interval can be conditional on the latest data. A page reload resumes from the resource ID in the URL or saved response, not from a browser-only job counter. Show a refresh/retry control when the API or worker fails. Honor `Retry-After` for `429` and reuse the same idempotency key for safe retries of a mutating action. Do not poll completed resources indefinitely.

Review states are `queued`, `processing`, `ready_for_review`, `failed`, `changes_requested`, and `approved`. Coverage is `sufficient`, `partial`, or `insufficient`. Show the API's nonempty `coverage_reason` for partial or insufficient coverage; sufficient coverage has `coverage_reason:null`. A completed review with insufficient coverage shows **Needs human review** even if `findings` is empty. A failed review shows its safe failure summary and the retry action where permitted; it is never displayed as a clean pass. A follow-up turn's `answered`, `clarification_needed`, or `insufficient_evidence` result must be visibly distinct. Explain that new questions are closed on approved and changes-requested revisions while retaining their earlier Q&A history. The Creator may read reviewer Q&A but cannot ask a follow-up in the MVP.

Make the Reviewer conversation a prominent part of the review workspace. Show the immutable draft and anchored findings alongside a persistent, scrollable question-and-answer thread for that revision. Visually distinguish Reviewer questions from AI answers; show queued/processing, clarification, insufficient-evidence and failed states, and keep exact citations adjacent to the answer they support. The composer offers whole-review or selected-finding context and lets the Reviewer ask for a clearer, evidence-grounded wording proposal. A proposed edit in chat is advisory text: it does not overwrite the stored finding, change the Creator's working copy, or make a human decision. Keep the composer visible only to the assigned Reviewer while the current review is `ready_for_review`; in closed reviews retain history with a clear read-only explanation. Preserve the conversation after refresh by loading stored turns, not browser memory. For navigation, present the account and a clearly labeled **Sign out** action together in a consistent header, including narrow screens.

## 7. Forms and validation

UI schemas mirror server limits and improve feedback; server validation is final. Count Unicode **code points**, not JavaScript UTF-16 code units, for the 20,000-codepoint draft limit and 2,000-codepoint question limit (`Array.from(value).length`). Comments are limited to 4,000 code points. Source files accept `.txt` or `.md`, max 2 MiB; validate extension and size before upload, then display any backend `413`/`415` response. The Admin source form also collects `effective_from` and optional `effective_to`, rejecting an end date that is not later than the start date; the backend applies the organisation's time zone and final validation. Do not assume a file is safe solely from its extension. `title`, `channel`, and any server-defined values must be validated against the eventual OpenAPI schema; do not invent a channel enum in the UI.

`DraftEditor` separates **Save** (`PATCH` with `expected_working_copy_version`) from **Submit for review** (`POST /submissions` with `reviewer_user_id` and the current expected version). Populate the reviewer picker from `GET /organisations/{org_id}/reviewers`, which returns active Reviewers' `user_id` and `display_name` to the Creator or Admin while excluding the caller. Show an empty state if none is available; the backend rechecks eligibility and self-approval at submission. Do not expose the admin-only full membership list or hardcode a reviewer ID.

The `DecisionForm` is shown only when the review is `ready_for_review` and the current user is its assigned Reviewer. `request_changes` requires a non-empty comment. `approve` requires `override_reason` of at least 15 characters when a high-severity finding exists or coverage is `insufficient`; show the warning and reason field before submission. Send `expected_revision_id` and `expected_review_version` from the loaded review. On `409`, reload and ask the Reviewer to recheck the new state. The backend remains the final authority for all transition checks.

## 8. Finding anchors and Creator-applied suggestions

Findings refer to the NFC-normalized **immutable revision**, with `[start_codepoint,end_codepoint)` offsets and a `quote`. JavaScript string indexes count UTF-16 code units, so never pass the API offsets directly to `slice()` on a JS string. For display, use `const points = Array.from(revision.content.normalize('NFC'))`, then `points.slice(start, end).join('')`. Confirm that this equals `anchor.quote`; if it does not, show the finding without inline highlighting and log a non-sensitive diagnostic with the request ID. An `anchor: null` finding is a whole-draft issue.

Show a suggestion as a proposal with its explanation and citations. Only a Creator can use **Apply to working copy**. For the safe MVP, enable that button only if the normalized working-copy content exactly equals the normalized submitted revision and the anchor quote matches. A complete sentence suggestion cannot be inserted into an anchor that covers only a phrase; offer manual editing for that mismatch. Replace the codepoint range in a copy of the normalized content, update the form, and save through `PATCH /drafts/{draft_id}` with `expected_working_copy_version`. This conservative rule means a second suggestion may need manual application after the first changes the copy; it prevents an old offset from silently modifying the wrong words. If the draft changed, show **Copy suggestion** or manual editing instead. An unanchored issue is always manual. A Reviewer cannot apply edits to the Creator's copy.

Keep the original revision text read-only in the Reviewer workspace even after a Creator revises. New submissions create new revision numbers and new reviews. Findings, citations, comments, and questions remain attached to their own revision/review.

## 9. Loading, errors, accessibility, and trust

- Show a skeleton or explicit loading label on first load; preserve the previous list while a filter or page refetches. Empty states distinguish “no drafts yet,” “no assigned drafts,” “no approved sources,” and “no findings but insufficient coverage.”
- Map `401` to sign-in, `403` to access denied, cross-org `404` to not found, `409` to a refresh-and-review prompt, `413`/`415` to upload guidance, and `429` to a retry timer. For `502`/`503`/`504`, keep unsaved form values in memory and show retry plus request ID.
- Use semantic headings and labels, visible focus, keyboard-operable dialogs, contrast-checked status badges, and `aria-live="polite"` for asynchronous status changes. Do not rely on color alone to distinguish severity, processing, failure, or approval.
- Render source, draft, finding, and model text as plain text. If Markdown is later supported, disable raw HTML and sanitize links and output. Avoid `dangerouslySetInnerHTML`. Use `rel="noopener noreferrer"` on external links.
- Show a brief disclosure near submission that selected draft text and approved source passages are sent from the backend to Sarvam AI, with IndieRouter used when configured after exhausted transient Sarvam retries. Follow-up context may also be sent. Do not claim that external processing never occurs.
- On logout, clear cached private resources and in-memory CSRF. Do not store draft bodies, source passages, chat turns, or session tokens in `localStorage`, analytics payloads, or client error logs.

## 10. Suggested source layout and build order

```text
frontend/src/
├─ app/                 router, providers, role gates, app shell
├─ lib/                 typed API client, error mapper, query keys, codepoint helpers
├─ components/          shared status, evidence, form, dialog primitives
├─ features/auth/       login, session, organisation selection
├─ features/sources/    source library, upload, version detail
├─ features/drafts/     creator list, editor, timeline, suggestions
└─ features/reviews/    inbox, revision viewer, findings, chat, decision
```

Implement in vertical slices: (1) session and org switch, (2) source upload and status, (3) Creator draft/save/submit, (4) review display and citations, (5) follow-up and decisions, (6) accessibility and deployed smoke test. Each slice should use a real API endpoint or a clearly labeled temporary fixture; remove fixtures before claiming the flow works.

## 11. Deployment and acceptance checks

Build the Vite SPA on Vercel. Configure `/api/:path*` as the **specific** rewrite to the Render API and an SPA fallback for non-API routes; ensure the API rewrite is evaluated before the fallback. Use the same relative `/api/v1` URL in local and production builds, with Vite's dev proxy forwarding it locally. Do not configure a public `VITE_API_KEY` or browser-side direct Render URL. Test refresh on a nested route, sign-in cookie set/forwarding, `/auth/me`, CSRF on a POST, logout, and a full role-specific flow on the actual deployed origins. [Vercel rewrites](https://vercel.com/docs/routing/rewrites), [Vite server proxy](https://vite.dev/config/server-options).

Minimum meaningful checks:

1. Admin uploads a source; UI shows queued/processing/ready, then approved only after approval. A Creator cannot open admin source or member controls.
2. Creator saves, submits to an eligible Reviewer, reloads, and sees the same revision/review state. Reviewer sees the immutable submitted text and a cited finding. Old finding offsets never edit a changed working copy.
3. Reviewer asks a follow-up, sees pending and completed/clarification/insufficient states, requests changes, then approves a later revision. The Creator sees comments and history after reload.
4. Simulate `401`, `403`, cross-org `404`, `409 stale_version`, `429`, a failed worker job, insufficient coverage, and an expired session. Check that none is shown as approval or a clean pass.
5. Check keyboard navigation, status announcements, narrow-screen layout, and the deployment cookie/CSRF path. Unit tests should target codepoint anchor conversion and error mapping; one end-to-end smoke test should cover the complete Creator → Reviewer → Creator → Reviewer loop.

## 12. Contract gaps and future scope

The API contract now includes a Creator-accessible Reviewer picker and participant-scoped, paginated comment history. Implement these routes before connecting the corresponding screens; the frontend must not substitute the Admin-only member list or keep comments solely in browser memory.

Review detail also returns nullable `decision` with action, comment, override reason, actor and timestamp. Display it in both the Creator's revision history and the Reviewer workspace. A `changes_requested` badge alone is insufficient: the Creator must be able to read the human's change request after reload. Keep this decision separate from the comment conversation and AI findings.

Later additions include PDF/image input, visual asset inspection, rich-text editing, notifications, offline drafts, analytics, and advanced search. They do not change the text MVP's core boundary: approved evidence and immutable revisions anchor every review decision.

## Reference Boundaries

| Reference | This file uses it for | Authority boundary |
|---|---|---|
| [API Contracts.md](<API Contracts.md>) | Paths, payloads, states, errors, auth, permissions, codepoint offsets | **Authoritative backend contract.** If this file differs, fix this frontend specification or explicitly propose a contract change. |
| [Database Design and ERD.md](Database%20Design%20and%20ERD.md) | Immutable revision and comment history concepts | Internal persistence detail; the browser must use the API, not query these tables. |
| [RAG Architecture Specification.md](RAG%20Architecture%20Specification.md), [Prompt Engineering Specification.md](Prompt%20Engineering%20Specification.md) | Meaning of coverage, citations, follow-up routes, and model limitations | Internal AI behavior; the browser displays validated API results and never calls these services directly. |
| [PRESENTATION.md](../PRESENTATION.md) | Product roles, chosen stack, workflow, Round 2 priority | Product intent and scope; it does not define wire payloads. |
| [Problem_statement.md](../Problem_statement.md) | Hackathon problem and constraints | Challenge statement; this frontend spec is an implementation proposal. |
| [data/README.md](../../data/README.md) | Synthetic demo and evaluation boundaries | Data pack provenance; evaluation fixtures are not production guidance. |
| [React Router routing](https://reactrouter.com/start/declarative/routing), [TanStack Query polling](https://tanstack.com/query/latest/docs/framework/react/guides/polling), [Vite proxy](https://vite.dev/config/server-options), [Vercel rewrites](https://vercel.com/docs/routing/rewrites) | Current library/platform mechanics | Official implementation references, not VeriLoom product requirements. |

