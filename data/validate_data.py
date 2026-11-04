"""Validate dataset contracts and references; does not run a model or the application.

Run from the workspace root: python data/validate_data.py
Dependency: jsonschema (pip install jsonschema if missing).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def main() -> int:
    errors: list[str] = []
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    datasets: dict[str, list[dict]] = {}
    counts = {}
    for entry in manifest["datasets"]:
        name = entry["name"]
        path = ROOT / entry["path"]
        schema_path = ROOT / entry["schema"]
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            errors.append(f"{name}: UTF-8 BOM present")
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            errors.append(f"{name}: manifest checksum differs; inspect change and refresh manifest")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        records = []
        for number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
            if not line.strip():
                errors.append(f"{name}:{number}: blank JSONL line")
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{name}:{number}: {exc}")
                continue
            for issue in validator.iter_errors(obj):
                errors.append(f"{name}:{number}:{list(issue.path)}: {issue.message}")
            records.append(obj)
        ids = [record.get(entry["id_field"]) for record in records]
        if len(set(ids)) != len(ids):
            errors.append(f"{name}: duplicate IDs")
        if len(records) != entry["record_count"]:
            errors.append(f"{name}: manifest count differs")
        datasets[name] = records
        counts[name] = len(records)

    approved = {r["record_id"]: r for r in datasets["approved_knowledge"]}
    isolation = {r["record_id"]: r for r in datasets["isolation_fixtures"]}
    if set(approved) & set(isolation):
        errors.append("Knowledge and isolation IDs collide")
    all_sources = approved | isolation
    for record in all_sources.values():
        rid = record["record_id"]
        if rid != record["document_id"] + "#" + record["local_id"]:
            errors.append(f"{rid}: canonical ID mismatch")
        path = (ROOT / record["source_path"]).resolve()
        if not path.is_relative_to(DATA.resolve()) or not path.is_file():
            errors.append(f"{rid}: source path missing or outside data folder")
        elif record["local_id"] not in path.read_text(encoding="utf-8-sig"):
            errors.append(f"{rid}: local ID absent from source file")
        start = date.fromisoformat(record["effective_from"])
        if record["effective_to"] and date.fromisoformat(record["effective_to"]) < start:
            errors.append(f"{rid}: invalid effective date range")
    for record in approved.values():
        if not (record["ingestible"] and record["status"] == "approved" and record["organisation_id"] == "nook-kettle"):
            errors.append(f"{record['record_id']}: invalid primary ingestion scope")
    for record in isolation.values():
        if record["ingestible"]:
            errors.append(f"{record['record_id']}: isolation record marked for normal ingestion")

    def check_sources(ids: list[str], owner: str, allowed: dict = approved) -> None:
        for rid in ids:
            if rid not in allowed:
                errors.append(f"{owner}: unknown or out-of-scope source {rid}")

    drafts = {r["case_id"]: r for r in datasets["drafts"]}
    for case in drafts.values():
        for finding in case["expected_findings"]:
            check_sources(finding["source_ids"], case["case_id"])
        if case["expected_status"] == "pass" and case["expected_findings"]:
            errors.append(f"{case['case_id']}: passing case contains expected findings")
        if case["expected_status"] == "flag" and not case["expected_findings"]:
            errors.append(f"{case['case_id']}: flagged case has no expected findings")
        asset = (case.get("image") or {}).get("asset_path")
        if asset and not (ROOT / asset).is_file():
            errors.append(f"{case['case_id']}: image asset does not exist")

    for case in datasets["followups"]:
        cid = case["case_id"]
        draft = drafts.get(case["draft_id"])
        if not draft or draft["draft_revision"] != case["draft_revision"]:
            errors.append(f"{cid}: missing draft or revision")
        elif draft["split"] != case["split"]:
            errors.append(f"{cid}: linked draft crosses development/holdout boundary")
        check_sources(case["expected_source_ids"], cid)
        check_sources(case["available_source_ids"], cid)
        if case["expected_route"] == "reuse_evidence" and not set(case["expected_source_ids"]).issubset(case["available_source_ids"]):
            errors.append(f"{cid}: reuse route lacks expected evidence")
        if case["requires_rewrite"] and not case["expected_query"]:
            errors.append(f"{cid}: rewrite required without a target query")
        if case["history"] and case["history"][-1]["text"] == case["question"]:
            errors.append(f"{cid}: current question duplicated in prior history")

    for case in datasets["scope_cases"]:
        check_sources(case["candidate_source_ids"], case["case_id"], all_sources)
        check_sources(case["expected_allowed_ids"], case["case_id"], all_sources)
        check_sources(case["expected_excluded_ids"], case["case_id"], all_sources)
        when = date.fromisoformat(case["as_of"])
        allowed = set()
        for rid in case["candidate_source_ids"]:
            if rid not in all_sources:
                continue
            r = all_sources[rid]
            if (r["organisation_id"] == case["authenticated_organisation_id"]
                and r["status"] == "approved"
                and date.fromisoformat(r["effective_from"]) <= when
                and (r["effective_to"] is None or when < date.fromisoformat(r["effective_to"]))):
                allowed.add(rid)
        if allowed != set(case["expected_allowed_ids"]):
            errors.append(f"{case['case_id']}: scope expectation disagrees with metadata")
        if set(case["candidate_source_ids"]) - allowed != set(case["expected_excluded_ids"]):
            errors.append(f"{case['case_id']}: exclusion expectation disagrees with metadata")

    for ref in datasets["public_references"]:
        if ref["ingestible"] or not ref["adoption_required"] or ref["organisation_id"] is not None:
            errors.append(f"{ref['record_id']}: public reference promoted to organisation knowledge")
    for evidence in datasets["problem_evidence"]:
        if evidence["ingestible"]:
            errors.append(f"{evidence['record_id']}: problem evidence marked for brand ingestion")
    for svg in (DATA / "brand_sources").glob("*.svg"):
        try:
            ET.parse(svg)
        except ET.ParseError as exc:
            errors.append(f"{svg.name}: invalid SVG XML: {exc}")

    report = {
        "kind": "static_dataset_validation",
        "status": "passed" if not errors else "failed",
        "counts": counts,
        "draft_status_counts": dict(Counter(r["expected_status"] for r in drafts.values())),
        "draft_split_counts": dict(Counter(r["split"] for r in drafts.values())),
        "errors": errors,
        "limitations": ["No RAG pipeline or model was executed.", "Expected answers are authored labels, not measured performance.", "SVG XML parsing does not validate visual layout.", "JSON schema validation does not establish truth of external sources."]
    }
    (DATA / "validation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
