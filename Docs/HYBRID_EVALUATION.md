# Synthetic hybrid evaluation

Measured locally on 2026-11-05 using the pinned `intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3` weights. These are synthetic fixture results, not real-world quality evidence.

From the repository root, reproduce retrieval with:

```powershell
.\backend\.venv\Scripts\python.exe -B .\backend\scripts\evaluate_hybrid_mvp.py
```

The script requires cached weights and disables Hugging Face network access. It reads only the 45 eligible approved knowledge records as corpus, runs the 12 text drafts (7 development, 5 holdout), and reads answer keys only after retrieval. Evaluation labels are never embedded or searched. It calls production `_retrieve` and `retrieve_evidence`, including query planning, BM25Plus, candidate limits, and RRF. A narrow Session-compatible adapter replaces pgvector SQL with exact cosine over the real E5 vectors. Fixture eligibility is applied first; this does **not** verify PostgreSQL queries, database tenant isolation, persistence, or deployed latency. No retriever tuning was performed using holdout results.

## Measured retrieval run

| Metric | Development | Holdout |
|---|---:|---:|
| Text drafts | 7 | 5 |
| Source-scoreable drafts | 1 | 5 |
| Mean recall@5 / @8 | 0 / 0 | 0.40 / 0.40 |
| Mean precision@5 / @8 | 0 / 0 | 0.16 / 0.10 |
| Mean reciprocal rank (top 8 returned) | 0 | 0.35 |
| Retrieval mean / p50 / p95, ms | 57.310 / 48.200 / 88.221 | 78.755 / 79.823 / 90.860 |

The six remaining development drafts have no expected source IDs and are unscored for relevance; their retrieval time is included. NK-16 (the sole source-scoreable development draft) retrieved neither expected source. Holdout recall@8: NK-19 0, NK-20 0, NK-21 1, NK-22 0.5, NK-23 0.5. These misses limit any quality claim.

There were 12 dense queries, one measurement per draft, with no truncation. Model loading took 17,221.262 ms; embedding the 45 passages took 1,504.335 ms. Retrieval timings include query embedding, local simulated cosine, BM25 and RRF. They exclude model loading, corpus embedding, PostgreSQL/network, generation, and worker overhead. Percentiles over 7/5 samples are descriptive only.

The persisted script was subsequently executed successfully to verify reproduction: all 12 source rankings and relevance metrics matched the inline run, with zero provider calls. Its observed retrieval mean/p50/p95 was 42.176/40.570/47.682 ms (development) and 42.016/41.839/43.089 ms (holdout); model load 12,354.581 ms, passage embeddings 1,127.970 ms. This second run verifies reproducibility rather than tuning. Timing differences illustrate why these local samples are not deployment budgets. `ruff check --no-cache backend/scripts/evaluate_hybrid_mvp.py` passed; the first cached Ruff invocation could not write its cache under the sandbox.

## Generation attempts and limits

The initial inline evaluation requested 12 Sarvam-only completions using unchanged production prompts/schema and only synthetic draft/evidence payloads. All failed with `SarvamTransientError`; zero reviews reached validation. Those fast sandbox failures do not measure model quality.

A later bounded probe reused the exact eight-source hybrid snapshots for NK-16 (development) and NK-21 (holdout), using production `build_worker_chat`: Sarvam first, then the approved IndieRouter fallback after exhausted transient retries. Both logical completions failed with `OpenAICompatibleTransientError`, after 1,529.411 ms and 1,317.517 ms respectively. The production adapters used their bounded default retries. No successful provider/model provenance was available. Credentials were consumed only by normal server adapters and were not printed. Provider calls were not repeated after these sandbox failures.

The default script makes zero provider calls. The sandbox probes above were inconclusive about network availability and model quality; the approved network run below supersedes their generation limitation. Multilingual, clean-case false-positive rates, aggregate finding precision/recall and full worker/DB end-to-end quality remain unmeasured.

