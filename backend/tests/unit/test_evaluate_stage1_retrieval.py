"""Independent qrels/group scoring, development isolation, and label boundaries."""

from copy import deepcopy
import math

import pytest

from scripts import evaluate_stage1_retrieval as evaluation


def pack():
    cases = evaluation._read_jsonl(evaluation.JUDGMENTS)
    records = evaluation._read_jsonl(evaluation.DATA_DIR / "knowledge/records.jsonl")
    return cases, {record["record_id"] for record in records}


def test_checked_in_supplement_is_balanced_development_only_and_uses_approved_records():
    cases, source_ids = pack()
    evaluation.validate_judgments(cases, source_ids)
    assert len(cases) == 20
    assert len(source_ids) == 45
    assert sum(case["finding_correctness"]["status"] == "clean" for case in cases) == 7
    assert sum(case["finding_correctness"]["status"] == "violation" for case in cases) == 7
    assert sum(case["finding_correctness"]["status"] == "unverified" for case in cases) == 2
    assert sum(case["input"]["kind"] == "followup_search" for case in cases) == 4
    assert all(case["split"] == "development" for case in cases)
    assert any("unicode" in case["tags"] for case in cases)
    assert any(grade == 0 for case in cases for grade in case["qrels"].values())


def test_metric_gains_rank_discount_unjudged_results_and_deduplication():
    case = {"qrels": {"a": 3, "b": 2, "negative": 0}, "required_evidence_groups": [
        {"group_id": "fact", "acceptable_source_ids": ["a", "b"]},
        {"group_id": "unavailable", "acceptable_source_ids": []},
    ]}
    score = evaluation.score_ranking(["unjudged", "b", "b", "negative"], case)
    assert score["returned"] == 3
    assert score["judged_returned"] == 2
    assert score["judged_fraction"] == pytest.approx(2 / 3)
    assert score["qrel_recall"] == 0.5
    assert score["mrr"] == 0.5
    assert score["ndcg"] == pytest.approx((3 / math.log2(3)) / (7 + 3 / math.log2(3)))
    assert score["available_group_recall"] == 1
    assert score["unavailable_group_ids"] == ["unavailable"]
    assert score["complete_required_evidence"] is False


def test_groups_accept_alternatives_once_and_preserve_missing_fact_limitations():
    groups = [
        {"group_id": "rule", "acceptable_source_ids": ["a", "b"]},
        {"group_id": "fact", "acceptable_source_ids": ["c"]},
    ]
    partial = evaluation.score_groups(["a", "b", "a"], groups)
    assert partial["available_group_recall"] == 0.5
    assert partial["retrieved_groups"] == 1
    assert partial["unretrieved_group_ids"] == ["fact"]
    assert partial["complete_required_evidence"] is False
    assert evaluation.score_groups(["b", "c"], groups)["complete_required_evidence"] is True
    unavailable = evaluation.score_groups(["a"], [
        {"group_id": "unknown_fact", "acceptable_source_ids": []},
    ])
    assert unavailable["available_group_recall"] is None
    assert unavailable["retrievable_groups"] == 0
    assert unavailable["complete_required_evidence"] is False


def test_empty_and_all_zero_qrels_have_explicit_denominators():
    case = {"qrels": {"negative": 0}, "required_evidence_groups": [
        {"group_id": "missing", "acceptable_source_ids": []},
    ]}
    score = evaluation.score_ranking([], case)
    assert score["mrr"] is None
    assert score["ndcg"] is None
    assert score["qrel_recall"] is None
    assert score["judged_fraction"] is None
    aggregate = evaluation._average_metrics([score])
    assert aggregate["n"] == 1
    assert aggregate["mrr"] == {"mean": None, "n": 0}
    assert aggregate["group_micro_recall"] is None
    assert aggregate["unavailable_groups"] == 1
    with pytest.raises(ValueError, match="positive"):
        evaluation.score_ranking([], case, k=0)


