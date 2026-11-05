"""Development-only Stage 1 retrieval baseline; no provider or database access.

Run from backend: python -B scripts/evaluate_stage1_retrieval.py
Inputs and labels live outside approved knowledge. Only case input text reaches
the production query planner/retriever; judgements are validated and scored after
every ranking completes. This measures canonical atomic records, not ingestion,
stored vectors, live SQL isolation, routing, generation, or semantic correctness.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.rag.embeddings import MODEL_NAME, MODEL_REVISION, get_embedder  # noqa: E402
from scripts.diagnose_retrieval import TracingSession, _trace, _vector_summary  # noqa: E402
from scripts.evaluate_text_mvp import DATA_DIR, _read_jsonl, eligible_knowledge  # noqa: E402

JUDGMENTS = DATA_DIR / "evaluation/retrieval_judgments_dev.jsonl"


@dataclass(frozen=True)
class CaseInput:
    case_id: str
    kind: str
    text: str
    tags: tuple[str, ...]


def case_input(case: dict[str, Any]) -> CaseInput:
    """Project only task inputs, rejecting holdout and unsupported input kinds."""
    if (case.get("schema_version") != 1 or case.get("split") != "development"
            or case.get("organisation_id") != "nook-kettle"
            or case.get("data_kind") != "synthetic_demo"):
        raise ValueError("Only version-1 synthetic Nook & Kettle development cases are allowed")
    value = case.get("input", {})
    if (set(value) != {"kind", "text"} or value["kind"] not in {"draft", "followup_search"}
            or not isinstance(value["text"], str) or not value["text"].strip()):
        raise ValueError("Case input requires only a supported kind and nonempty text")
    if not isinstance(case.get("case_id"), str) or not case["case_id"].strip():
        raise ValueError("Case ID is required")
    tags = case.get("tags")
    if (not isinstance(tags, list) or not tags
            or any(not isinstance(tag, str) or not tag for tag in tags)
            or len(tags) != len(set(tags))):
        raise ValueError("Unique nonempty tags are required")
    return CaseInput(case["case_id"], value["kind"], value["text"], tuple(tags))


def validate_judgments(cases: list[dict[str, Any]], source_ids: set[str]) -> None:
    """Validate external labels independently of ranking and finding labels."""
    seen: set[str] = set()
    required = {"schema_version", "case_id", "split", "organisation_id", "data_kind",
                "input", "tags", "qrels", "required_evidence_groups",
                "finding_correctness", "judgment_note"}
    for case in cases:
        item = case_input(case)
        if set(case) != required or item.case_id in seen:
            raise ValueError("Cases require unique IDs and the exact judgment fields")
        seen.add(item.case_id)
        if not isinstance(case["judgment_note"], str) or not case["judgment_note"].strip():
            raise ValueError("A judgment scope note is required")
        qrels = case["qrels"]
        if not isinstance(qrels, dict) or not qrels:
            raise ValueError("Independent graded passage qrels are required")
        if any(source not in source_ids or type(grade) is not int or grade not in range(4)
               for source, grade in qrels.items()):
            raise ValueError("Qrels require eligible source IDs and integer grades 0..3")
        groups = case["required_evidence_groups"]
        if not isinstance(groups, list) or not groups:
            raise ValueError("Required evidence groups must be explicit")
        group_ids: set[str] = set()
        for group in groups:
            if (set(group) != {"group_id", "acceptable_source_ids", "reason"}
                    or not isinstance(group["group_id"], str) or not group["group_id"]
                    or group["group_id"] in group_ids
                    or not isinstance(group["reason"], str) or not group["reason"].strip()):
                raise ValueError("Evidence groups require unique IDs, alternatives, and a reason")
            group_ids.add(group["group_id"])
            alternatives = group["acceptable_source_ids"]
            if (not isinstance(alternatives, list) or len(alternatives) != len(set(alternatives))
                    or any(source not in source_ids or qrels.get(source, 0) <= 0
                           for source in alternatives)):
                raise ValueError("Evidence alternatives must have independent positive qrels")
            if not alternatives and "missing_evidence" not in item.tags:
                raise ValueError("Unavailable evidence groups require an explicit missing-evidence tag")
        labels = case["finding_correctness"]
        if (not isinstance(labels, dict) or set(labels) != {"status", "expected_findings"}
                or labels["status"] not in {"clean", "violation", "unverified", "not_applicable"}
                or not isinstance(labels["expected_findings"], list)):
            raise ValueError("Separate finding correctness labels are required")
        findings = labels["expected_findings"]
        if (labels["status"] in {"clean", "not_applicable"}) != (not findings):
            raise ValueError("Finding status disagrees with expected findings")
        if (item.kind == "followup_search") != (labels["status"] == "not_applicable"):
            raise ValueError("Explicit search questions have no initial-review finding labels")
        for finding in findings:
            if (set(finding) != {"category", "evidence_status", "quote"}
                    or finding["category"] not in {"tone", "terminology", "factual_claim",
                                                  "promotional_claim", "accessibility", "other"}
                    or finding["evidence_status"] not in {"supported", "insufficient"}
                    or not finding["quote"] or finding["quote"] not in item.text):
                raise ValueError("Finding labels require valid categories and exact draft quotes")
        expected_status = "insufficient" if labels["status"] == "unverified" else "supported"
        if findings and any(finding["evidence_status"] != expected_status for finding in findings):
            raise ValueError("Finding evidence status disagrees with correctness label")


def score_groups(ranking: list[str], groups: list[dict[str, Any]]) -> dict[str, Any]:
    """OR within an evidence group, AND across groups; absent facts stay absent."""
    retrieved = set(ranking)
    available = [group for group in groups if group["acceptable_source_ids"]]
    missing = [group["group_id"] for group in groups if not group["acceptable_source_ids"]]
    hits = [group["group_id"] for group in available
            if retrieved.intersection(group["acceptable_source_ids"])]
    return {
        "retrievable_groups": len(available), "retrieved_groups": len(hits),
        "retrieved_group_ids": hits, "unavailable_group_ids": missing,
        "unretrieved_group_ids": [group["group_id"] for group in available
                                  if group["group_id"] not in hits],
        "available_group_recall": len(hits) / len(available) if available else None,
        "complete_required_evidence": not missing and len(hits) == len(available),
    }


def score_ranking(ranking: list[str], case: dict[str, Any], *, k: int = 8) -> dict[str, Any]:
    """Incomplete-judgment MRR/nDCG: unjudged passages get zero gain, not a verdict."""
    if k < 1:
        raise ValueError("k must be positive")
    ranked = list(dict.fromkeys(ranking))[:k]
    qrels = case["qrels"]
    positives = {source for source, grade in qrels.items() if grade > 0}
    gains = [qrels.get(source, 0) for source in ranked]
    ideal = sorted(qrels.values(), reverse=True)[:k]

    def dcg(grades: list[int]) -> float:
        return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))

    ideal_dcg = dcg(ideal)
    return {
        "k": k, "returned": len(ranked), "judged_returned": sum(source in qrels for source in ranked),
        "judged_fraction": sum(source in qrels for source in ranked) / len(ranked) if ranked else None,
        "positive_qrels": len(positives),
        "qrel_recall": len(positives.intersection(ranked)) / len(positives) if positives else None,
        "mrr": next((1 / index for index, grade in enumerate(gains, 1) if grade > 0), 0.0)
        if positives else None,
        "ndcg": dcg(gains) / ideal_dcg if ideal_dcg else None,
        **score_groups(ranked, case["required_evidence_groups"]),
    }


def _average_metrics(scores: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"n": len(scores)}
    for name in ("qrel_recall", "mrr", "ndcg", "judged_fraction", "available_group_recall"):
        values = [score[name] for score in scores if score.get(name) is not None]
        result[name] = {"mean": statistics.mean(values) if values else None, "n": len(values)}
    total = sum(score["retrievable_groups"] for score in scores)
    hits = sum(score["retrieved_groups"] for score in scores)
    result["group_micro_recall"] = hits / total if total else None
    result["retrievable_groups"] = total
    result["retrieved_groups"] = hits
    result["unavailable_groups"] = sum(len(score["unavailable_group_ids"]) for score in scores)
    return result


def run_evaluation(as_of: date = date(2026, 11, 5)) -> dict[str, Any]:
    records = eligible_knowledge(_read_jsonl(DATA_DIR / "knowledge/records.jsonl"), as_of)
    if len(records) != 45:
        raise ValueError("Stage 1 baseline requires the unchanged 45 approved atomic records")
    # Discard labels on the input projection; no existing draft/holdout files are opened.
    inputs = [case_input(case) for case in _read_jsonl(JUDGMENTS)]
    if not inputs or len({item.case_id for item in inputs}) != len(inputs):
        raise ValueError("Unique development inputs are required")
    started = time.perf_counter()
    embedder = get_embedder()
    if embedder.model_id != f"{MODEL_NAME}@{MODEL_REVISION}":
        raise ValueError("Stage 1 requires the pinned production E5 adapter")
    load_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    vectors = embedder.embed_passages(["passage: " + record["text"] for record in records])
    vector_summary = _vector_summary(vectors)
    embedding_ms = (time.perf_counter() - started) * 1000
    session = TracingSession(records, vectors, embedder.model_id, as_of)
    # Explicit follow-up search inputs exercise retrieval, never route/rewrite selection.
    traces = {item.case_id: _trace(session, item.text, embedder, as_of) for item in inputs}

    # Scoring starts only after every production ranking has completed.
    cases = _read_jsonl(JUDGMENTS)
    validate_judgments(cases, {record["record_id"] for record in records})
    if [case_input(case) for case in cases] != inputs:
        raise ValueError("Evaluation inputs changed while ranking")
    results: dict[str, Any] = {}
    stages: dict[str, list[dict[str, Any]]] = {stage: [] for stage in (
        "dense_at8", "bm25_at8", "rrf_at8", "rrf_candidates", "candidate_union", "final_at8",
    )}
    for case in cases:
        trace = traces[case["case_id"]]
        candidate_union = list(dict.fromkeys(source for search in trace["searches"]
                                            for source in search["candidate_union"]))
        candidate_score = score_groups(candidate_union, case["required_evidence_groups"])
        stages["candidate_union"].append(candidate_score)
        final_score = score_ranking(trace["final8"], case)
        stages["final_at8"].append(final_score)
        per_search = []
        for search in trace["searches"]:
            query_scores = {}
            for stage, field, k in (("dense_at8", "dense_candidates", 8),
                                    ("bm25_at8", "bm25_candidates", 8),
                                    ("rrf_at8", "rrf_full", 8),
                                    ("rrf_candidates", "rrf_full", 30)):
                score = score_ranking(search[field], case, k=k)
                stages[stage].append(score)
                query_scores[stage] = score
            per_search.append(query_scores)
        results[case["case_id"]] = {
            "input_kind": case["input"]["kind"], "tags": case["tags"],
            "trace": trace, "candidate_union": candidate_score, "final": final_score,
            "query_stage_scores": per_search,
            "offline_finding_correctness": case["finding_correctness"],
        }
    latencies = sorted(trace["elapsed_ms"] for trace in traces.values())
    return {
        "scope": "Development-only canonical-record retrieval; production Python planner/BM25/RRF/final selection; simulated exact-cosine SQL.",
        "as_of": as_of.isoformat(), "model": embedder.model_id,
        "eligible_knowledge_count": len(records), "passage_vectors": vector_summary,
        "case_count": len(inputs), "input_kind_counts": dict(Counter(item.kind for item in inputs)),
        "finding_label_counts": dict(Counter(case["finding_correctness"]["status"] for case in cases)),
        "tag_counts": dict(Counter(tag for item in inputs for tag in item.tags)),
        "graded_qrels": "0=judged nonrelevant, 1=contextual, 2=useful, 3=direct; absent=unjudged",
        "metric_limits": "MRR/nDCG use incomplete qrels and zero gain for unjudged results; no relevance precision claim. Group recall excludes explicitly unavailable fact groups.",
        "stage_denominators": "dense/BM25/RRF stages count searched queries; candidate union/final count cases",
        "stages": {stage: _average_metrics(scores) for stage, scores in stages.items()},
        "planned_queries": sum(trace["planned_queries"] for trace in traces.values()),
        "searched_queries": sum(trace["searched_queries"] for trace in traces.values()),
        "incomplete_cases": [key for key, trace in traces.items() if trace["returned_truncated"]],
        "dense_sql_branch_calls": session.dense_queries,
        "latency_ms": {"n": len(latencies), "mean": statistics.mean(latencies),
                       "median": statistics.median(latencies),
                       "p95_nearest_rank": latencies[math.ceil(0.95 * len(latencies)) - 1],
                       "model_load": load_ms, "passage_embedding": embedding_ms},
        "latency_scope": "One serial pass; per-case E5 query, simulated cosine, BM25, RRF, selection. Model load and corpus embedding separate; excludes DB, worker, provider.",
        "not_measured": ["finding correctness/clean false findings", "answerability/abstention behavior",
                         "follow-up routing/rewriting", "upload ingestion/stored-vector consistency",
                         "live SQL/authorization/ANN recall", "representative multilingual quality",
                         "holdout/generalization", "reranker benefit/Render capacity"],
        "provider_calls": 0, "defaults_changed": False, "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(run_evaluation(), indent=2, ensure_ascii=True))