## Approved network generation smoke

An explicit opt-in now reproduces a bounded two-case probe:

```powershell
.\backend\.venv\Scripts\python.exe -B .\backend\scripts\evaluate_hybrid_mvp.py --generate-smoke
```

This option is restricted to synthetic NK-16 and NK-21. It freshly retrieves evidence through the same hybrid path, uses unchanged production prompts/schema and `build_worker_chat`, validates through `validate_review`, closes providers, and appends offline answer keys only after both completions. The run used approved execution outside the restricted network sandbox. Exactly two logical completions were requested; no retries/tuning of holdout were performed after this run (provider-internal bounded retry policy is unchanged).

| Case | Result | Findings passing validator | Exact anchors | Exact citations |
|---|---|---:|---:|---:|
| NK-16, development | Sarvam / sarvam-105b; partial coverage | 3 | 3 | 1 |
| NK-21, holdout | `ValidationError`; review rejected | 0 accepted | Unmeasured | Unmeasured |

NK-16 generation plus validation took 4,921.999 ms. Its accepted code-point anchors were `[23,43)` for `120 calories per cup`, `[0,13)` for `Cardamom Chai`, and `[0,44)` for the whole draft. The one cited quote, `Preferred term: Cardamom Chai.`, occurs exactly in retrieved `NK-TERMS-001#TERM-03`. These exact provenance checks passed; they do not establish that the findings are appropriate.

Manual audit against the offline label found one expected issue and two unwanted findings in NK-16. The high-severity insufficient-evidence nutrition finding correctly identifies the unsupported calorie claim and proposes removing it, matching the authored expected issue. Neither of the label's two preferred nutrition-rule sources was retrieved, so this is an abstention rather than a supported nutrition-rule citation. A medium terminology finding merely confirms the name is already correct and says no change is needed: it should not be an issue. A low tone finding overreaches on a short factual sentence, supplies no citation despite available voice guidance, and its suggested rewrite repeats the same unverified calorie claim. Thus all three passed structural/provenance validation while two fail this manual usefulness/support audit. This is evidence of a remaining semantic validation gap, not a quality success claim.

NK-21 retrieved both expected voice rules but its generated response failed Pydantic validation and was rejected. The safe report retained the exception class only; the failed candidate and field details were not retained, so no finding/anchor/citation count or semantic judgement is claimed for that rejected review. There was no second holdout attempt. The run again reproduced all retrieval relevance metrics; retrieval mean/p95 was 47.293/60.659 ms development and 47.879/60.591 ms holdout. These exclude generation and database overhead. Ruff passed with `--no-cache` after adding the option.

## Stage 1 development-only retrieval baseline, 2026-11-05

After the deterministic Stage 1 fixes, `backend/scripts/evaluate_stage1_retrieval.py` scored the separate 20-case `data/evaluation/retrieval_judgments_dev.jsonl` supplement against the same 45 approved synthetic records. Cases comprise seven clean drafts, seven violating drafts, two unverified-claim drafts, and four explicit follow-up search questions. The evaluator uses the pinned real E5 model, production query planner/BM25/RRF/final selection, and simulated exact-cosine SQL. It does not open the old draft/follow-up holdout files. Labels are checked and scored only after all ranking; no provider is called. Finding-correctness labels are stored separately and **not scored** by this retrieval-only run.

| Stage | Retrievable evidence groups found | Scope |
|---|---:|---|
| Dense top 8 | 21/25 (84%) | 20 searched queries |
| BM25 top 8 | 20/25 (80%) | 20 searched queries |
| RRF candidate pool | 23/25 (92%) | 20 searched queries |
| Final selected 8 | 20/25 (80%) | 20 cases |

