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
