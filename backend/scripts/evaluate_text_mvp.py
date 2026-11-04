"""Run an offline lexical retrieval baseline on synthetic text draft fixtures.

From ``backend`` run ``python scripts/evaluate_text_mvp.py``. The script reads
only the approved-knowledge seed and evaluation drafts. It never sends fixture
labels to retrieval or to an external provider. Findings and answer generation
are not measured by this retrieval-only baseline.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Sequence

from rank_bm25 import BM25Plus

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.rag.retrieval.hybrid import tokenize  # noqa: E402


ROOT = BACKEND_DIR.parent
DATA_DIR = ROOT / "data"
DEFAULT_TOP_K = (5, 8)


class EvaluationDataError(ValueError):
    """The fixture inputs cannot support a trustworthy evaluation."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError(f"Could not read evaluation data: {path.name}.") from exc


def eligible_knowledge(records: Sequence[dict[str, Any]], as_of: date) -> list[dict[str, Any]]:
    """Apply the demo organisation, approval, ingestibility, and date gates."""
    return [
        record
        for record in records
        if record.get("organisation_id") == "nook-kettle"
        and record.get("data_kind") == "synthetic_demo"
        and record.get("ingestible") is True
        and record.get("status") == "approved"
        and date.fromisoformat(record["effective_from"]) <= as_of
        and (
            record.get("effective_to") is None
            or as_of < date.fromisoformat(record["effective_to"])
        )
    ]


def _rank_bm25(index: BM25Plus, records: Sequence[dict[str, Any]], query: str, limit: int) -> list[str]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    scores = index.get_scores(query_tokens)
    query_terms = set(query_tokens)
    ranked = sorted(
        range(len(records)),
        key=lambda i: (-float(scores[i]), records[i]["record_id"]),
    )
    return [
        records[i]["record_id"]
        for i in ranked
        if query_terms.intersection(tokenize(records[i]["text"]))
    ][:limit]


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _score_split(
    split: str,
    cases: Sequence[dict[str, Any]],
    rankings: dict[str, list[str]],
    latencies_ms: dict[str, list[float]],
    knowledge_ids: set[str],
) -> dict[str, Any]:
    per_case: list[dict[str, Any]] = []
    for case in cases:
        # Read answer-key fields only after retrieval has completed. They never
        # influence query text, corpus eligibility, or rank order.
        expected_ids = {
            source_id
            for finding in case["expected_findings"]
            for source_id in finding["source_ids"]
        }
        unknown = expected_ids - knowledge_ids
        if unknown:
            raise EvaluationDataError(
                f"{case['case_id']} references unknown approved source IDs."
            )
        if not expected_ids:
            continue

        ranked_ids = rankings[case["case_id"]]
        result: dict[str, Any] = {"case_id": case["case_id"], "expected_source_count": len(expected_ids)}
        for k in DEFAULT_TOP_K:
            selected = ranked_ids[:k]
            hits = len(expected_ids.intersection(selected))
            result[f"recall_at_{k}"] = hits / len(expected_ids)
            result[f"precision_at_{k}"] = hits / k
        result["mrr"] = next(
            (1.0 / rank for rank, record_id in enumerate(ranked_ids, 1) if record_id in expected_ids),
            0.0,
        )
        result["retrieval_latency_ms"] = round(statistics.mean(latencies_ms[case["case_id"]]), 3)
        per_case.append(result)

    if not per_case:
        return {
            "split": split,
            "draft_count": len(cases),
            "scoreable_case_count": 0,
            "unscored_no_expected_sources": len(cases),
            "retrieval": None,
            "latency_ms": None,
            "cases": [],
        }

    def mean(key: str) -> float:
        return round(statistics.mean(case[key] for case in per_case), 4)

    all_query_latencies = [value for case in cases for value in latencies_ms[case["case_id"]]]
    return {
        "split": split,
        "draft_count": len(cases),
        "scoreable_case_count": len(per_case),
        "unscored_no_expected_sources": len(cases) - len(per_case),
        "retrieval": {
            "mean_recall_at_5": mean("recall_at_5"),
            "mean_recall_at_8": mean("recall_at_8"),
            "mean_precision_at_5": mean("precision_at_5"),
            "mean_precision_at_8": mean("precision_at_8"),
            "mean_mrr": mean("mrr"),
        },
        "latency_ms": {
            "queries_measured": len(all_query_latencies),
            "repeats_per_case": len(all_query_latencies) // len(cases),
            "mean": round(statistics.mean(all_query_latencies), 3),
            "p50": _percentile(all_query_latencies, 0.50),
            "p95": _percentile(all_query_latencies, 0.95),
        },
        "cases": per_case,
    }


