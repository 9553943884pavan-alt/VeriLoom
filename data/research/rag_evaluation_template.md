# RAG and Review Evaluation Collection Guide

This guide describes how to collect actual results later. No model has been evaluated and no participant responses are recorded here.

## Canonical inputs

Use `data/evaluation/drafts.jsonl`, `followup_cases.jsonl`, and `scope_cases.jsonl` as the synthetic input and expected-output fixtures. Their contracts are in `data/schemas/`. Copy an existing record when authoring a new case and assign a new unique ID.

For follow-ups, keep the current `question` outside `history`; history contains earlier turns only. `expected_route` is `reuse_evidence`, `hybrid_search`, or `clarify`. `requires_rewrite` and `should_abstain` are independent booleans. A retrieval request may legitimately conclude that evidence is missing.

Use document-qualified evidence IDs such as `NK-FACTS-001#FACT-06`. Set `available_source_ids` to evidence supplied before the current question. A reuse-evidence case must already have the required sources. Keep a draft and its follow-ups in the same development or holdout split.

## Actual results log

Record observations in a separate results file after executing the application. Never replace expected labels with a model response or feed expected answers into retrieval.

| Field | Record after execution |
|---|---|
| Run ID and timestamp | Unique run identifier and actual time |
| Case ID | Corresponding synthetic fixture ID |
| Application and model versions | Actual configuration used |
| Retrieved source IDs | IDs in returned order, before and after reranking |
| Actual route and rewritten query | What the application chose |
| Actual findings or answer | Faithful output from the system |
| Source support | Whether cited text supports the answer |
| False positives and missed findings | Record separately |
| Organisation and version filtering | Whether disallowed records were excluded |
| Latency and cost | Measured values when available |
| Reviewer notes | Clearly distinguish judgement from measured facts |

Use development cases while tuning. Keep holdout cases out of tuning prompts and evaluate them separately. These are small, authored fixtures, so holdout results would still not establish real-world performance. Tone judgements and suggested edits allow reasonable variation; assess meaning and source support rather than exact wording.

For real participant examples, obtain consent, anonymise them, and store them separately from this synthetic collection with provenance. Do not label those examples as `synthetic_demo`, and do not present synthetic performance as user validation.