@pytest.mark.parametrize("corruption", [
    "holdout", "unknown_source", "boolean_grade", "grade_out_of_range", "duplicate_case",
    "duplicate_group", "duplicate_alternative", "unjudged_alternative", "negative_alternative",
    "missing_group_reason", "unsupported_input_field", "finding_quote", "finding_status",
    "unmarked_missing_evidence",
])
def test_invalid_labels_fail_closed(corruption):
    cases, source_ids = pack()
    case = deepcopy(cases[0])
    selected = [case]
    group = case["required_evidence_groups"][0]
    if corruption == "holdout":
        case["split"] = "holdout"
    elif corruption == "unknown_source":
        case["qrels"]["OTHER#UNKNOWN"] = 3
    elif corruption == "boolean_grade":
        case["qrels"]["NK-FACTS-001#FACT-06"] = True
    elif corruption == "grade_out_of_range":
        case["qrels"]["NK-FACTS-001#FACT-06"] = 4
    elif corruption == "duplicate_case":
        selected.append(deepcopy(case))
    elif corruption == "duplicate_group":
        case["required_evidence_groups"].append(deepcopy(group))
    elif corruption == "duplicate_alternative":
        group["acceptable_source_ids"] *= 2
    elif corruption == "unjudged_alternative":
        group["acceptable_source_ids"] = ["NK-BRAND-001#VISUAL-01"]
    elif corruption == "negative_alternative":
        group["acceptable_source_ids"] = ["NK-FACTS-001#FACT-07"]
    elif corruption == "missing_group_reason":
        group["acceptable_source_ids"] = []
        group["reason"] = ""
    elif corruption == "unmarked_missing_evidence":
        group["acceptable_source_ids"] = []
    elif corruption == "unsupported_input_field":
        case["input"]["gold_rule"] = "Read the expected facts"
    elif corruption == "finding_quote":
        case["finding_correctness"] = {
            "status": "violation", "expected_findings": [
                {"category": "factual_claim", "evidence_status": "supported", "quote": "absent"},
            ],
        }
    elif corruption == "finding_status":
        case["finding_correctness"]["status"] = "violation"
    with pytest.raises(ValueError):
        evaluation.validate_judgments(selected, source_ids)


def test_finding_labels_are_never_retrieval_qrels():
    cases, _ = pack()
    case = cases[0]
    before = evaluation.score_ranking(list(case["qrels"]), case)
    case["finding_correctness"] = {"status": "violation", "expected_findings": [
        {"category": "other", "evidence_status": "supported", "quote": "Cardamom Chai"},
    ]}
    assert evaluation.score_ranking(list(case["qrels"]), case) == before
    assert not hasattr(evaluation.case_input(case), "qrels")


def test_run_ranks_all_projected_inputs_before_scoring_and_never_opens_holdout(monkeypatch):
    original_read = evaluation._read_jsonl
    expected = [evaluation.case_input(case) for case in original_read(evaluation.JUDGMENTS)]
    ranked = []
    opened = []

    def read(path):
        opened.append(path)
        assert path in {evaluation.JUDGMENTS, evaluation.DATA_DIR / "knowledge/records.jsonl"}
        return original_read(path)

    class Embedder:
        model_id = f"{evaluation.MODEL_NAME}@{evaluation.MODEL_REVISION}"

        def embed_passages(self, inputs):
            assert len(inputs) == 45
            assert all(value.startswith("passage: ") for value in inputs)
            return [[1.0] + [0.0] * 383 for _ in inputs]

    def trace(session, text, embedder, as_of):
        ranked.append(text)
        return {"final8": ["NK-FACTS-001#FACT-06"], "searches": [], "elapsed_ms": 1.0,
                "planned_queries": 1, "searched_queries": 1, "returned_truncated": False}

    original_validate = evaluation.validate_judgments

    def validate(cases, source_ids):
        assert ranked == [item.text for item in expected]
        original_validate(cases, source_ids)

    monkeypatch.setattr(evaluation, "_read_jsonl", read)
    monkeypatch.setattr(evaluation, "get_embedder", Embedder)
    monkeypatch.setattr(evaluation, "_trace", trace)
    monkeypatch.setattr(evaluation, "validate_judgments", validate)
    result = evaluation.run_evaluation()
    assert result["case_count"] == 20
    assert result["provider_calls"] == 0
    assert result["defaults_changed"] is False
    assert result["stages"]["final_at8"]["n"] == 20
    assert result["stages"]["dense_at8"]["n"] == 0
    assert result["finding_label_counts"]["clean"] == 7
    assert len(opened) == 3