def run_evaluation(
    *, data_dir: Path = DATA_DIR, as_of: date | None = None, repeats: int = 3
) -> dict[str, Any]:
    """Evaluate text-only synthetic drafts with BM25; labels are metric-only."""
    if repeats < 1 or repeats > 100:
        raise ValueError("repeats must be between 1 and 100")
    evaluation_date = as_of or date.today()
    knowledge = eligible_knowledge(_read_jsonl(data_dir / "knowledge" / "records.jsonl"), evaluation_date)
    if not knowledge:
        raise EvaluationDataError("No approved, effective nook-kettle knowledge records are eligible.")
    cases = [
        case
        for case in _read_jsonl(data_dir / "evaluation" / "drafts.jsonl")
        if case.get("organisation_id") == "nook-kettle"
        and case.get("data_kind") == "synthetic_demo"
        and case.get("content_format") == "text"
        and case.get("split") in {"development", "holdout"}
    ]
    if not cases:
        raise EvaluationDataError("No eligible text-only development or holdout drafts were found.")
    if any(not isinstance(case.get("content"), str) or not case["content"].strip() for case in cases):
        raise EvaluationDataError("A text-only draft has empty or invalid content.")

    tokenized_corpus = [tokenize(record["text"]) for record in knowledge]
    if not any(tokenized_corpus):
        raise EvaluationDataError("Eligible knowledge contains no searchable text.")
    build_started = time.perf_counter()
    index = BM25Plus(tokenized_corpus)
    index_build_ms = (time.perf_counter() - build_started) * 1000

    # Retrieval is completed using draft content only. The expected answer key
    # is not read until rankings and timings have been collected.
    rankings: dict[str, list[str]] = {}
    latencies_ms: dict[str, list[float]] = {}
    for case in cases:
        case_id = case["case_id"]
        rankings[case_id] = []
        latencies_ms[case_id] = []
        for _ in range(repeats):
            started = time.perf_counter()
            ranked_ids = _rank_bm25(index, knowledge, case["content"], limit=8)
            latencies_ms[case_id].append((time.perf_counter() - started) * 1000)
            if not rankings[case_id]:
                rankings[case_id] = ranked_ids
            elif rankings[case_id] != ranked_ids:
                raise EvaluationDataError("Deterministic BM25 baseline returned inconsistent rankings.")

    knowledge_ids = {record["record_id"] for record in knowledge}
    by_split = {
        split: _score_split(
            split,
            [case for case in cases if case["split"] == split],
            rankings,
            latencies_ms,
            knowledge_ids,
        )
        for split in ("development", "holdout")
    }
    return {
        "dataset": "synthetic_demo_text_drafts",
        "as_of": evaluation_date.isoformat(),
        "eligible_knowledge_count": len(knowledge),
        "eligible_text_draft_count": len(cases),
        "retrieval_baseline": "BM25Plus lexical ranking over the whole draft text; top 8",
        "query_strategy": "whole draft content; no claim decomposition or embedding",
        "repeats_per_case": repeats,
        "index_build_latency_ms": round(index_build_ms, 3),
        "provider_calls": 0,
        "splits": by_split,
        "not_measured": [
            "finding precision/recall, anchor validity, answer support, citations, and end-to-end review latency",
            "cases with no expected source IDs are unscored for source retrieval; this includes clean-pass drafts",
        ],
        "limitations": [
            "Synthetic fixture labels are used only after ranking to calculate metrics, never as retrieval input.",
            "This is a lexical retrieval baseline, not a measurement of the dense or hybrid production retriever.",
            "Per-query latency is a local microbenchmark and excludes database, embedding, provider, and worker overhead.",
            "Holdout results are reported for final evaluation and must not be used to tune the implementation.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    report = run_evaluation(data_dir=args.data_dir, as_of=args.as_of, repeats=args.repeats)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
