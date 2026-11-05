# RAG Architecture Specification

This is the implementation contract for VeriLoom's approved-source retrieval, initial draft review, and reviewer follow-up answers. It explains the ML and retrieval work in terms a backend implementer can build and test. The [API Contracts](API%20Contracts.md) owns public HTTP shapes and permissions; [Database Design and ERD](Database%20Design%20and%20ERD.md) owns physical tables, constraints, indexes, and migrations; [Prompt Engineering Specification](Prompt%20Engineering%20Specification.md) owns prompt text, template versions, and model output schemas. If a detail here conflicts with a public response shape, the API contract wins until both documents are deliberately revised together.

## 1. What the RAG system is allowed to decide

RAG finds relevant **approved organisation evidence** and helps Sarvam produce review findings and grounded answers. It does not approve content, modify a Creator's draft, or turn a draft into a policy. A human Reviewer makes the decision. Uploaded guidance remains private in R2 and PostgreSQL, but the selected draft text and passages sent to Sarvam leave our storage boundary; the product must disclose that to an organisation before it uses the AI review.

The build window MVP accepts administrator `.txt` and `.md` guidance up to 2 MiB and Creator text drafts. The fixture pack is synthetic. Image/PDF extraction and pixel-level logo checks are outside this MVP. A metadata description of an image must never be reported as proof that the image itself was checked.

**One-line flow:** administrator upload → private R2 original → queued ingestion → NFC text passages → local E5 embeddings → PostgreSQL + pgvector → administrator approval → scoped hybrid search → Sarvam review or answer → server citation validation → persisted finding or chat turn → human Reviewer.

## 2. Stable inputs, outputs, and ownership

| Stage | Input | Persisted output | Owner |
|---|---|---|---|
| Ingest | Immutable `source_version` ID and R2 object | `source_passages`, 384-dimensional vectors, ingestion status | Worker |
| Retrieve | Server-derived `org_id`, `as_of` date, query, optional category, `corpus_version` | Ranked evidence candidates, then `review_evidence` or `chat_evidence` snapshots when supplied to a prompt | RAG service |
| Review | Immutable `revision_id`, assigned `review_id` | Findings, coverage, citation snapshots, corpus version | Worker + Sarvam adapter |
| Follow-up | Active `review_id`, `revision_id`, raw question, optional finding ID | Route, optional rewrite, answer status, answer, citations | Worker + Sarvam adapter |

The Python service signatures are those in the [API Contracts](API%20Contracts.md): `ingest_source(version_id)`, `retrieve_evidence(org_id, as_of, query, category, corpus_version, top_k=8)`, `review_revision(review_id)`, and `answer_question(turn_id)`. The browser never calls these functions or the model provider directly. Public status is read through `GET /api/v1/organisations/{org_id}/reviews/{review_id}` and the corresponding question-turn endpoints. The caller's authenticated membership supplies `org_id`; text inside a question cannot change the scope.

## 3. Source ingestion

