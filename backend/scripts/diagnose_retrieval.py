"""Trace development-only retrieval with cached pinned E5 and no provider or DB.

Run from backend: python scripts/diagnose_retrieval.py [--experiments]
Production Python ranking is observed, not replaced. Its SQL boundary is the
existing exact-cosine SyntheticSession, so this does not verify PostgreSQL or
stored production vectors. Six of seven development text cases have no expected
findings: the remaining n=1 case and optional illustrative ablations cannot
establish general retrieval, multilingual, answer, or production quality.
Holdout cases are never ranked or scored. Gold fields are read after all ranking.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import UUID

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.jobs.handlers import review_revision as review  # noqa: E402
from app.rag.embeddings import (  # noqa: E402
    EMBEDDING_DIMENSION, MODEL_NAME, MODEL_REVISION, get_embedder,
)
from app.rag.retrieval import hybrid  # noqa: E402
from scripts.evaluate_hybrid_mvp import ORG, SyntheticSession  # noqa: E402
from scripts.evaluate_text_mvp import DATA_DIR, _read_jsonl, eligible_knowledge  # noqa: E402


class TracingSession(SyntheticSession):
    """Observe simulated SQL vectors and full cosine order before SQL LIMIT."""

    def execute(self, statement: Any, params: dict[str, Any]) -> Any:
        if "ORDER BY p.embedding <=>" in str(statement):
            vector = json.loads(params["embedding"])
            norm = math.sqrt(sum(value * value for value in vector))
            if len(vector) != EMBEDDING_DIMENSION or not math.isfinite(norm) or not 0.999 <= norm <= 1.001:
                raise ValueError("Invalid query vector at the simulated SQL boundary")

            def cosine(row: dict[str, Any]) -> float:
                other = row["vector"]
                return sum(a * b for a, b in zip(other, vector, strict=True)) / (
                    sum(a * a for a in other) * sum(b * b for b in vector)
                ) ** 0.5

            ordered = sorted(self.rows, key=lambda row: (-cosine(row), str(row["id"])))
            self.full_dense = [str(row["id"]) for row in ordered]
            self.query_vector_norm = norm
        return super().execute(statement, params)


def _vector_summary(vectors: list[list[float]]) -> dict[str, Any]:
    norms = [math.sqrt(sum(value * value for value in vector)) for vector in vectors]
    if any(len(vector) != EMBEDDING_DIMENSION for vector in vectors):
        raise ValueError("Incorrect passage vector dimensions")
    if not all(math.isfinite(value) for vector in vectors for value in vector):
        raise ValueError("Nonfinite passage vector")
    if not all(0.999 <= norm <= 1.001 for norm in norms):
        raise ValueError("Passage vectors are not normalized")
    return {"count": len(vectors), "dimensions": EMBEDDING_DIMENSION,
            "norm_min": min(norms), "norm_max": max(norms), "finite": True}


def _trace(session: TracingSession, draft: str, embedder: Any, as_of: date) -> dict[str, Any]:
    """Spy on unchanged production BM25 scores and fusion inputs/outputs."""
    refs = {str(row["id"]): row["source_ref"] for row in session.rows}
    queries, planned_truncated = review._queries(draft, embedder)
    calls: list[dict[str, Any]] = []
    bm25_observations: list[dict[str, Any]] = []
    original_bm25 = hybrid.BM25Plus
    original_fuse = hybrid.fuse_rankings

    class BM25Spy:
        def __init__(self, tokens: list[list[str]]) -> None:
            self.index = original_bm25(tokens)
            self.tokens = tokens

        def get_scores(self, query: list[str]) -> Any:
            scores = self.index.get_scores(query)
            ordered = sorted(range(len(session.rows)), key=lambda index: (
                -float(scores[index]), str(session.rows[index]["id"]),
            ))
            matches = [str(session.rows[index]["id"]) for index in ordered
                       if set(query).intersection(self.tokens[index])]
            bm25_observations.append({"full_bm25_ids": matches, "query_terms": query})
            return scores

    def fuse_spy(dense: list[str], lexical: list[str], **kwargs: Any) -> Any:
        fused = original_fuse(dense, lexical, **kwargs)
        observation = bm25_observations[-1] if bm25_observations else {
            "full_bm25_ids": [], "query_terms": [],
        }
        calls.append({
            "dense_full": [refs[key] for key in session.full_dense],
            "dense_candidates": [refs[key] for key in dense],
            "bm25_full_matching": [refs[key] for key in observation["full_bm25_ids"]],
            "bm25_candidates": [refs[key] for key in lexical],
            "candidate_union": sorted({refs[key] for key in dense + lexical}),
            "rrf_full": [refs[item.passage_id] for item in fused],
            "rrf_candidates": [dict(source_ref=refs[item.passage_id], score=item.score,
                                    dense_rank=item.dense_rank, bm25_rank=item.lexical_rank)
                               for item in fused],
            "query_terms": observation["query_terms"],
            "query_vector_norm": session.query_vector_norm,
        })
        return fused

    before = session.dense_queries
    started = time.perf_counter()
    with patch.object(hybrid, "BM25Plus", BM25Spy), patch.object(hybrid, "fuse_rankings", fuse_spy):
        selected, returned_truncated = review._retrieve(
            session, org_id=ORG, corpus_version=1, as_of=as_of,
            draft=draft, embedder=embedder,
        )
    return {
        "query_texts": queries, "query_tokens": [embedder.count_tokens("query: " + query) for query in queries],
        "planned_queries": len(queries), "searched_queries": len(calls),
        "dense_sql_branch_calls": session.dense_queries - before,
        "planned_truncated": planned_truncated, "returned_truncated": returned_truncated,
        "final8": [passage.source_ref for passage in selected], "searches": calls,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _score(trace: dict[str, Any], gold: set[str]) -> dict[str, Any]:
    """Read gold only after ranking; unscored negative cases have null recall."""
    def recall(ranking: list[str], limit: int | None = None) -> float | None:
        return len(gold.intersection(ranking[:limit])) / len(gold) if gold else None

    searches = []
    for search in trace["searches"]:
        ranks = {source: {key: (search[key].index(source) + 1 if source in search[key] else None)
                          for key in ("dense_full", "dense_candidates", "bm25_full_matching",
                                      "bm25_candidates", "rrf_full")}
                 for source in sorted(gold)}
        searches.append({
            "gold_ranks": ranks,
            "recall": {"dense_at8": recall(search["dense_candidates"], 8),
                       "dense_candidates": recall(search["dense_candidates"]),
                       "bm25_at8": recall(search["bm25_candidates"], 8),
                       "bm25_candidates": recall(search["bm25_candidates"]),
                       "candidate_union": recall(search["candidate_union"]),
                       "rrf_at8": recall(search["rrf_full"], 8),
                       "rrf_candidates": recall(search["rrf_full"])},
        })
    return {"expected_sources": sorted(gold), "scored": bool(gold),
            "final_recall_at8": recall(trace["final8"]), "searches": searches}


def _reproduce_limits(embedder: Any, as_of: date) -> dict[str, Any]:
    """Use actual query planning/tokenization, but mock long-draft search only."""
    text = "Cardamom Chai is served hot. " * 130 + "All items are vegan."
    planned, _ = review._queries(text, embedder)
    searched: list[str] = []

    def fake_retrieve(*args: Any, **kwargs: Any) -> list[Any]:
        searched.append(kwargs["query"])
        return [SimpleNamespace(passage_id=UUID(int=index)) for index in range(1, 9)]

    with patch.object(review, "retrieve_evidence", fake_retrieve):
        _, truncated = review._retrieve(None, org_id=ORG, corpus_version=1,
                                        as_of=as_of, draft=text, embedder=embedder)
    padded = " " * 4100 + "Cardamom Chai contains 120 calories per cup."
    queries, padded_truncated = review._queries(padded, embedder)
    error = None
    try:
        hybrid.retrieve_evidence(None, org_id=ORG, corpus_version=1, as_of=as_of,
                                 query=queries[0], category=None, embedder=embedder)
    except ValueError as exc:
        error = str(exc)
    return {
        "long_draft": {"boundary": "Actual planner and E5 tokenizer; mocked retrieval returns eight IDs per call.",
                       "planned_queries": len(planned), "searched_queries": len(searched),
                       "tail_in_planned_queries": "vegan" in " ".join(planned),
                       "tail_in_searched_queries": "vegan" in " ".join(searched),
                       "returned_truncated": truncated},
        "padded_query": {"boundary": "Actual planner, tokenizer and retrieval validation; no SQL or query inference reached.",
                         "characters": len(padded), "tokens": embedder.count_tokens("query: " + padded),
                         "planned_queries": len(queries), "returned_truncated": padded_truncated,
                         "validation_error": error},
    }


def run_diagnostic(*, experiments: bool = False, as_of: date = date(2026, 11, 5)) -> dict[str, Any]:
    records = eligible_knowledge(_read_jsonl(DATA_DIR / "knowledge/records.jsonl"), as_of)
    cases = [case for case in _read_jsonl(DATA_DIR / "evaluation/drafts.jsonl")
             if case["split"] == "development" and case["content_format"] == "text"
             and case["organisation_id"] == "nook-kettle" and case["data_kind"] == "synthetic_demo"]
    if len(records) != 45 or len(cases) != 7:
        raise ValueError("Expected unchanged 45-record, seven-development-text fixture pack")
    started = time.perf_counter()
    embedder = get_embedder()
    if embedder.model_id != f"{MODEL_NAME}@{MODEL_REVISION}":
        raise ValueError("Diagnostic requires the production pinned E5 adapter")
    load_ms = round((time.perf_counter() - started) * 1000, 3)
    inputs = ["passage: " + record["text"] for record in records]
    vectors = embedder.embed_passages(inputs)
    validation = _vector_summary(vectors)
    session = TracingSession(records, vectors, embedder.model_id, as_of)
    traces = {case["case_id"]: _trace(session, case["content"], embedder, as_of) for case in cases}
    experimental: dict[str, Any] = {}
    if experiments:
        case = next(case for case in cases if case["case_id"] == "NK-16")
        # Fixture metadata and the development draft only; never expected policy text or IDs.
        metadata_records = [dict(record, text=record["title"] + "\n" + record["text"]) for record in records]
        metadata_vectors = embedder.embed_passages(["passage: " + record["text"] for record in metadata_records])
        _vector_summary(metadata_vectors)
        metadata_session = TracingSession(metadata_records, metadata_vectors, embedder.model_id, as_of)
        experimental["title_plus_text"] = _trace(metadata_session, case["content"], embedder, as_of)
        query = "What approved nutritional information and calorie values are available for Cardamom Chai?"
        experimental["nutrition_rule_query"] = _trace(session, query, embedder, as_of)
    reproductions = _reproduce_limits(embedder, as_of)
    # Only now read answer-key fields; nothing above branches on gold or holdout labels.
    gold = {case["case_id"]: {source for finding in case["expected_findings"] for source in finding["source_ids"]}
            for case in cases}
    for case_id, trace in traces.items():
        trace["scoring"] = _score(trace, gold[case_id])
    for trace in experimental.values():
        trace["scoring"] = _score(trace, gold["NK-16"])
    return {
        "scope": "Development-only; actual production Python ranking; simulated exact-cosine SQL; no provider or live PostgreSQL.",
        "as_of": as_of.isoformat(), "model": embedder.model_id, "model_load_ms": load_ms,
        "passage_vectors": validation, "passage_token_max": max(map(embedder.count_tokens, inputs)),
        "development_text_cases": len(cases), "positively_scored_cases": sum(bool(value) for value in gold.values()),
        "production_diagnostic_dense_calls": sum(trace["dense_sql_branch_calls"] for trace in traces.values()),
        "traces": traces, "reproductions": reproductions,
        "experiments": {"enabled": experiments, "illustrative_only": True, "case_id": "NK-16", "n": 1 if experiments else 0,
                        "defaults_changed": False, "winner_selected": False, "traces": experimental},
        "limits": ["No holdout evaluation or tuning", "No persisted-vector, live SQL, or scope-isolation verification",
                   "No generation, semantic entailment, multilingual or general quality claims",
                   "Latency excludes database/provider/worker overhead; model load is separate"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiments", action="store_true", help="Run two illustrative NK-16-only ablations")
    args = parser.parse_args()
    print(json.dumps(run_diagnostic(experiments=args.experiments), indent=2, ensure_ascii=True))
