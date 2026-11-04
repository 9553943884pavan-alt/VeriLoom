"""Refresh record counts and hashes after intentional dataset edits.

Run python data/validate_data.py afterwards; refreshing hashes alone is not validation.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS = [
    ("approved_knowledge", "knowledge/records.jsonl", "knowledge", "record_id", "approved_demo_ingestion"),
    ("isolation_fixtures", "knowledge/isolation_fixtures.jsonl", "knowledge", "record_id", "evaluation_only"),
    ("drafts", "evaluation/drafts.jsonl", "draft", "case_id", "evaluation_only"),
    ("followups", "evaluation/followup_cases.jsonl", "followup", "case_id", "evaluation_only"),
    ("scope_cases", "evaluation/scope_cases.jsonl", "scope_case", "case_id", "evaluation_only"),
    ("public_references", "public_references/references.jsonl", "public_reference", "record_id", "reference_only_requires_adoption"),
    ("problem_evidence", "public_references/problem_evidence.jsonl", "problem_evidence", "record_id", "research_only"),
]


def main():
    datasets = []
    for name, relative, schema, id_field, purpose in SPECS:
        raw = (ROOT / "data" / relative).read_bytes()
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        datasets.append({"name": name, "path": "data/" + relative,
                         "schema": f"data/schemas/{schema}.schema.json",
                         "id_field": id_field, "purpose": purpose,
                         "record_count": len(rows), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {"dataset_version": "2.0", "prepared_on": "2026-11-03",
                "encoding": "UTF-8 without BOM", "format": "JSON Lines",
                "normal_ingestion_allowlist": ["data/knowledge/records.jsonl"],
                "synthetic_data_disclosure": "Brand facts and evaluation labels are AI-assisted fictional demo material, reviewed for internal consistency. They are not customer research or measured model results.",
                "datasets": datasets}
    (ROOT / "data/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Manifest refreshed; run python data/validate_data.py")


if __name__ == "__main__":
    main()
