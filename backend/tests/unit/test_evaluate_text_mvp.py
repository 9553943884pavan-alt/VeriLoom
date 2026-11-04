"""Focused tests for the offline synthetic text retrieval evaluation."""

from datetime import date
import json
from pathlib import Path

import pytest

from scripts.evaluate_text_mvp import (
    EvaluationDataError,
    eligible_knowledge,
    run_evaluation,
)


def record(record_id="A#1", **overrides):
    value = {
        "record_id": record_id,
        "organisation_id": "nook-kettle",
        "data_kind": "synthetic_demo",
        "ingestible": True,
        "status": "approved",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "text": "Cardamom Chai costs 120 rupees.",
    }
    return value | overrides


def draft(case_id="D-1", *, split="development", content="Cardamom Chai costs 120 rupees.", source_ids=None):
    findings = []
    if source_ids:
        findings = [{"source_ids": source_ids}]
    return {
        "case_id": case_id,
        "organisation_id": "nook-kettle",
        "data_kind": "synthetic_demo",
        "split": split,
        "content_format": "text",
        "content": content,
        "expected_findings": findings,
    }


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_eligibility_filters_org_status_ingestible_and_dates():
    rows = [
        record(),
        record("B#1", organisation_id="other"),
        record("C#1", status="archived"),
        record("D#1", ingestible=False),
        record("E#1", effective_from="2026-12-01"),
        record("F#1", effective_to="2026-11-04"),
    ]
    assert [row["record_id"] for row in eligible_knowledge(rows, date(2026, 11, 4))] == ["A#1"]


def test_report_scores_dev_and_holdout_separately_without_scoring_pass_cases(tmp_path):
    write_jsonl(tmp_path / "knowledge" / "records.jsonl", [
        record("NK#FACT-1", text="Cardamom Chai costs 120 rupees."),
        record("NK#FACT-2", text="The cafe is closed Monday."),
    ])
    write_jsonl(tmp_path / "evaluation" / "drafts.jsonl", [
        draft("DEV-1", source_ids=["NK#FACT-1"]),
        draft("DEV-PASS", content="The café is welcoming."),
        draft("HOLD-1", split="holdout", content="Cardamom Chai costs 120 rupees.", source_ids=["NK#FACT-1"]),
    ])

    report = run_evaluation(data_dir=tmp_path, as_of=date(2026, 11, 4), repeats=2)

    assert report["provider_calls"] == 0
    assert report["eligible_knowledge_count"] == 2
    assert report["splits"]["development"]["draft_count"] == 2
    assert report["splits"]["development"]["scoreable_case_count"] == 1
    assert report["splits"]["development"]["unscored_no_expected_sources"] == 1
    assert report["splits"]["development"]["retrieval"]["mean_recall_at_5"] == 1.0
    assert report["splits"]["holdout"]["draft_count"] == 1
    assert report["splits"]["holdout"]["retrieval"]["mean_mrr"] == 1.0
    assert report["splits"]["holdout"]["latency_ms"]["queries_measured"] == 2
    assert "not a measurement of the dense or hybrid production retriever" in report["limitations"][1]


def test_answer_key_source_ids_are_metrics_only_and_must_exist_in_eligible_corpus(tmp_path):
    write_jsonl(tmp_path / "knowledge" / "records.jsonl", [record()])
    write_jsonl(tmp_path / "evaluation" / "drafts.jsonl", [draft(source_ids=["UNKNOWN#1"])])
    with pytest.raises(EvaluationDataError, match="unknown approved source IDs"):
        run_evaluation(data_dir=tmp_path, as_of=date(2026, 11, 4))


def test_rejects_missing_eligible_knowledge_and_invalid_repeat_count(tmp_path):
    write_jsonl(tmp_path / "knowledge" / "records.jsonl", [record(effective_from="2027-01-01")])
    write_jsonl(tmp_path / "evaluation" / "drafts.jsonl", [draft()])
    with pytest.raises(EvaluationDataError, match="No approved, effective"):
        run_evaluation(data_dir=tmp_path, as_of=date(2026, 11, 4))
    with pytest.raises(ValueError, match="repeats"):
        run_evaluation(data_dir=tmp_path, repeats=0)
