# VeriLoom — Round 1 Submission

**Team name:** THE_KING  
**Participant:** Thunuguntla Venkata Pavan Kumar  
**Team size:** 1 (solo participant)  
**Event:** We Code Coders — Inkloom AI problem statement  
**Submission round:** Round 1 — The Plan

## 1. Understanding of the problem statement

Growing organisations create content through many employees, agencies, and AI tools. As more people contribute, it becomes harder to ensure that every piece of content follows the organisation’s brand guidelines and uses current, approved facts, terminology, messages, and visual assets.

This can lead to inconsistent tone or wording, outdated or incorrect information, unsupported promotional claims, off-brand visuals, and accessibility issues reaching customers. These problems can confuse people and damage the organisation’s credibility and trust.

We understand the challenge as building an intelligent brand-governance platform that reviews content before publication against an organisation’s approved guidance and sources. It should clearly explain each issue, show the relevant guideline or source, and suggest a meaningful correction that preserves the creator’s intent. Creators and authorised reviewers should stay in control of edits and approvals, with collaboration and revision history. The platform must also protect confidential materials and be transparent about AI and external services.

**Supporting evidence:** In a [2025 Content Marketing Institute and MarketingProfs survey](https://contentmarketinginstitute.com/enterprise-research/enterprise-content-marketing-research-findings-2025) of 310 enterprise marketers, 47% reported workflow or content-approval challenges and 61% reported difficulty communicating across organisational silos. [UK Government Digital Service guidance](https://www.gov.uk/government/publications/govuk-content-principles-conventions-and-research-background/govuk-content-principles-conventions-and-research-background) also explains why shared style rules matter when many people create content: they give writers and editors common answers on style, format, and accuracy and help readers encounter consistent material. The survey is self-reported, and the government guidance is qualitative; neither measures the impact of this proposed product. No customer interviews or product impact measurements are claimed.

## 2. Users affected by the problem

| User | Need and difficulty today | Role in the product |
|---|---|---|
| **Content Creator** — employee or agency contributor | Needs to know what to fix before publication without losing the purpose of the draft. | Submits a draft, reads cited findings and suggestions, edits, and resubmits. |
| **Reviewer** — manager, team lead, or authorised brand reviewer | Needs to verify claims and wording, ask why an issue was flagged, and make a documented decision. | Examines findings, asks follow-up questions, comments, requests changes, or approves. |
| **Brand Administrator** | Needs one controlled set of current brand rules, facts, terms, and assets. | Registers the organisation, maintains approved sources and versions, and assigns roles. |

Customers are indirectly affected when inaccurate or inconsistent material is published. The first users of the platform are the three internal roles above.

## 3. Proposed solution

The proposed product is a **source-grounded brand review workspace**. A Brand Administrator supplies approved guidance. When a Creator submits a draft, the system compares it with the relevant guidance and returns findings with an explanation, a source reference, and a suggested edit that preserves the Creator's intent. If the approved sources do not support a decision, the system marks it for human review instead of inventing a rule. The Reviewer can question findings, request changes, or approve. All decisions and draft revisions remain visible.

The distinguishing workflow is the link between **approved source version → finding → reviewer question → creator revision → human decision**. This allows a team to inspect why a flag appeared and what changed. AI suggests and explains; it does not publish content or make the final approval decision.

## 4. Complete product workflow

1. **Set up the organisation.** The Brand Administrator creates the organisation, adds approved guidance, facts, terminology, and assets, and assigns Creator and Reviewer access.
2. **Index approved knowledge.** FastAPI stores original uploaded files in a private Cloudflare R2 bucket. It extracts supported text, divides it into passages, stores source/version/status metadata in PostgreSQL, and creates local embeddings in pgvector. Only approved, effective passages for the active organisation are eligible for review.
3. **Submit a draft.** The Creator writes or uploads supported content. The backend saves the draft and its revision, then starts a review. Draft content is kept separate from approved guidance.
4. **Generate a grounded review.** The backend retrieves relevant approved passages using dense and keyword search. Sarvam AI receives the draft and selected evidence and returns structured issues, explanations, source IDs, and suggested edits. The backend validates and stores the result.
5. **Review and ask questions.** The Reviewer opens the draft and cited findings. For a follow-up question, the backend loads the relevant draft revision, finding, citations, and a short conversation history. It reuses sufficient evidence or retrieves new evidence when needed. The answer cites its sources or says that evidence is insufficient.
6. **Decide and revise.** The Reviewer comments, requests changes, or approves. The Creator edits manually or applies a proposed change, then resubmits. The system preserves each revision, comment, question, answer, and decision. The Reviewer remains responsible for approval.
7. **Learn from repeated issues.** Later in the build, saved finding categories can show recurring problems and guidance that may need clarification.

The architecture and reviewer follow-up routes are shown in the block-diagram appendix.

## 5. Main features intended for the event

**Round 2 core:** organisation and role access; approved-source ingestion with versions; text draft submission; source-cited findings and meaningful suggested edits; reviewer follow-up chat; comments, revision requests, resubmission, and approval history; deployed demo.

**Three named improvements to demonstrate in Round 2 and finish in Round 3:**

1. **Grounded hybrid review:** combine exact-term BM25 search and semantic pgvector search, cite the approved source/version, and mark unsupported cases for human review.
2. **Context-aware reviewer follow-ups:** answer questions about a finding using existing evidence when sufficient; rewrite ambiguous questions or retrieve fresh evidence when necessary; save answers against the draft revision.
3. **Revision-aware human approval:** retain an audit trail of comments and decisions, restrict actions by role and organisation, block approval of stale revisions, and summarise recurring finding categories.

The primary event workflow supports text. If time permits after it is stable, I will add one more input type, such as a text-extracted PDF or structured social post with image/alt-text fields. Actual visual checking requires image processing and approved asset comparison; the current metadata examples do not prove that capability.

## 6. Technology stack and technical plan

| Layer | Choice | Why it fits |
|---|---|---|
| Frontend | React, Vite, Tailwind CSS; Vercel deployment | Fast to build a responsive Creator/Reviewer interface and deploy a judge-accessible demo. |
| API and workflow | Python, FastAPI; Render deployment | Handles authentication and roles, ingestion, retrieval, AI calls, and review-state validation in one backend. |
| Database and vectors | Render PostgreSQL with pgvector | Keeps users, drafts, review history, source metadata, and semantic retrieval in one managed database. |
| Original files | Private Cloudflare R2 | Stores uploaded originals; PostgreSQL keeps object keys and metadata. |
| Language model | Sarvam AI | Generates structured findings, suggested edits, and grounded follow-up answers. The API key stays on the backend. |
| Embeddings and retrieval | Local `intfloat/multilingual-e5-small`; BM25 via Python `rank-bm25`; reciprocal rank fusion | Combines semantic matches with exact brand terms and product names. |
| Optional reranking | Local `BAAI/bge-reranker-v2-m3` on a small candidate set | Planned only if measured retrieval gain justifies added Render memory and latency. |
| Source control | Git and GitHub | Shows build-window progress and provides reviewable source code. |

**Hardest technical part:** returning accurate findings when approved sources contain similar wording, changed facts, or gaps. The plan uses organisation/status/version filters before retrieval, BM25 plus dense search, source IDs carried into every finding, validation of structured AI output, and an explicit insufficient-evidence path. Reviewer follow-ups are tied to the active revision rather than relying on an LLM's untracked memory. I will evaluate retrieval, citation support, unsupported claims, and response time using separate development and holdout cases.

**Privacy and control:** role and organisation checks apply to every draft and source query; R2 files are private; secrets remain server-side. Draft excerpts and selected source passages sent to Sarvam AI are processed by an external provider, so this must be disclosed. Real confidential material should only be used after the organisation approves that arrangement and current provider terms are checked.

## 7. Responsibilities of each team member

| Participant | Responsibilities | Contribution evidence |
|---|---|---|
| Thunuguntla Venkata Pavan Kumar — sole participant | Problem research, UX, React frontend, FastAPI backend, PostgreSQL schema, RAG and AI integration, evaluation, deployment, documentation, and demo. | Git history, development log, working demo, tests/evaluation outputs, and individual submission. |

There are no other team members. I will submit my own required individual form/PDF and use the same exact team name and legal name in every event field.

## 8. Development plan across all three rounds

| Round | Work and deliverable | Completion evidence |
|---|---|---|
| **Round 1 — Plan** | Define the problem and users; research the approval challenge; specify the workflow, architecture, three improvements, risk controls, and solo scope. Prepare a fictional demo/evaluation data pack. | This PDF, source links, the detailed presentation, and a clearly labelled synthetic data pack. Preparation is disclosed; it is not claimed as judged product implementation. |
| **Round 2 — Build** | During the official build window, initialise Git, build authentication/roles and source ingestion, then implement the complete Creator → AI review → Reviewer → Creator revision → approval loop. Add basic follow-up chat and hybrid retrieval. Deploy and document it. | Judge-accessible repository and deployed link or functional demo, architecture/API/model/data disclosures, a full two-account walkthrough, and actual development-case results. Request real structured feedback if available. |
| **Round 3 — Finish** | Deliver and evaluate the three named improvements; harden error handling, access checks, stale-revision protection, usability, and accessibility. Add one extra content format only if the core flow remains reliable. Prepare final report and demonstration. | Final product and repository, demo/video if requested, evaluation on untouched holdout cases, reliability and accessibility notes, limitations, roadmap, and complete participant details. |

The organiser's official dates control the schedule. Product implementation begins inside the permitted build window; prior planning and synthetic data are disclosed. With one builder, I will prioritise a reliable end-to-end text workflow before adding more formats or analytics.

**Material disclosure:** This plan and the fictional Nook & Kettle demo data used significant AI assistance and human review. The data are synthetic, not customer research or measured product results.

## 9. Expected impact

Creators should receive specific, source-backed feedback before a draft is published. Reviewers should spend less time locating rules and explaining the same issues repeatedly, while keeping the final decision. Administrators should see which guidance is repeatedly misunderstood. Customers should encounter fewer unsupported or inconsistent claims.

These are **expected outcomes, not measured results**. During the event I will record whether the correct source appears in retrieved results, whether findings cite supporting passages, how often unsupported cases are correctly deferred, the time for a normal review, and whether a user can complete the revision/approval loop. Any user feedback will be reported only if it is actually collected.

## 10. Future scope after the event

After the core workflow is validated, expand to image and logo comparison against approved assets, stronger accessibility checks, more document and social formats, integrations with content tools, notifications, larger-scale search, richer governance analytics, and human-led pilots with real organisations. These require additional data, privacy review, and testing; they are not claimed as complete event features.

## 11. Complete team and participant details

| Required detail | Participant record |
|---|---|
| Exact desired team name | THE_KING |
| Participant full legal name for certificate | Thunuguntla Venkata Pavan Kumar |
| Team size | 1 — solo participant |
| Participant email | iec2025049@iiita.ac.in |
| Institution | Indian Institute of Information Technology, Allahabad (IIIT Allahabad) |
| GitHub profile | https://github.com/9553943884pavan-alt |

**Submission check:** Verify the exact spelling of the team and certificate name, institution, email, and GitHub profile against your registration. Upload the PDF to Google Drive, set **Anyone with the link → Viewer**, and open the link in an incognito window before submitting. As a solo participant, complete the individual submission yourself. Keep the PDF link and exact team name for later rounds.

### References for technical choices

[Sarvam API model catalogue](https://docs.sarvam.ai/api/getting-started/models) · [E5-small model card](https://huggingface.co/intfloat/multilingual-e5-small) · [Reranker model card](https://huggingface.co/BAAI/bge-reranker-v2-m3) · [Render PostgreSQL extensions](https://render.com/docs/postgresql-extensions) · [Render ParadeDB guidance](https://render.com/docs/deploy-paradedb) · [Cloudflare R2 documentation](https://developers.cloudflare.com/r2/)