All 20 planned queries were searched and no case was marked incomplete. Four explicitly unavailable groups, such as an unapproved calorie value or refund terms, are recorded separately and excluded from the 25 retrievable-group denominator; their absence must not be counted as a retriever miss. The final list missed five cases: ST1-DEV-11, 13, 14, 17 and 18. Three of those groups reached the candidate pool but were lost before the final eight; two were absent at the candidate stage. This distinguishes candidate retrieval from final selection. Final MRR was 0.85, nDCG@8 was approximately 0.658, and judged fraction among returned passages was approximately 0.394. The qrels are incomplete, so nDCG and judged fraction are descriptive only; no population precision or overall review-quality claim follows.

An independent reproduction yielded the same group counts and 20/20 search count. Its mean/median/p95-nearest-rank retrieval times were 43.879/42.951/49.670 ms; model load took 12.612 seconds and corpus embedding 1.113 seconds separately. These local serial timings exclude PostgreSQL/network, ingestion, worker and provider work. A previous run of the same cases measured 60.793 ms mean, illustrating timing variability. A separate disposable PostgreSQL parity test still matched the real pgvector and local exact-cosine rankings for NK-16 (45 synthetic CTE passages, 20 dense and seven lexical candidates); its exact test database was dropped and absence confirmed. It does not test stored production vectors, real organisation data or a deployed worker.

Run the baseline from `backend/` with `.\.venv\Scripts\python.exe -B scripts/evaluate_stage1_retrieval.py`. The Stage 1 fixes preserve the E5 model, 20/20 candidate limits, RRF settings and final top-eight limit. The original holdout scores above are historical observations; they were not used to tune these fixes or the new development labels.

## Stage 2 development comparison, 2026-11-05

The 20 development judgments and 45 eligible synthetic records are unchanged. `backend/scripts/evaluate_stage2_retrieval.py` projects only case input before retrieval, uses the same pinned real E5 model, production hybrid retrieval and simulated exact-cosine SQL, and reads labels only after ranking. The Stage 1 diagnostic entry point remains unaugmented so the prior baseline can still be reproduced. The tested opt-in `purpose_queries/1.0.0` path uses explicit nutrition/dietary, refund/return/warranty, and promotion cues to append general governing-evidence questions after the original bounded query or draft segments. At most eight total queries execute; selected evidence retains per-query provenance. Source IDs, labels and approved facts are never inserted into the planner. The worker default after the one-shot fresh holdout remains Stage 1; see the release decision below.

| Development metric | Stage 1 | Purpose queries, drafts only | Purpose queries, drafts and follow-ups |
|---|---:|---:|---:|
| Dense top-eight required groups | 21/25 | 23/25 | 25/25 |
| BM25 top-eight required groups | 20/25 | 23/25 | 25/25 |
| RRF candidate union required groups | 23/25 | 24/25 | 25/25 |
| Final selected eight required groups | 20/25 | 23/25 | 25/25 |
| Final MRR | 0.850 | 0.900 | 0.925 |
| Final nDCG@8, incomplete judgments | 0.658 | 0.737 | 0.790 |
| Selected passages explicitly judged nonrelevant | 19 | 18 | 17 |
| Searches across 20 cases | 20 | 24 | 26 |

All five previously missed available groups were present in the final eight on this development run: ST1-DEV-11 nutrition absence, 13 refund uncertainty, 14 caffeine/nutrition absence, 17 promotion approval, and 18 refund uncertainty. The four groups marked unavailable remain excluded from the 25-group denominator; retrieving a related rule does not establish an unavailable calorie, caffeine or refund fact. All bounded planned queries executed, including a separate seven-segment long-draft probe and a Unicode probe. The original draft/offsets and existing tenant, approval, effective-date and model filters were not changed.

The observed warm serial mean retrieval time in one local exact-cosine run was 38.5 ms for Stage 1 and 55.0 ms for the selected purpose-query variant; a separate run measured 40.9 and 55.6 ms. These small samples exclude PostgreSQL network, worker scheduling, generation and provider time. A Windows process working-set experiment measured about 79 MB before model loading and about 807 MB after pinned E5 loading plus 45 corpus embeddings; about 760 MB remained after the runs. This is process memory, not a measured Render worker peak. The optional pinned BGE reranker cache occupies 2.29 GB on disk; its RAM and latency were not measured, so it remains disabled.

