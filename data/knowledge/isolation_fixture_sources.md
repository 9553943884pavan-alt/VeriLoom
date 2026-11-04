# Retrieval isolation fixture sources

These two synthetic records are evaluation-only inputs. Do not import them into the active Nook & Kettle knowledge base. They deliberately resemble a valid price passage so an evaluation can check whether retrieval respects organisation, status, and effective dates.

## NK-FACTS-ARCHIVE-001

- Organisation: `nook-kettle`
- Version: `1.0`
- Status: `archived`
- Applicable from: `2026-10-01`
- Applicable until: `2026-11-03` (exclusive)
- Local ID: `FACT-06`
- Synthetic historical statement: Cardamom Chai was priced at ₹99 per cup during this historical fixture period. This archived value must not answer a current-price question dated 2026-11-03 or later. The current approved price is in `NK-FACTS-001#FACT-06`.

This fixture is deliberately authored to test date/status filtering. It is not evidence that a real café ever used this price.

## CC-FACTS-001

- Organisation: `cedar-cup`
- Version: `1.0`
- Status: `approved` within this separate fictional organisation only
- Applicable from: `2026-11-03`
- Applicable until: none
- Local ID: `FACT-06`
- Synthetic statement: Cedar Cup's Cardamom Chai is priced at ₹80 per cup. Cedar Cup is a separate fictional test organisation. This price must never be used to answer a Nook & Kettle question.

Both JSONL records have `ingestible: false`. A dedicated isolation check may load them into a disposable fixture corpus; the normal ingestion manifest must exclude them.
