"""Development Stage 2 comparisons; frozen Stage 1 baseline, no providers.

Run from backend: python -B scripts/evaluate_stage2_retrieval.py
Fresh independently authored holdout requires --fresh-holdout PATH --receipt PATH.
The receipt is created exclusively before ranking; reusing it is rejected.
Existing draft/follow-up holdout files are never opened. Gold reaches only scoring.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
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
from scripts.evaluate_stage1_retrieval import (  # noqa: E402
    JUDGMENTS, _average_metrics, case_input, score_groups, score_ranking, validate_judgments,
)
from scripts.evaluate_text_mvp import DATA_DIR, _read_jsonl, eligible_knowledge  # noqa: E402

VARIANTS = ("stage1", "purpose_drafts", "purpose_all", "category_cap1", "purpose_category_all")
STAGES = {"dense_at8": ("dense_candidates", 8), "bm25_at8": ("bm25_candidates", 8),
          "rrf_at8": ("rrf_full", 8), "rrf_candidates": ("rrf_full", 30)}


def project_input(case: dict, *, fresh_holdout: bool = False):
    """Allow holdout only through the explicit one-shot mode, without gold input."""
    expected = "holdout" if fresh_holdout else "development"
    if case.get("split") != expected:
        raise ValueError(f"Expected only {expected} cases")
    return case_input({**case, "split": "development"})


def reserve_holdout(path: Path, receipt: Path) -> None:
    """Consume a fresh assessment once, including failed attempts, before ranking."""
    if path.resolve() == JUDGMENTS.resolve() or path.name in {"drafts.jsonl", "followup_cases.jsonl"}:
        raise ValueError("Use a newly independently authored holdout file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with receipt.open("x", encoding="utf-8") as handle:
        json.dump({"holdout_sha256": digest, "status": "assessment_reserved",
                   "variants_frozen": list(VARIANTS)}, handle)


def interleave(rankings: list[list[str]], limit: int) -> list[str]:
    """Keep query opportunity equal when comparing multi-query stages."""
    output = []
    for rank in range(max((len(items) for items in rankings), default=0)):
        for items in rankings:
            if rank < len(items) and items[rank] not in output:
                output.append(items[rank])
                if len(output) == limit:
                    return output
    return output


def stage_rankings(trace: dict) -> dict[str, list[str]]:
    return {stage: interleave([search[field] for search in trace["searches"]], limit)
            for stage, (field, limit) in STAGES.items()}


def trace_variant(session, item, embedder, day, variant: str, categories: dict[str, str]) -> dict:
    """Pass only projected task input to the planner, then inspect rank outputs."""
    original = review._queries
    started = time.perf_counter()

    def plan(text, counter):
        queries, truncated = original(text, counter)
        if variant in {"purpose_all", "purpose_category_all"} or (variant == "purpose_drafts" and item.kind == "draft"):
            queries = augment_queries(queries, counter)
        return queries, truncated

    with patch.object(review, "_queries", plan):
        trace = _trace(session, item.text, embedder, day)
    if variant in {"category_cap1", "purpose_category_all"}:
        from scripts.experiment_evidence_selection import select_with_cap

        ranked = interleave([search["rrf_full"] for search in trace["searches"]], 30)
        trace["final8"] = select_with_cap(ranked, categories, cap=1)
    trace["variant_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return trace


def summarize(traces: dict[str, dict], cases: list[dict]) -> dict:
    stages: dict[str, list[dict]] = {stage: [] for stage in (*STAGES, "candidate_union", "final_at8")}
    results = {}
    for case in cases:
        trace = traces[case["case_id"]]
        per_stage = {stage: score_ranking(ranked, case, k=STAGES[stage][1])
                     for stage, ranked in stage_rankings(trace).items()}
        union = list(dict.fromkeys(ref for search in trace["searches"] for ref in search["candidate_union"]))
        per_stage["candidate_union"] = score_groups(union, case["required_evidence_groups"])
        per_stage["final_at8"] = score_ranking(trace["final8"], case)
        for stage, score in per_stage.items():
            stages[stage].append(score)
        negatives = [source for source in trace["final8"] if case["qrels"].get(source) == 0]
        results[case["case_id"]] = {"tags": case["tags"], "trace": trace, "scores": per_stage,
                                   "selected_judged_nonrelevant": negatives}
    latencies = sorted(item["variant_elapsed_ms"] for item in traces.values() if "variant_elapsed_ms" in item)
    return {"stages": {stage: _average_metrics(scores) for stage, scores in stages.items()},
            "latency_ms": {"n": len(latencies), "mean": statistics.mean(latencies) if latencies else None,
                           "median": statistics.median(latencies) if latencies else None},
            "selected_judged_nonrelevant": sum(len(item["selected_judged_nonrelevant"]) for item in results.values()),
            "cases_with_judged_nonrelevant": sum(bool(item["selected_judged_nonrelevant"]) for item in results.values()),
            "planned_queries": sum(t["planned_queries"] for t in traces.values()),
            "searched_queries": sum(t["searched_queries"] for t in traces.values()),
            "incomplete_cases": [key for key, trace in traces.items() if trace["returned_truncated"]],
            "results": results}


def run_evaluation(path: Path = JUDGMENTS, *, fresh_holdout: bool = False, receipt: Path | None = None) -> dict:
    if fresh_holdout:
        if receipt is None:
            raise ValueError("Fresh holdout requires a new receipt path")
        reserve_holdout(path, receipt)
    elif path.resolve() != JUDGMENTS.resolve():
        raise ValueError("Development comparisons use the fixed independent judgments")
    inputs = [project_input(case, fresh_holdout=fresh_holdout) for case in _read_jsonl(path)]
    day = date(2026, 11, 5)
    records = eligible_knowledge(_read_jsonl(DATA_DIR / "knowledge/records.jsonl"), day)
    embedder = get_embedder()
    vectors = embedder.embed_passages(["passage: " + record["text"] for record in records])
    categories = {record["record_id"]: record["category"] for record in records}
    traces = {}
    for variant in VARIANTS:
        session = TracingSession(records, vectors, embedder.model_id, day)
        traces[variant] = {item.case_id: trace_variant(session, item, embedder, day, variant, categories)
                           for item in inputs}
    # Execution probes have no relevance gold and do not enter quality denominators.
    probes = {}
    if not fresh_holdout:
        for name, text in {"long_draft": "Clear approved wording. " * 700 + "Final caffeine claim.",
                           "unicode": "\tCafe\u0301\u2003नमस्ते\n"}.items():
            item = replace(inputs[0], case_id=name, text=text)
            session = TracingSession(records, vectors, embedder.model_id, day)
            probes[name] = trace_variant(session, item, embedder, day, "stage1", categories)
            assert probes[name]["planned_queries"] == probes[name]["searched_queries"]
            assert all(tokens <= 512 for tokens in probes[name]["query_tokens"])
    cases = _read_jsonl(path)
    if [project_input(case, fresh_holdout=fresh_holdout) for case in cases] != inputs:
        raise ValueError("Inputs changed while ranking")
    validate_judgments([{**case, "split": "development"} for case in cases], set(categories))
    return {"scope": "Frozen Stage1 and explicit experimental variants; canonical records; simulated SQL; no provider.",
            "split": "fresh_holdout" if fresh_holdout else "development", "model": embedder.model_id,
            "case_count": len(inputs), "knowledge_count": len(records),
            "variants": {variant: summarize(value, cases) for variant, value in traces.items()},
            "execution_probes": probes, "provider_calls": 0, "defaults_changed": False,
            "denominators": "All stage metrics count cases; multi-query lists are round-robin merged before scoring.",
            "limits": "Incomplete qrels; no precision, generation, clean false-finding, abstention, or generalization claim. Category cap can reduce relevance and is experimental."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh-holdout", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_evaluation(args.fresh_holdout or JUDGMENTS,
                                    fresh_holdout=args.fresh_holdout is not None,
                                    receipt=args.receipt), indent=2, ensure_ascii=True))