Generic category-cap diversification was rejected: by itself it reached only 21/25 final groups with lower nDCG than Stage 1; combined with purpose queries it fell to 24/25 and lower MRR/nDCG than purpose queries alone. The final selection remains the Stage 1 rank-wise interleaving and the per-query RRF, candidate limits, E5 model/revision, and final-eight cap are unchanged. The development labels have incomplete relevance judgments, so the smaller judged-negative count is an observation, not proof of zero irrelevant evidence. No production deployment or provider-based finding-quality evaluation is claimed.

An independent disposable PostgreSQL test (`python -B -m tests.integration.live_stage2_ingestion_retrieval` from `backend/`) migrated only a random test database and exercised a real Markdown upload repository, production extraction/chunking/ingestion, pinned E5 vectors stored in pgvector, and production scoped SQL retrieval. It stored 16 passages across eight source-state/tenant variants, verified NFC/LF source spans and token bounds, and excluded pending, future, expired, archived, not-ready, wrong-model and other-tenant passages. Both decomposed-accent and Hindi queries returned only eligible current-tenant passages; category isolation passed. The exact disposable database was dropped and absence confirmed. This check used an in-memory original-object store, not HTTP/R2 or the durable worker. It verifies the stored-vector path and isolation, not deployed latency or generation quality.

## Fresh independently authored holdout and release decision

After freezing the candidate pipeline in commit `97dde3a`, a separate author created 12 new synthetic holdout cases without inspecting development/old-holdout labels, ranking outputs or experiments. The set has nine drafts (two clean, five violating, two unverified) and three explicit follow-up searches, including four Hindi inputs and a 1,626-character draft with its service claim near the end. Schema and current-source references were validated before the one-shot run. The evaluator wrote an exclusive [assessment receipt](../data/evaluation/stage2_holdout_receipt.json) with the file SHA-256 before ranking; the initial sandbox attempt failed to create this receipt and ran no rankings, then the approved local run completed once. The [holdout labels](../data/evaluation/retrieval_judgments_stage2_holdout.jsonl) were not used in runtime queries/prompts or for tuning.

| Fresh holdout metric | Frozen Stage 1 | Frozen purpose-query variant |
|---|---:|---:|
| Required groups in candidate union | 14/17 | 14/17 |
| Required groups in final eight | 14/17 | 13/17 |
| Final MRR | 0.778 | 0.778 |
| Final nDCG@8, incomplete judgments | 0.556 | 0.549 |
| Selected passages explicitly judged nonrelevant | 10 | 10 |
| Searches across 12 cases | 12 | 13 |

The purpose query recovered no additional candidate groups and displaced the `ordering_service` group from the final eight in long-draft case S2-HO-07. Both variants missed `uncertainty_rule` in S2-HO-08, `nutrition_absence` in S2-HO-09 and `dietary_information` in S2-HO-10. This is a small fictional corpus and incomplete qrel set, but it directly fails to confirm the development improvement. Holdout latency (about 97 ms Stage 1, 59 ms purpose) is not comparable: the Stage 1 variant ran first in the same cold serial process, so those numbers are not evidence of a speed gain. The development latency and Windows process-memory observations above remain the bounded resource evidence.

The release decision is conservative: the worker retains the Stage 1 query plan and final selector by default. The Stage 2 planner, provenance code, ablation scripts and tests remain available as an explicit experiment; no query template, source label, model or ranking parameter was adjusted using the fresh holdout. Disabling the experimental worker path is a release gate, not a holdout-tuned ranking change. This holdout has now been observed and must not be used as an unseen final test for future revisions. A new independently authored set and a worker-sized memory/latency run are needed before enabling purpose queries or a reranker. AI-generated finding quality is still separate and unmeasured here.
