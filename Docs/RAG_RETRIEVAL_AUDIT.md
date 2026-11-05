# Retrieval quality audit and improvement plan

Date: 2026-11-05. Scope: the pre-fix code, synthetic data, development-only experiments, primary-source research and live pgvector parity. Three agents independently reviewed code, data and research. This records the original defects and measurements. The Stage 1 code fixes and new development baseline are recorded in [HYBRID_EVALUATION.md](HYBRID_EVALUATION.md) and [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md); no deployment is claimed here.

## 1. Findings in plain language

**Dense embeddings and hybrid retrieval are implemented and execute.** The pinned E5 model produces real normalized vectors, PostgreSQL orders candidates by cosine distance, BM25 produces a separate lexical ranking, and RRF combines them. A separate live pgvector test reproduced the local diagnostic rankings exactly.

The main observed problem is selecting the right *governing rule* for a claim. Searching a sentence about a drink's calories strongly retrieves that drink's name and menu facts. The useful rules about nutrition and unsupported claims reach the candidate pool but do not reach the final eight passages supplied to the LLM.

There are also three reproduced code defects: later segments of long drafts can be skipped, token-valid padded drafts can fail a separate character limit, and new-topic follow-ups can incorrectly reuse an unrelated finding's evidence. These defects are independent of the small synthetic corpus's formatting.

The data is structurally sound, but its retrieval evaluation labels are too sparse and uneven for broad quality claims. Generated findings also have a separate semantic usefulness problem: correct citations and JSON do not establish that a finding is appropriate.

## 2. What actually runs

| Stage | Verified implementation | Evidence and boundary |
|---|---|---|
| Passage embedding | Local `intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3`; `passage: ` prefix | [embeddings.py](../backend/app/rag/embeddings.py), [ingest_source.py](../backend/app/jobs/handlers/ingest_source.py). All 45 diagnostic passage vectors are finite, 384-dimensional and normalized. |
| Query embedding | Same pinned adapter; `query: ` prefix | Real query vector norm measured as approximately `0.999999986`; model weights were cached and loaded offline. |
| Dense search | `ORDER BY p.embedding <=> CAST(:embedding AS vector), p.id LIMIT 20` | [hybrid.py](../backend/app/rag/retrieval/hybrid.py). Real pgvector SQL parity test described below. |
| Lexical search | Unicode tokenization and `rank_bm25.BM25Plus`, at most 20 matching candidates | Same organisation, approval, ready, archive, date, category and model predicate as dense search. Nonmatching BM25Plus background scores are excluded. |
| Fusion | RRF with `k=60`; at most 30 fused candidates, then at most 8 returned | [fusion.py](../backend/app/rag/retrieval/fusion.py), [hybrid.py](../backend/app/rag/retrieval/hybrid.py). Captured actual production fusion inputs and output. |
| Query planning | Token-based sentence packing, capped at eight query segments | [review_revision.py](../backend/app/jobs/handlers/review_revision.py). This is not a semantic claim extractor. The early-stop defect limits actual execution. |
| Reranking | Not implemented or called | `RERANKER_ENABLED` and `RERANKER_MODEL` are configuration placeholders. Setting the flag does not activate a reranker today. |
| Follow-up adaptation | Deterministic routing plus conditional standalone query rewriting | [followup.py](../backend/app/rag/retrieval/followup.py), [answer_question.py](../backend/app/jobs/handlers/answer_question.py). Reuse relevance is currently too broad. |

The model's official instructions require the two prefixes for asymmetric retrieval and describe a 512-token limit. The reviewed adapter follows those requirements. Its FAQ also notes that cosine values are often high; a value such as `0.75` is not a 75% probability that a passage supports a claim. [Official E5 model card](https://huggingface.co/intfloat/multilingual-e5-small).

### Live SQL verification and current database setup

`live_retrieval_sql_smoke.py --disposable` created its own separate random database and installed pgvector **only there**. Retrieval itself ran in a read-only transaction over synthetic CTE relations containing the 45 real E5 passage vectors. It executed the production eligibility/cosine SQL and production BM25/RRF code.

- PostgreSQL pgvector version: `0.8.7`.
- NK-16: one dense SQL search, one lexical corpus query, 20 dense candidates and 7 lexical candidates.
- Live PostgreSQL and local exact-cosine simulation had identical candidate rankings and identical final eight source references.
- No application tenant records were read; no LLM/provider calls were made.
- The test database was dropped and its absence confirmed.

This establishes live SQL/operator parity for this synthetic case. It does not establish stored-vector consistency, ingestion equivalence, approximate-index recall, production latency or every authorisation edge case.

A separate read-only metadata query found **no pgvector extension and none of the four checked application tables** (`organisations`, `source_versions`, `source_passages`, `alembic_version`) in the database currently configured by `.env`. Earlier successful application tests used disposable migrated databases. The configured application target therefore needs approved schema setup before it can serve the workflow. This setup gap did not cause the offline retrieval metrics. No application database migration was performed during this audit.

