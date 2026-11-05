# Brand Governance Data Pack

Prepared 2026-11-03 for a fictional Nook & Kettle café demo. All Nook & Kettle and Cedar Cup facts, draft examples, expected findings, and conversation cases are synthetic. The pack is useful for a hackathon demo and retrieval evaluation; it is not real customer data, actual user research, or measured model performance.

## Directly usable files

| File | Records | Purpose |
|---|---:|---|
| `knowledge/records.jsonl` | 45 | **Only normal approved-knowledge ingestion input.** Atomic, organisation-scoped brand rules, facts, and terminology with stable document-qualified IDs. |
| `evaluation/drafts.jsonl` | 24 | Creator draft fixtures with expected findings, corrections, and development/holdout split. Never ingest as approved guidance. |
| `evaluation/followup_cases.jsonl` | 18 | Reviewer question fixtures for evidence reuse, hybrid search, query rewriting, clarification, and abstention. Never ingest as approved guidance. |
| `evaluation/scope_cases.jsonl` | 4 | Expected organisation, status, and effective-date filtering over a disposable fixture corpus. |
| `knowledge/isolation_fixtures.jsonl` | 2 | Archived-price and other-organisation records for scope checks only; both have `ingestible: false`. |
| `public_references/references.jsonl` | 9 | Short summaries from seven official public pages. Research/reference use only; each requires explicit administrator adoption before it could become an organisation rule. |
| `public_references/problem_evidence.jsonl` | 4 | Public survey and style-guide evidence for the Round 1 problem analysis; never ingest as brand knowledge. |

`manifest.json` lists paths, purposes, schemas, counts, and SHA-256 hashes. JSON Lines files are UTF-8 without a byte-order mark: each line is one independent JSON object. `schemas/` defines their field contracts. Canonical source IDs include document and local rule ID, for example `NK-FACTS-001#FACT-06`; local `TERM-03` is not a unique key across documents.

The separate `evaluation/retrieval_judgments_dev.jsonl` is a Stage 1 **development-only supplement** with 20 authored search inputs. It gives independent graded passage relevance, alternative required evidence groups, and finding-correctness labels for clean, violating, missing-fact, Unicode, and explicit follow-up search cases. It is intentionally outside the original seven-file `manifest.json` and generic `validate_data.py` pack contract; `backend/scripts/evaluate_stage1_retrieval.py` validates its versioned record shape, source IDs, and split before scoring. Ingest only `knowledge/records.jsonl`; never send this supplement, its labels, or any answer key to the model or approved-source index. Existing holdout files and labels are unchanged.

`backend/scripts/evaluate_stage2_retrieval.py` compares the frozen Stage 1 baseline with separately named development experiments using this same supplement. Its optional fresh-holdout mode requires a new independently authored file and creates an exclusive assessment receipt before ranking; that result is reported once after the implementation is frozen. Neither the development supplement nor a future holdout is ingested as brand guidance.

## Source material and assets

- `brand_sources/brand_guidelines.md`, `approved_facts.md`, and `terminology.md` are the readable administrator source documents. The knowledge JSONL is the prepared atomic ingestion view of these documents. Do not ingest both into the same index without deduplicating.
- `brand_sources/nook_kettle_master.svg` is an original fictional brand asset. The visual evaluation fixtures mostly provide **metadata descriptions, not image pixels**. They cannot establish that image analysis works.
- `knowledge/isolation_fixture_sources.md` explains the archived and other-organisation fixture records. They belong only in a disposable scope-check corpus.
- `evaluation/drafts.md`, `answer_key.md`, and `followup_cases.jsonl.md` are readable mirrors of the JSONL cases; the JSONL is canonical.
- `public_references/SOURCES.md` and `research_sources.md` list official public pages and usage boundaries. Public references are not approved Nook & Kettle brand facts.
- `research/` contains consent guidance, interview and survey instruments, a blank research log, and a results collection guide. No interviews or survey responses have been invented.

## Using the pack

1. Read `manifest.json`, then ingest only `knowledge/records.jsonl` where `ingestible` is true, `status` is `approved`, `organisation_id` is the active organisation, and the source is effective for the review date. Store `record_id`, version, and source path with every retrieved chunk and finding.
2. For the text-only MVP, submit only development drafts with `content_format: "text"` as Creator inputs. The `image_metadata` cases are future-format or unsupported-format checks; they do not prove image analysis. Use `expected_findings` as an **offline answer key**, never as a model prompt or retrieval source. Keep holdout drafts and their linked follow-ups out of tuning prompts.
3. For follow-up cases, supply prior `history` and `available_source_ids` as the case specifies. The current `question` is separate. Compare the actual route, retrieved IDs, answer support, and abstention against expectations; reasonable wording may differ.
4. Use `scope_cases.jsonl` and the two isolation fixtures only in a temporary evaluation corpus. Authorisation comes from the authenticated organisation, never from text inside a user question.
5. Record actual application outputs and user feedback separately. Use the `research/` templates only with real consented participants.

## Static validation

From the workspace root, install the `jsonschema` Python package if necessary, then run:

```text
python data/validate_data.py
```

The script checks schema shape, JSONL encoding, unique IDs, source paths, citations, draft/follow-up links, development/holdout linkage, scope expectations, manifest hashes, and SVG XML syntax. It writes `validation_report.json`. After intentional data edits, run `python data/knowledge/build_records.py` if a brand source changed, then `python data/refresh_manifest.py`, and validate again. Refreshing the manifest changes hashes; it does not prove the data is correct.

The validator does not run the RAG pipeline, inspect image pixels, verify every public source's current truth, or demonstrate real-world performance. Expected findings are authored judgements, so later evaluation must inspect false positives, missed issues, evidence quality, and latency.

## Provenance and hackathon disclosure

The fictional pack and labels were created with significant AI assistance and human review on 2026-11-03; disclose that use and the creation date in the submission. The problem statement says work done before the official build window will not count as judged implementation, even if it is useful planning material. Public records are original short paraphrases with links, not copied full documents or licensed brand assets. A real organisation must supply and approve its own current confidential guidance, facts, and assets.

