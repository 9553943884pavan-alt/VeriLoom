"""Stage comparisons keep labels out of ranking and preserve metric meanings."""

from datetime import date
import json
from types import SimpleNamespace

import pytest

from scripts import evaluate_stage2_retrieval as evaluation


def case():
    return {"schema_version": 1, "case_id": "DEV-TEST", "split": "development",
            "organisation_id": "nook-kettle", "data_kind": "synthetic_demo",
            "input": {"kind": "draft", "text": "Café claim."}, "tags": ["clean"],
            "qrels": {"relevant": 3, "negative": 0},
            "required_evidence_groups": [{"group_id": "fact", "acceptable_source_ids": ["relevant"], "reason": "fact"}]}


def trace():
    return {"planned_queries": 2, "searched_queries": 2, "returned_truncated": False,
            "final8": ["negative", "relevant"], "searches": [
                {"dense_candidates": ["negative", "relevant"], "bm25_candidates": ["relevant"],
                 "rrf_full": ["negative", "relevant"], "candidate_union": ["relevant", "negative"]},
                {"dense_candidates": ["relevant"], "bm25_candidates": [],
                 "rrf_full": ["relevant"], "candidate_union": ["relevant"]}]}


def test_projection_contains_only_inputs_and_rejects_implicit_holdout():
    record = case()
    item = evaluation.project_input(record)
    assert not hasattr(item, "qrels") and not hasattr(item, "required_evidence_groups")
    record["qrels"] = {"invented-label": 999}
    assert evaluation.project_input(record) == item
    record["split"] = "holdout"
    with pytest.raises(ValueError, match="development"):
        evaluation.project_input(record)
    assert evaluation.project_input(record, fresh_holdout=True) == item
    record["split"] = "development"
    with pytest.raises(ValueError, match="holdout"):
        evaluation.project_input(record, fresh_holdout=True)


def test_fresh_holdout_receipt_is_exclusive_and_records_digest(tmp_path):
    path, receipt = tmp_path / "independent_fresh.jsonl", tmp_path / "receipt.json"
    path.write_text(json.dumps({**case(), "split": "holdout"}), encoding="utf-8")
    evaluation.reserve_holdout(path, receipt)
    saved = receipt.read_text(encoding="utf-8")
    assert len(json.loads(saved)["holdout_sha256"]) == 64
    with pytest.raises(FileExistsError):
        evaluation.reserve_holdout(path, receipt)
    assert receipt.read_text(encoding="utf-8") == saved
    with pytest.raises(ValueError, match="independently authored"):
        evaluation.reserve_holdout(tmp_path / "drafts.jsonl", tmp_path / "another.json")


def test_multiquery_metrics_use_cases_and_keep_union_unordered():
    report = evaluation.summarize({"DEV-TEST": trace()}, [case()])
    assert all(stage["n"] == 1 for stage in report["stages"].values())
    assert report["stages"]["candidate_union"]["group_micro_recall"] == 1
    assert report["stages"]["candidate_union"]["mrr"]["n"] == 0
    assert report["stages"]["final_at8"]["mrr"]["mean"] == 0.5
    assert report["selected_judged_nonrelevant"] == 1
    assert report["cases_with_judged_nonrelevant"] == 1
    assert report["planned_queries"] == report["searched_queries"] == 2


@pytest.mark.parametrize(("variant", "kind", "augmented"), [
    ("stage1", "draft", False), ("purpose_drafts", "draft", True),
    ("purpose_drafts", "followup_search", False), ("purpose_all", "followup_search", True),
])
def test_variant_planning_is_label_free_and_scoped(monkeypatch, variant, kind, augmented):
    def original(text, embedder):
        return [text], False
    monkeypatch.setattr(evaluation.review, "_queries", original)
    monkeypatch.setattr(evaluation, "augment_queries", lambda queries, counter: [*queries, "purpose"])
    observed = []

    def fake_trace(session, text, embedder, day):
        observed.extend(evaluation.review._queries(text, embedder)[0])
        return trace()

    monkeypatch.setattr(evaluation, "_trace", fake_trace)
    item = SimpleNamespace(text="Café claim.", kind=kind)
    evaluation.trace_variant(None, item, None, date(2026, 11, 5), variant, {})
    assert observed == ["Café claim."] + (["purpose"] if augmented else [])
    assert evaluation.review._queries is original


def test_interleave_deduplicates_and_retains_late_query_opportunity():
    assert evaluation.interleave([["a", "b", "c"], ["a", "last"]], 3) == ["a", "b", "last"]
    assert evaluation.interleave([], 8) == []