## 3. Why the calorie example fails

Development NK-16 is: `Cardamom Chai contains 120 calories per cup.`

Its two authored expected sources are the unsupported nutritional-value rule and the fact that nutritional values have not been supplied. Both have zero lexical token overlap with this draft under the current tokenizer.

| Expected source | Dense rank | Cosine | BM25 rank | Full RRF rank | Candidate pool | Final eight |
|---|---:|---:|---|---:|---|---|
| `NK-BRAND-001#CLAIM-03` | 12 | 0.75129 | No matching tokens | 12 | Present | Absent |
| `NK-FACTS-001#FACT-10` | 18 | 0.74341 | No matching tokens | 18 | Present | Absent |

The production top eight instead contain three drink/menu facts, three terminology entries, one voice rule and another fact. These are predominantly about the product rather than authority for the nutrition claim.

This is **100% recall of the two authored sources in the candidate union, but 0% in the final eight**, for this one case. Dense-only top eight also misses both. RRF did not uniquely cause this miss; its final ranks for the two expected passages remain 12 and 18. Increasing the first-stage pool alone is not necessary to recover these particular sources, because they are already present.

RRF does have a strong agreement preference. With current limits, a passage ranked 20 in both branches scores `2/(60+20)=0.025`, exceeding a rank-one single-branch passage's `1/61≈0.01639`. A constructed ranking reproduced a dense-rank-one singleton falling to fused rank 20. This is expected rank-fusion behavior, not a formula implementation error. The original paper supports RRF as an empirical fusion method, not guaranteed relevance on every domain. [Original RRF paper](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf).

### Bounded experiments, not selected fixes

These two additional experiments used only development NK-16. No holdout cases or gold rule text/IDs were used to form the queries or embedding inputs. Gold was read after ranking.

| Experiment | CLAIM-03 dense / fused | FACT-10 dense / fused | Final-eight authored-source recall |
|---|---|---|---:|
| Current text-only baseline | 12 / 12 | 18 / 18 | 0.00 |
| Title + passage text for dense and BM25 | 9 / 9 | 20 / 20 | 0.00 |
| Additional nutrition-information question | 13 / 21 | 5 / 3 | 0.50 |

The question was `What approved nutritional information and calorie values are available for Cardamom Chai?` It uses the draft's topic, rather than an invented company policy. It is a manually authored illustration, not an implemented planner or a validated improvement across the product.

Title enrichment alone did not improve final recall in this example. No experiment was selected as a winner, and no production ranking default was changed. There is only one source-scored development draft, so optimising against it would readily overfit.

## 4. Reproduced code and logic issues

### P0: later draft segments are never searched

In `_retrieve`, the first query contributes up to eight passages, fills `selected`, and triggers the outer-loop break. With at least eight returned passages, later planned queries never execute.

The real E5 tokenizer planned three segments in a constructed long draft. A controlled retrieval double returned eight passages for the first segment: only one search occurred, and the final vegan claim was never searched. `query_truncated` still returned `False`. This reproduction tests query-selection control flow; the retrieval double's ranking is not a model-quality measurement.

The coverage downgrade checks only `query_truncated`, so a model's `coverage='sufficient'` can survive even when later segments were skipped. This is a false coverage risk. It does **not** explain the short NK-16 miss or the existing short-draft aggregate score.

Proposed correction: execute every bounded planned query, retain query-to-passage provenance, merge and select only after all searches complete. If a budget prevents processing a claim, record partial coverage. Add a regression in which the last query contributes a distinct relevant passage.

### P0: query character and token limits disagree

`_queries` accepts a complete draft when it fits 512 model tokens, while `retrieve_evidence` rejects strings longer than 4,000 characters before trimming them.

Reproduction: 4,100 leading spaces plus NK-16 produces 4,144 characters but only 17 E5 tokens. It becomes one query with no truncation flag, then raises `ValueError: query must contain 1 to 4000 characters` before retrieval. This was reproduced with the actual tokenizer and production input validation.

Proposed correction: normalize search whitespace separately from immutable draft text, trim before checking, and enforce both character and token bounds in one query-preparation path. Test long internal whitespace, long normal text and Unicode. Preserve original draft text for anchors and citations.

### P1: new-topic follow-ups can reuse unrelated evidence

The router picks reuse whenever a current finding has evidence and the question contains a word such as `explain`, `source` or `evidence`. It has no representation of that finding's actual subject. The answer handler then supplies only the reused citations.

Four constructed questions about warranty, refunds, Sunday hours and caffeine all chose reuse while an unrelated finding was selected. These are four diagnostic examples, not a measured failure rate. A good generator may abstain, but the router has already prevented a potentially useful search.

