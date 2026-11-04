# Data Collection and Quality Audit

Date: 2026-11-03

## Existing-data assessment

The first pack was useful as a narrative demonstration: it provided a fictional company, approved facts and brand rules, eight drafts, an answer key, six follow-up cases, and blank research instruments. It was not ready for direct application ingestion or reproducible evaluation. The guidance existed mainly as Markdown, draft fixtures were initially Markdown, and the follow-up data had unqualified source IDs such as `TERM-03` even though the brand guide and terminology file both use that local ID. One answer treated the word “just” as an unapproved discount even when paired with the correct price; no source rule supported that interpretation. `CLAIM-04` also needed explicit wording to avoid flagging availability at both of the brand’s two approved cafés.

## Repairs and additions

- Normalised the brand guide, facts, and terminology into 45 atomic records with document-qualified IDs, source version, status, date, organisation scope, and `ingestible` metadata.
- Clarified the availability rule and added four narrow messaging/governance rules. The brand's prices and opening hours remain aligned across facts and draft labels.
- Produced 24 structured draft cases: eight clean, 15 with expected findings, and one that needs a human because the proposed image is missing. Cases cover tone, terminology, factual and promotional claims, accessibility metadata, visual metadata, no-evidence situations, a prompt-injection attempt, and mixed issues.
- Produced 18 structured reviewer follow-up cases, including contextual rewrites, evidence reuse, fresh retrieval, an ambiguous query, missing evidence, and an unsafe instruction. There are 16 development and eight holdout drafts; linked follow-ups retain the same split.
- Added two isolated records and four scope cases for archived-source and cross-organisation leakage checks. Isolation records are excluded from normal ingestion.
- Collected nine brief, cited summaries from seven official public pages covering accessibility, writing, and visual-identity examples, plus four problem-evidence records from one enterprise survey and one public-sector content-design background document. They remain research-only and require an administrator to adopt any applicable rule.
- Added schemas, a manifest, and a static validator. Readable Markdown views remain for review; JSONL is canonical for ingestion and evaluation.

## Static verification result

`python data/validate_data.py` passed on this collection. It checked JSONL parseability and schemas, counts and hashes, unique IDs, source provenance paths, all expected citation links, draft/follow-up references, split consistency, effective-date and organisation scope expectations, and SVG XML syntax. See `validation_report.json` for the machine-readable outcome.

## Evidence limits

These examples are synthetic. No model, RAG pipeline, image-analysis system, user interview, survey, or live workflow was executed. The result proves that the files are structurally usable and internally linked; it does not prove retrieval accuracy, brand-review quality, visual compliance, WCAG conformance, or demand from real users. Public pages can change after the access date. User research and product performance should be gathered separately during the applicable event stage.