1. The API authorises an Admin, validates extension/size, hashes the exact uploaded bytes with SHA-256, and writes the original to the private R2 key `org/{org_id}/sources/{source_id}/versions/{version_id}/original`. It commits the pending source version and ingestion job together, then returns `202`. R2 success followed by database failure leaves an orphan object for deletion/reconciliation; it never makes a source searchable.
2. The worker claims the job and reads only its recorded R2 key. Decode supported text as UTF-8, reject undecodable or empty input, normalise text to NFC, and record an extraction/ingestion error without approving anything. Preserve the original bytes in R2 for audit. Do not strip content that could change a rule's meaning; normalise line endings and whitespace only with stable offset bookkeeping.
3. For uploaded Markdown, split at headings and paragraph boundaries first. Keep each chunk's text as a **contiguous exact span** of the NFC-normalised source so `start_offset` and `end_offset` identify that text unambiguously; store the enclosing heading as passage `title` metadata instead of prepending a distant heading to the chunk. Pack adjacent short paragraphs only within one section; never merge unrelated policy sections or different source versions. If a paragraph is too long, split at sentence boundaries, then word boundaries. For an unbroken span that still exceeds the budget, split at verified Unicode code-point boundaries and recount each resulting embedding input with the real tokenizer; fail if even one code point cannot fit. A small overlap may improve context, but duplicate text must remain attributable to the same source version and must not create duplicate independent rules. Store `chunk_index` and start/end offsets into the normalised source text.
4. Use the E5 tokenizer to count the full embedding input, including the literal `passage: ` prefix and special tokens. Keep every chunk at **at most 512 model tokens**; target a lower working budget (for example 300–400 tokens) so titles/section labels can fit. Never silently truncate. A source that yields no valid passages fails ingestion. The model is `intfloat/multilingual-e5-small`, pinned to a tested revision; it outputs 384-dimensional vectors. Embed `passage: <chunk text>` locally, store `vector(384)`, and record the embedding model/revision used so a later model change triggers an explicit re-embed. [E5 model card](https://huggingface.co/intfloat/multilingual-e5-small).
5. Commit all passages for the version, vector values, and `ready` status transactionally. `ready` means extraction and indexing succeeded; it does **not** mean approved. The Admin's separate approval endpoint atomically activates the version, archives the prior active version of that logical source, and increments `organisations.corpus_version`. A failed or pending version contributes zero candidates.

For the demo seed, ingest only [data/knowledge/records.jsonl](../../data/knowledge/records.jsonl) rows with `ingestible=true`, `status=approved`, matching organisation, and applicable dates. Map the fixture slug `nook-kettle` to the demo organisation UUID. Each `record_id` is one canonical rule/fact and becomes `source_ref`; preserve `document_id`, `source_version`, `title`, `category`, and effective dates. If an atomic record exceeds the token budget, make multiple passages with the same `source_ref` and distinct `chunk_index`. Do not also ingest its human-readable `brand_sources/*.md` mirror. Never ingest `data/evaluation/*`, public references, or `ingestible:false` isolation fixtures into the normal corpus. The [data pack README](../../data/README.md) and [knowledge schema](../../data/schemas/knowledge.schema.json) define those fixture fields.

An uploaded document receives generated passage references such as `DOC-{source_id}#P0001`. `source_ref` is a citation-facing string, **not** the PostgreSQL primary key. `passage_id` is the immutable UUID used for joins and retrieval trace; `version_id` identifies the immutable file snapshot. The source version's display string, such as `1.0`, is separate from `version_id`.

## 4. Corpus membership, versions, and date rules

Before either dense or BM25 ranking, build the eligible set from a server-derived organisation UUID and the requested review date:

```text
source_passages.org_id = authenticated org_id
AND source_versions.approval_status = approved
AND source_versions.ingestion_status = ready
AND sources.archived_at IS NULL
AND source_versions.effective_from <= as_of
AND (source_versions.effective_to IS NULL OR as_of < source_versions.effective_to)
```

Apply a category filter only when a question genuinely calls for one; broad claim review may need both a rule and a fact in different categories. Authorisation is always applied before category filtering. Use the organisation's IANA time zone when deriving `as_of`; the MVP rejects approval of a future-dated version and treats validity as `[effective_from, effective_to)`. Archiving removes a version from **new** retrieval but retains its passages for historical citations. If an active version reaches `effective_to`, it is ineligible even if its approval status has not changed; a daily change in eligible documents must invalidate the BM25 cache.

A review records `reviewed_against_corpus_version` and an `as_of` date at job start. Run its candidate queries against one repeatable-read corpus snapshot, or detect a changed `corpus_version` and retry selection before the model call. Capture the selected `passage_id`, exact text, source/version IDs, dates, and approval status in a review evidence snapshot **before** calling Sarvam. A concurrent Admin approval cannot swap evidence midway through one review. The snapshot documents what the review relied on; it does not make archived or expired passages current authority for a later follow-up. A follow-up checks the **current** approved corpus and, if a cited version has been replaced or expired, retrieves again and explains that the guidance changed. Historical review/answer citations retain their original source version; derive `current_status:"archived"` for replaced/manually archived versions and `current_status:"expired"` for an otherwise approved version past `effective_to`.

## 5. Hybrid retrieval and optional reranking

For a source search, formulate a short query from the draft claim or review question. Use `query: <text>` for E5, with the same tokenizer limit and pinned model used for passages. Embed the query locally. The search stages operate on exactly the eligible set in Section 4:

Normalize only the search-query copy with NFC and collapsed Unicode whitespace before checking its 4,000-character and 512-model-token limits. Keep the submitted revision text, code-point anchors, and stored citation passages unchanged. Apply the same bounds to every planned long-draft segment and direct follow-up search.

| Stage | MVP implementation | Why it exists |
|---|---|---|
| Dense | pgvector cosine similarity over `vector(384)`; return a bounded top list from the scoped SQL query | Finds meaning despite different wording or language |
| Lexical | Python `rank-bm25` over a **separately scoped** in-memory corpus | Finds exact product names, prices, policy terms, and negations |
| Fusion | Reciprocal Rank Fusion (RRF) over passage IDs, e.g. `score(p)=Σ 1/(60+rank_i(p))` | Combines ranks without comparing incompatible raw scores |
| Rerank | Optional local `BAAI/bge-reranker-v2-m3` on a bounded fused set | Can improve passage ordering if measured quality warrants its cost |
| Select | Deduplicate by `passage_id`, limit prompt passages and total tokens | Keeps context focused and provider costs bounded |

Start with configurable candidate limits (for example 20 dense + 20 BM25, fuse at most 30, and send at most 5–8 passages) and tune them on **development** cases; these are proposed defaults, not measured optimums. The internal `top_k=8` is a maximum returned by `retrieve_evidence`, not a promise that eight valid sources exist. Do not impose an arbitrary cosine-score cutoff as the only evidence test: inspect whether the text actually supports the proposed finding or answer. If the reranker is enabled, pin its revision, run it only on the fused candidates, record `reranker_used`, and fall back to RRF if loading/scoring fails. Keep it disabled if it does not materially improve retrieval on the dataset within Render latency/memory limits. [pgvector](https://github.com/pgvector/pgvector), [rank-bm25](https://github.com/dorianbrown/rank_bm25), [BGE reranker model card](https://huggingface.co/BAAI/bge-reranker-v2-m3).

### BM25 cache and small-corpus limits

The small demo corpus can build a `rank-bm25` index per `(org_id, corpus_version, as_of_date)`; if category limits the candidate set, filter **before** ranking or include category in the cache key. Cache passage IDs, token lists, and index together. Never use a global cross-organisation BM25 index and filter its top results afterward: that can leak ranking influence and omit valid in-scope hits. Rebuild after Admin approval/archive, effective-date rollover, or a code change to lexical tokenisation; use a bounded cache size and locks so two jobs do not race to replace one entry. Tokenise Unicode words consistently for documents and query, preserve useful numeric/product tokens, and benchmark stemming/stopwords on fixture cases before adopting them. If the corpus grows beyond what fits a Render worker, move lexical search to a database-backed index while retaining the same scoped-candidate contract.

Approximate pgvector indexes may return too few results under selective filters. For this small MVP, exact scoped cosine search is an acceptable first implementation; if an ANN index is introduced, verify recall and scoped result count against the exact baseline. Database Design owns the final SQL/index choice. A zero-candidate retrieval is a first-class result, not permission to answer from model memory.

## 6. Initial review of a submitted draft

`POST /drafts/{draft_id}/submissions` freezes the Creator's NFC-normalised content into an immutable revision, creates one review and a durable `review_revision` job, and returns `202`. The worker:

1. Confirms the review still points to its immutable revision and assigned organisation. It loads the revision text and active approved corpus. Draft text is **never** inserted into `source_passages` or the BM25 index.
2. Derives retrieval queries from checkable claims, product names, and relevant guidance areas. A short draft may be searched as a whole; a longer draft should be segmented by sentence/claim so one broad embedding does not hide a specific rule. The query planner may be deterministic first, then improved with a versioned model step if evaluation shows a need. All generated query text remains untrusted input.
3. Runs hybrid retrieval for each query, deduplicates candidate passages, and captures the evidence snapshot. A limited prompt receives the immutable draft, its channel, relevant passages with opaque `passage_id` labels, and the output contract defined by the Prompt Engineering Specification. Only the backend/worker calls Sarvam V2 chat.
   The bounded query plan must finish before selecting the shared maximum of eight passages. Reserve each segment's leading eligible passage where possible, fill remaining slots by the existing per-query order, and store each selected passage's query index and local retrieval ranks in `review_evidence.retrieval_scores`. If the plan truncates the draft or any searched segment contributes no selected evidence, the worker cannot publish `coverage:"sufficient"`.
4. Parses and validates the model's structured findings. For this text-only MVP it may emit categories `tone`, `terminology`, `factual_claim`, `promotional_claim`, `accessibility`, or `other` when grounded in the supplied text; reject `visual_asset` until actual visual input and validation exist. Each supported finding needs a valid retrieved citation and exact supporting source quote. For an unsupported factual/promotional claim, use `evidence_status:"insufficient"`, `citations:[]`, and a human-review explanation. A whole-draft issue may have `anchor:null`.
5. Sets review `coverage` to `sufficient`, `partial`, or `insufficient` based on what was actually checked and grounded. Store `coverage_reason:null` for sufficient coverage and a concise, nonempty reason for partial or insufficient coverage. If no approved evidence can support a meaningful check, finish as `ready_for_review` with `coverage:"insufficient"`, a reason, and a visible **Needs human review** warning. Do not output a clean pass. A provider timeout, invalid unrepairable response, or indexing dependency failure sets `failed` with a safe retryable/nonretryable summary, not an empty result.

The persisted finding must match the exact API shape: `finding_id`, `category`, `severity`, `evidence_status`, `anchor`, `explanation`, `suggested_edit`, and `citations`. The model supplies a **quoted draft span**, not trusted offsets; the backend finds that quote in the immutable NFC revision and computes the half-open Unicode **code-point** range. If the quote is absent or occurs more than once, leave `anchor:null` or reject an unintelligible candidate; never guess a location. Verify `revision_text[start_codepoint:end_codepoint] == quote` before storing. React must convert code-point positions to JavaScript UTF-16 offsets when highlighting. The suggested edit is a proposal for the Creator, never an automatic source change.

## 7. Adaptive conversational follow-ups

An assigned Reviewer can `POST /reviews/{review_id}/questions` only for the current revision while the review is `ready_for_review`. Save the raw question and enqueue `answer_question`; the response is `202`, then the browser polls the turn endpoint. Each turn is tied to one review and revision. Load that immutable revision, optional selected finding, its evidence snapshots, and a **bounded recent** chat window from PostgreSQL. The LLM has no durable private memory outside these records. For each turn, choose one public route:

| Route | When to use it | Retrieval action |
|---|---|---|
| `reuse_evidence` | Direct question about the finding and its still-approved citations fully answer it | Revalidate current scope/dates; reuse a bounded set; no search |
| `rewrite_retrieve` | “What about Sunday?” or another reference needs revision/chat context to stand alone | Save a standalone `rewritten_query`, then scoped hybrid search |
| `fresh_retrieve` | New topic or old citation is no longer current | Search current approved corpus; explain changed guidance if relevant |
| `clarify` | Several plausible referents remain and guessing risks a wrong answer | Ask a precise clarification; `answer_status:"clarification_needed"` |
| `abstain` | A clear question has no sufficient approved support | Explain what is missing; `answer_status:"insufficient_evidence"` |

The route decision can start as explicit rules using `finding_id`, reference phrases, and source validity, with a small model classifier only if needed. A rewrite must preserve the user's intent and must not insert a fact as if approved. For every non-clarification route, check whether the chosen passages actually support the answer; retrieval relevance alone is not proof. Save route, raw/rewrite query, passage IDs, answer status, model/template version, and citation snapshots. The [follow-up fixture schema](../../data/schemas/followup.schema.json) uses the coarser expected route `hybrid_search`; map it to the API's `rewrite_retrieve` when `requires_rewrite=true` and to `fresh_retrieve` otherwise. `reuse_evidence` and `clarify` map directly. Evaluate `should_abstain` as an **answer outcome**, not a required route: a reused passage may still fail to establish a requested fact, and an ambiguous case may need clarification. A clear unsupported case can use API route `abstain` with `answer_status:"insufficient_evidence"`; a `clarify` route yields `answer_status:"clarification_needed"` even when the fixture says to avoid a definitive answer.

The Stage 1 deterministic router reuses current finding citations only for a complete, explicit request about that selected finding. A new topic searches current approved guidance; a reference that needs context is rewritten before search. The answer still validates whether the resulting current evidence supports the requested claim.

## 8. Citations, provenance, and safe refusal

The model can cite only IDs in the evidence snapshot supplied for **that** review/turn. The server maps each returned `passage_id` to `source_passages`, confirms the `org_id` and version, verifies eligibility in the captured review/turn snapshot, and verifies that any quoted excerpt is an exact substring of the stored passage. A new follow-up must first make its own current-approval/effective-date check; an old review keeps its historical evidence valid even if that source later gets archived. Populate the public evidence object from database fields, never from model-written title, version, date, or source reference:

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

The API's JSON object above is illustrative; real `excerpt` comes from the exact stored passage and is length-bounded for display. The evidence detail endpoint lets the Admin inspect organisation passages and lets a Creator or assigned Reviewer inspect only a passage **cited** in a review/chat they may access. Store citation joins/snapshots so archived or expired source evidence remains auditable, while recalculating `current_status` as `approved | archived | expired` for display. Reject fabricated passage IDs, citation text not found in the passage, out-of-scope IDs, and contradictions between a claim and evidence. A bounded repair attempt may retry a malformed model response with the same frozen inputs; if it still fails, return `coverage:"insufficient"` or `answer_status:"insufficient_evidence"` as appropriate. Provider outages are job failures, not evidence insufficiency.

## 9. Worker reliability and operational traces

Use the durable PostgreSQL `jobs` queue specified by the API contract: `ingest_source`, `review_revision`, and `answer_question`, claimed with `FOR UPDATE SKIP LOCKED`, leases, bounded retries, and backoff. Start with one Render worker to avoid loading multiple local E5/reranker copies. A job handler must be idempotent: upsert/replace passages for one pending version within a transaction, keep one review per revision, and avoid duplicate findings or chat turns after a lease expiry. Do not retry a terminal validation error as though it were a transient provider timeout. A failed review's explicit retry endpoint reuses the same review/revision and respects the contract's retry count; it never silently creates a new submitted revision.

Record diagnostic metadata without secrets or complete sensitive documents in generic logs: job ID, org ID, resource ID, corpus version, embedding/reranker/provider/template versions, route, candidate counts, retrieval latency, provider latency, validation failure code, and retry count. Keep full draft/source text in permissioned application records only. If R2, PostgreSQL, local model loading, or Sarvam fails, update the correct resource/job state and return the contract's safe failure field. The browser polls state; no background job should publish or approve a draft.

## 10. Evaluation and release checks

Use [data/evaluation/drafts.jsonl](../../data/evaluation/drafts.jsonl), [followup_cases.jsonl](../../data/evaluation/followup_cases.jsonl), [scope_cases.jsonl](../../data/evaluation/scope_cases.jsonl), and the two non-ingestible [isolation fixtures](../../data/knowledge/isolation_fixtures.jsonl) as **offline checks**, never prompt context or normal knowledge. The direct text-only MVP evaluation subset is **12 of 24 drafts** (`content_format:"text"`; seven development, five holdout) and **nine of 18 follow-up cases** linked to those text drafts (six development, three holdout). The other 12 drafts use `image_metadata`, which the public text-only submission API rejects; their nine linked follow-ups are future-format or negative-format cases, not scored text-review results. Do not claim 24-draft or 18-follow-up performance for this MVP. Development cases support tuning. Preserve the holdout split until the implementation is fixed; then report its results once, with failures. First run `python data/validate_data.py` to confirm the pack still matches its schemas and manifest. That validates fixture integrity, not model performance.

| Layer | Measure or test | Failure to surface |
|---|---|---|
| Eligibility | 100% expected org/status/effective-date exclusion on scope cases; deliberately query another org and an archived price | Cross-org or stale guidance appearing |
| Retrieval | Recall@5/@8 or MRR against expected source IDs; compare dense-only, BM25-only, RRF, and optional reranker | Right rule missing from bounded evidence |
| Findings | Precision/recall against expected issue categories, anchor validity, unsupported-claim handling | Unsupported edits or missed rules |
| Follow-up | Route choice, rewrite fidelity, expected-source recall, answer support, clarification/abstention | Answer from chat memory or wrong source |
| Citation | Every citation ID resolves to the captured eligible passage/version; excerpt exactness | Fabricated or cross-version citation |
| Operations | End-to-end review/question latency, p95 worker time, provider errors, retries, model memory | Demo timeout or duplicate result |

The thresholds for model quality and acceptable latency must be set after a local baseline; do not claim measured accuracy before running it. The release gate is a live **three-role** path, with distinct Creator and Reviewer accounts: Admin approves guidance; Creator submits a text revision; Reviewer sees cited findings or a visible insufficiency warning; Reviewer asks a direct and a new-topic question; Reviewer requests a change; Creator resubmits; Reviewer approves; old citations and revision history remain inspectable. Include one demonstrated abstention and one source-isolation test.

### Approved provider fallback

The worker uses Sarvam first. After its bounded transient retries are exhausted, a configured IndieRouter adapter may receive the required private draft, approved passages and bounded recent context; the project owner explicitly approved that transfer. Permanent Sarvam errors and malformed output do not trigger fallback. Both providers use the same prompt versions and server validators. Persist actual provider/model attribution independently for the review, rewrite and grounded answer using migration 007; clarification/abstention without generation has no answer provider. No fallback completion bypasses citation or anchor checks.

Provider retries can exceed one queue lease. The worker renews its lease on a separate database session every third of the lease duration while a handler runs. Before handler commits, it verifies and locks the running job's organisation, worker ownership and unexpired wall-clock lease in that same transaction. Known or uncertain loss rolls back publication and abandons the stale attempt. Stop the renewal thread before terminal queue writes; their own ownership checks still apply.

## Reference boundaries

- **Public API, roles, state transitions, evidence JSON, and internal function signatures:** [API Contracts](API%20Contracts.md). This document implements retrieval behind those shapes and does not redefine them.
- **Tables, keys, indexes, migrations, retention, and lifecycle:** [Database Design and ERD](Database%20Design%20and%20ERD.md). The logical filters and immutable snapshot requirements here are inputs to that design.
- **Model instructions, prompt templates, variables, versioning, structured JSON schema, and repair prompts:** [Prompt Engineering Specification](Prompt%20Engineering%20Specification.md). This document owns candidate selection and deterministic validation around those prompts.
- **Screens, polling, highlighting, and citation UI:** [Frontend Architecture Specification](Frontend%20Architecture%20Specification.md). React consumes the API and never reconstructs source approval rules.
- **Product scope and round plan:** [PRESENTATION.md](../PRESENTATION.md); [Problem_statement.md](../Problem_statement.md). The former is a plan, not proof that features already work.
- **Fixture contract and provenance:** [data/README.md](../../data/README.md), [knowledge schema](../../data/schemas/knowledge.schema.json), [follow-up schema](../../data/schemas/followup.schema.json). Development/holdout answers stay outside ingestion and runtime prompts.
- **External implementation references:** [E5 model card](https://huggingface.co/intfloat/multilingual-e5-small), [pgvector](https://github.com/pgvector/pgvector), [rank-bm25](https://github.com/dorianbrown/rank_bm25), [BGE reranker model card](https://huggingface.co/BAAI/bge-reranker-v2-m3), [Sarvam V2 chat](https://docs.sarvam.ai/api-reference/chat/chat-completions-v2), [Cloudflare R2 uploads](https://developers.cloudflare.com/r2/objects/upload-objects/).
