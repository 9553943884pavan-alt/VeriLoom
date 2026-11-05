"""Development-only, label-blind final-eight selection ablations.

Run from backend: python -B scripts/experiment_evidence_selection.py
This uses the unchanged Stage 1 trace and reads qrels only after rankings finish.
It never opens the existing holdout files or calls a provider/database.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Any
from unittest.mock import patch

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.jobs.handlers import review_revision as review  # noqa: E402
from app.rag.embeddings import get_embedder  # noqa: E402
from app.rag.retrieval.query_planning import augment_queries  # noqa: E402
from scripts.diagnose_retrieval import TracingSession, _trace  # noqa: E402
from scripts.evaluate_stage1_retrieval import JUDGMENTS, case_input, run_evaluation, score_ranking  # noqa: E402
from scripts.evaluate_text_mvp import DATA_DIR, _read_jsonl, eligible_knowledge  # noqa: E402


def select_with_cap(
    ranked: list[str], categories: dict[str, str], *, cap: int, limit: int = 8,
) -> list[str]:
    """Soft per-category cap, then restore RRF order for any empty slots."""
    if cap < 1 or limit < 1:
        raise ValueError("cap and limit must be positive")
    selected: list[str] = []
    counts: Counter[str] = Counter()
    for source in dict.fromkeys(ranked):
        category = categories[source]
        if counts[category] < cap:
            selected.append(source)
            counts[category] += 1
        if len(selected) == limit:
            return selected
    for source in dict.fromkeys(ranked):
        if source not in selected:
            selected.append(source)
        if len(selected) == limit:
            break
    return selected


def summarize(ranking_by_case: dict[str, list[str]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [score_ranking(ranking_by_case[case["case_id"]], case) for case in cases]
    groups = sum(score["retrievable_groups"] for score in scores)
    hits = sum(score["retrieved_groups"] for score in scores)
    return {
        "groups": f"{hits}/{groups}",
        "mrr": statistics.mean(score["mrr"] for score in scores if score["mrr"] is not None),
        "ndcg": statistics.mean(score["ndcg"] for score in scores if score["ndcg"] is not None),
        "judged_fraction": statistics.mean(score["judged_fraction"] for score in scores
                                           if score["judged_fraction"] is not None),
        "judged_zero_count": sum(
            sum(case["qrels"].get(source) == 0 for source in ranking_by_case[case["case_id"]])
            for case in cases
        ),
        "missed_cases": [case["case_id"] for case, score in zip(cases, scores, strict=True)
                         if score["unretrieved_group_ids"]],
    }


def _interleave_refs(searches: list[dict[str, Any]], *, per_query_limit: int) -> list[str]:
    """Flatten rank-wise without labels, mirroring Stage 1's multi-query order."""
    ranked = [[item["source_ref"] for item in search["rrf_candidates"][:per_query_limit]]
              for search in searches]
    selected: list[str] = []
    for result_index in range(max((len(results) for results in ranked), default=0)):
        for results in ranked:
            if result_index < len(results) and results[result_index] not in selected:
                selected.append(results[result_index])
    return selected


def _combined_traces(inputs: dict[str, Any]) -> dict[str, Any]:
    """Run purpose-augmented queries; only input text, never labels, reaches planner."""
    as_of = date(2026, 11, 5)
    records = eligible_knowledge(_read_jsonl(DATA_DIR / "knowledge/records.jsonl"), as_of)
    embedder = get_embedder()
    vectors = embedder.embed_passages(["passage: " + record["text"] for record in records])
    session = TracingSession(records, vectors, embedder.model_id, as_of)
    original = review._queries

    def planned(text: str, query_embedder: Any) -> tuple[list[str], bool]:
        base, truncated = original(text, query_embedder)
        return augment_queries(base, query_embedder), truncated

    with patch.object(review, "_queries", planned):
        return {item.case_id: _trace(session, item.text, embedder, as_of)
                for item in inputs.values()}


def run_experiment() -> dict[str, Any]:
    baseline = run_evaluation()
    inputs = baseline["results"]
    # All selections are computed without opening judgment labels.
    categories = {record["record_id"]: record["category"]
                  for record in _read_jsonl(DATA_DIR / "knowledge/records.jsonl")}
    baseline_rankings: dict[str, list[str]] = {}
    candidates: dict[str, list[str]] = {}
    for case_id, result in inputs.items():
        baseline_rankings[case_id] = result["trace"]["final8"]
        candidates[case_id] = list(dict.fromkeys(
            item["source_ref"] for search in result["trace"]["searches"]
            for item in search["rrf_candidates"]
        ))
    alternatives: dict[str, dict[str, list[str]]] = {
        f"category_soft_cap_{cap}": {
            case_id: select_with_cap(ranked, categories, cap=cap)
            for case_id, ranked in candidates.items()
        } for cap in (1, 2, 3, 4)
    }
    # The planner is independently authored by Agent A. Test the two changes
    # together after measuring each selection change against Stage 1.
    projected = [case_input(case) for case in _read_jsonl(JUDGMENTS)]
    case_inputs = {item.case_id: item for item in projected}
    combined_traces = _combined_traces(case_inputs)
    combined_baseline = {case_id: trace["final8"] for case_id, trace in combined_traces.items()}
    for per_query_limit in (8, 30):
        alternatives[f"purpose_plus_cap1_perquery{per_query_limit}"] = {
            case_id: select_with_cap(
                _interleave_refs(trace["searches"], per_query_limit=per_query_limit),
                categories, cap=1,
            )
            for case_id, trace in combined_traces.items()
        }
    cases = _read_jsonl(JUDGMENTS)
    return {
        "baseline": summarize(baseline_rankings, cases),
        "purpose_only": summarize(combined_baseline, cases),
        "alternatives": {name: summarize(rankings, cases)
                         for name, rankings in alternatives.items()},
        "detail": {case_id: {"baseline": baseline_rankings[case_id],
                             "candidate_count": len(candidates[case_id]),
                             **{name: rankings[case_id] for name, rankings in alternatives.items()}}
                   for case_id in baseline_rankings},
        "scope": "Synthetic 20-case development labels only; selection sees source categories and RRF ranks, never labels.",
    }


if __name__ == "__main__":
    print(json.dumps(run_experiment(), indent=2, ensure_ascii=True))