Proposed correction: allow reuse for a narrow, explicit question about the selected finding; otherwise rewrite a contextual question or search the new topic. Retrieval failure and insufficient generation support should remain separate outcomes. Standalone conversational query rewriting has direct research support, although a trained research model's results do not establish our router's accuracy. [CONQRR](https://aclanthology.org/2022.emnlp-main.679/).

### P1/P2: query purpose and evidence selection need development evaluation

All seven development text cases currently become a single query, with 17–35 E5 tokens. A marketing statement is often a poor search question for the rules governing that statement. Extracting checkable claims and seeking both governing policy and approved factual support is a better task formulation to test.

Both lexical text and dense passage inputs omit stored titles/headings. Metadata may help, but the measured title experiment above did not solve NK-16. If the embedding recipe changes, record its version and reindex consistently; identical weight revision/dimensions do not make old and newly contextualised vectors equivalent. Keep exact source text/offsets separate from retrieval-only context.

### Other findings

- No embedding model/revision mismatch was found in the reviewed indexing/search code. Production stored vectors were not inspected because the configured target is uninitialised.
- No source truncation was found in the 45-record diagnostic pack: its longest passage input is 84 E5 tokens. The upload chunker separately verifies its token budget and exact spans.
- Unicode lexical tokens are case-folded but not NFC-normalised. Canonically equivalent composed/decomposed accented input can differ lexically; guidance extraction is NFC, while follow-up inputs only trim whitespace. This requires a focused multilingual regression, not a global accent-removal assumption.
- BM25 is rebuilt per call; no cache or reranker implementation is wired. Rebuilding affects cost/latency, not the relevance of these deterministic rankings.
- Offline canonical atomic records and real Markdown uploads take different ingestion paths. Current quality evaluation does not measure retrieval after the actual upload/extraction/chunking/storage round trip.

## 5. Is data quality responsible?

### Verified healthy properties

- 45 synthetic approved records: 24 brand rules, 11 facts and 10 terminology entries, from three source documents.
- Text lengths: 94–408 characters, approximately 186 on average; no observed replacement characters or common UTF-8 corruption in the corpus.
- Rebuilding canonical knowledge and isolation records in memory matched their checked-in JSONLs exactly.
- Seven dataset manifest entries passed independent hash/count/ID checks.
- After adding the missing `jsonschema` development dependency, the full existing validator passed: 45 knowledge, 2 isolation, 24 draft, 18 follow-up, 4 scope, 9 public-reference and 4 problem-evidence records; zero reported errors. Its generated report was captured in memory so the tracked report stayed unchanged.

This establishes structural and reference consistency. These fictional facts are demo ground truth, not verified business facts or real customer research.

### Evaluation data is the larger weakness

1. **Only one source-scored development text draft.** Six of seven clean development drafts have empty `expected_findings`; the scorer skips them. Five holdout text drafts are all scored violations. This is inadequate for choosing a general retrieval configuration.
2. **Finding citations are reused as retrieval relevance labels.** A passage can help review a clean sentence without supporting a violation. Such relevance is currently not labelled. Distinguish passage relevance, finding correctness and factual answerability.
3. **Precision numbers are incomplete-judgement precision.** Across the existing authored source IDs, even retrieving every listed relevant source gives mean precision@8 of at most 0.25 development and 0.225 holdout. The reported 0.10 holdout value is not proof that 90% of returned passages are irrelevant. Some useful governance/voice passages are unlabelled.
4. **Narrow coverage.** One fictional organisation, three drinks, two locations, one source version/date, English guidance and no normal-corpus conflict cases. Multilingual and genuine revision/expiration behavior need separate cases. A source's review-reminder date should not be silently reinterpreted as an expiration.
5. **Holdout is now exposed.** Existing holdout observations are diagnostic. Future tuning needs development judgements, followed by a fresh independently authored holdout after the pipeline is frozen. Do not claim the old five cases remain an unseen final test.

Keep the current canonical corpus. The immediate data work should be **better independent relevance judgements and a small balanced development supplement**, rather than many similar rules or fabricated business facts. Label clean and violating claims, required evidence groups, acceptable alternative passages, missing-evidence cases and hard negatives. Keep labels outside ingestion and model prompts.

## 6. What the internet research supports

| Approach | Supported claim | Application to VeriLoom |
|---|---|---|
| Exact cosine first | pgvector's default exact nearest-neighbour search avoids the recall trade-off of approximate indexes. | Keep exact search for this 45-record corpus. This is mathematical neighbour recall, not policy relevance. [pgvector documentation](https://github.com/pgvector/pgvector). |
| Retrieve then rerank | A CrossEncoder scores query/passage pairs jointly after candidate retrieval. | Our nutrition sources are already candidates: evaluate a pinned, bounded reranker before truncating to eight. Measure CPU memory and cold/warm latency; no Render fit or quality improvement was measured here. [Sentence Transformers guide](https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html), [planned BGE model card](https://huggingface.co/BAAI/bge-reranker-v2-m3). |
| Contextual retrieval | Chunk-specific context can be used for dense and BM25 representations. Anthropic reports gains on its own experiments. | Try deterministic approved title/section metadata first. Our one-case test did not improve final recall; vendor benchmark percentages are not our expected gains. [Anthropic research](https://www.anthropic.com/research/contextual-retrieval). |
| Conversational rewrite | Context-dependent questions can be rewritten into standalone search queries. | Retain names/numbers/negation and detect subject changes before reusing evidence. This is a router/query improvement to evaluate, not a reason to introduce a large agent framework. [CONQRR paper](https://aclanthology.org/2022.emnlp-main.679/). |
| HyDE | An LLM-generated hypothetical document can guide dense retrieval, but can contain invented content. | Lower priority: adds a model call and risks generating policy-like text. Any experiment must use that text only for search and cite real approved passages. [Original HyDE paper](https://aclanthology.org/2023.acl-long.99/). |

The project-specific recommendations in the right column are engineering inferences. No external benchmark proves they improve our product. Replacing the embedding model or enabling a heavy reranker is not supported by the current one-case development evidence.

## 7. Proposed implementation order and acceptance checks

1. **Repair deterministic defects first:** all bounded draft queries execute; search normalization obeys both limits; unrelated follow-ups search again. Preserve immutable draft text, tenant/date filters and human approval. Test last-query contribution, truthful partial coverage and new-topic routing.
2. **Strengthen evaluation before tuning:** add independent retrieval judgements for clean and violating development cases; a compact supplement should cover factual, promotional, tone, terminology, mixed, missing-evidence, Unicode and follow-up cases. Freeze an independently authored fresh holdout. Do not edit policies or labels merely to make the current ranking pass.
3. **Compare stages separately:** dense-only, BM25-only, RRF candidate union, final selection; report required-evidence-group recall, MRR/nDCG with graded judgements, clean-case false findings, and latency. Measure actual ingestion/stored-vector retrieval as well as canonical-record diagnostics.
4. **Test claim/topic query planning and evidence coverage:** keep bounded query and prompt budgets, capture which claim retrieved each passage, and choose the final eight after merging. An omitted claim must reduce coverage. Batch/caching can control the extra retrieval cost without skipping claims.
5. **Evaluate conditional reranking over the retained candidate pool:** record candidate recall before and after selection, reranker usage, warm/cold latency and worker memory. Pin the model revision and define fallback behavior. Enable only after broader development evidence and deployment capacity checks.
6. **Test contextual indexing only if needed:** use approved metadata, keep citation text exact, version the input recipe and reindex. Compare on the same independent judgements.
7. **Evaluate generation separately:** the earlier NK-21 test had relevant voice rules but still failed output validation; NK-16 produced unwanted findings despite valid anchors/citations. Add compliant-text/no-op-edit checks and semantic usefulness judgements. Retrieval improvements alone do not fix these failures.

## 8. Reproduction and verification

From `backend/`, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -B scripts/diagnose_retrieval.py --experiments
.\.venv\Scripts\python.exe -B -m tests.integration.live_retrieval_sql_smoke --disposable
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check --no-cache app tests scripts alembic
.\.venv\Scripts\python.exe -m pip check
```

The diagnostic is development-only and provider-free; its SQL boundary is simulated. The live SQL check uses only synthetic CTE data; `--disposable` needs database-creation permission and removes its exact test database. Default live-check mode requires pgvector already installed and performs read-only SQL against the configured database with synthetic relation shadows.

Static validation used the existing `data/validate_data.py` with its report write intercepted in memory; the validator logic and dataset bytes were unchanged. `jsonschema==4.25.1` is now pinned in `backend/requirements-dev.txt` for reproducibility.

Runtime and source inspections found the defects above; **production retrieval behavior is unchanged by this audit**. Proposed corrections require implementation and regression verification. No production deployment, application schema migration, provider calls or holdout parameter tuning were performed.

## Reference boundaries

- This file owns audit observations, measured diagnostic scope and proposed experiments. It does not redefine public API fields or approve new production behavior.
- [RAG Architecture Specification](Product%20specification/RAG%20Architecture%20Specification.md) owns retrieval implementation targets; [API Contracts](Product%20specification/API%20Contracts.md) owns user-visible states and evidence shapes.
- [Data README](../data/README.md), [data audit](../data/AUDIT.md) and schemas own dataset eligibility and provenance. Schema success is not semantic truth or a quality benchmark.
- [Earlier hybrid evaluation](HYBRID_EVALUATION.md) retains the earlier baseline/generation observations. The live SQL parity test here closes only its simulated-SQL comparison gap for NK-16.
