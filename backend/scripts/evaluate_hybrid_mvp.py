"""Evaluate production hybrid Python retrieval on the synthetic text pack.

Run from backend: python scripts/evaluate_hybrid_mvp.py
No provider calls by default; --generate-smoke submits two synthetic cases.
No database writes. Cached E5 weights are required.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
import time
from uuid import NAMESPACE_URL, uuid5

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.jobs.handlers.review_revision import _retrieve  # noqa: E402
from app.rag.embeddings import get_embedder  # noqa: E402
from scripts.evaluate_text_mvp import (  # noqa: E402
    DATA_DIR, _read_jsonl, _score_split, eligible_knowledge,
)

ORG = uuid5(NAMESPACE_URL, "veriloom:synthetic:evaluation")


class _Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return 1


class SyntheticSession:
    """Simulate only the production SQL boundary, not SQL scope correctness.

    Exact cosine over real E5 vectors replaces pgvector. Production retrieval
    still performs lexical ranking, candidate bounds, RRF, and query planning.
    Eligibility is applied by the fixture loader before either ranking.
    """

    def __init__(self, records, vectors, model_id, as_of):
        self.model_id, self.as_of = model_id, as_of
        self.dense_queries = 0
        self.rows = sorted([
            dict(
                id=uuid5(ORG, r["record_id"]), source_id=uuid5(ORG, r["document_id"]),
                version_id=uuid5(ORG, r["document_id"] + r["source_version"]),
                source_ref=r["record_id"], source_version=r["source_version"],
                title=r["title"], text=r["text"], category=r["category"],
                effective_from=date.fromisoformat(r["effective_from"]),
                effective_to=date.fromisoformat(r["effective_to"]) if r["effective_to"] else None,
                vector=v,
            ) for r, v in zip(records, vectors, strict=True)
        ], key=lambda r: str(r["id"]))

    def execute(self, statement, params):
        sql = str(statement)
        if params["org_id"] != ORG:
            raise ValueError("Unexpected evaluation organisation")
        if "SELECT corpus_version FROM organisations" in sql:
            return _Result()
        if (params["model_id"] != self.model_id or params["as_of"] != self.as_of
                or params["category"] is not None):
            raise ValueError("Unexpected evaluation scope")
        if "ORDER BY p.embedding <=>" in sql:
            self.dense_queries += 1
            query = json.loads(params["embedding"])

            def cosine(row):
                vector = row["vector"]
                return sum(a*b for a, b in zip(vector, query, strict=True)) / (
                    sum(a*a for a in vector)*sum(b*b for b in query))**0.5

            ranked = sorted(self.rows, key=lambda r: (-cosine(r), str(r["id"])))
            return _Result([{"id": r["id"]} for r in ranked[:params["limit"]]])
        if "SELECT p.id, p.source_id" in sql and "ORDER BY p.id" in sql:
            return _Result(self.rows)
        raise ValueError("Unsupported evaluation SQL boundary")


def _generate_smoke(case, passages, chat, as_of):
    from app.integrations.failover import completed_provenance
    from app.jobs.handlers.review_revision import (
        DEVELOPER_PROMPT, INITIAL_REVIEW_SCHEMA, SYSTEM_PROMPT,
    )
    from app.rag.review_validation import validate_review

    payload = dict(
        task="initial_review", review_id=str(uuid5(ORG, case["case_id"])),
        revision_id=str(uuid5(ORG, case["case_id"] + ":revision")),
        review_as_of=as_of.isoformat(), channel=case["channel"], draft_text=case["content"],
        guidance=[dict(passage_id=str(p.passage_id), source_ref=p.source_ref,
                       source_version=p.source_version, title=p.title, text=p.text) for p in passages],
    )
    started = time.perf_counter()
    candidate = chat.complete_json([
        dict(role="system", content=SYSTEM_PROMPT),
        dict(role="developer", content=DEVELOPER_PROMPT),
        dict(role="user", content=json.dumps(payload, ensure_ascii=False)),
    ], schema_name="veriloom_initial_review_v2", schema=INITIAL_REVIEW_SCHEMA, max_tokens=4096)
    validated = validate_review(candidate, revision_text=case["content"],
                                supplied_passages={p.passage_id: p.text for p in passages})
    refs = {p.passage_id: p.source_ref for p in passages}
    return dict(
        status="validated", provider_model=completed_provenance(chat),
        generation_validation_ms=(time.perf_counter()-started)*1000,
        coverage=validated.coverage, coverage_reason=validated.coverage_reason,
        findings=[dict(
            category=f.candidate.category, severity=f.candidate.severity,
            evidence_status=f.candidate.evidence_status, quote=f.candidate.quote,
            anchor_start=f.anchor_start, anchor_end=f.anchor_end,
            explanation=f.candidate.explanation, suggested_edit=f.candidate.suggested_edit,
            citations=[dict(source_ref=refs[c.passage_id], supporting_quote=c.supporting_quote)
                       for c in f.candidate.evidence],
        ) for f in validated.findings],
    )


def run_evaluation(as_of=date(2026, 11, 5), *, generate_smoke=False):
    records = eligible_knowledge(_read_jsonl(DATA_DIR / "knowledge/records.jsonl"), as_of)
    cases = [c for c in _read_jsonl(DATA_DIR / "evaluation/drafts.jsonl")
             if c["organisation_id"] == "nook-kettle" and c["data_kind"] == "synthetic_demo"
             and c["content_format"] == "text" and c["split"] in {"development", "holdout"}]
    counts = {s: sum(c["split"] == s for c in cases) for s in ("development", "holdout")}
    if len(records) != 45 or counts != {"development": 7, "holdout": 5}:
        raise ValueError("Expected unchanged 45-record, 7-development/5-holdout synthetic pack")
    start = time.perf_counter()
    embedder = get_embedder()
    load_ms = (time.perf_counter()-start)*1000
    start = time.perf_counter()
    vectors = embedder.embed_passages(["passage: " + r["text"] for r in records])
    passage_ms = (time.perf_counter()-start)*1000
    session = SyntheticSession(records, vectors, embedder.model_id, as_of)
    rankings, timings, snapshots = {}, {}, {}
    for case in cases:
        start = time.perf_counter()
        selected, truncated = _retrieve(
            session, org_id=ORG, corpus_version=1, as_of=as_of,
            draft=case["content"], embedder=embedder,
        )
        timings[case["case_id"]] = [(time.perf_counter()-start)*1000]
        if truncated:
            raise ValueError("Unexpected truncated synthetic draft query")
        rankings[case["case_id"]] = [p.source_ref for p in selected]
        snapshots[case["case_id"]] = selected
    generation = {}
    if generate_smoke:
        from app.core.config import get_settings
        from app.integrations.failover import build_worker_chat

        chat = build_worker_chat(get_settings())
        try:
            for case in cases:
                if case["case_id"] not in {"NK-16", "NK-21"}:
                    continue
                try:
                    generation[case["case_id"]] = _generate_smoke(
                        case, snapshots[case["case_id"]], chat, as_of,
                    )
                except Exception as exc:
                    generation[case["case_id"]] = dict(status="failed", error_type=type(exc).__name__)
        finally:
            chat.close()
    # Answer keys are accessed only after all retrieval and generation complete.
    for case in cases:
        if case["case_id"] in generation:
            generation[case["case_id"]]["offline_expected_findings"] = case["expected_findings"]
    splits = {s: _score_split(
        s, [c for c in cases if c["split"] == s], rankings, timings,
        {r["record_id"] for r in records},
    ) for s in ("development", "holdout")}
    return dict(
        as_of=as_of.isoformat(), eligible_knowledge_count=len(records), text_count=len(cases),
        model=embedder.model_id, model_load_ms=load_ms, passage_embedding_ms=passage_ms,
        dense_queries=session.dense_queries, provider_logical_completions=len(generation),
        generation=generation, splits=splits, rankings=rankings,
        scope="Production Python hybrid/query planning; simulated SQL exact cosine. No DB integration.",
        latency_scope="Query E5, simulated cosine, BM25 and RRF; excludes load, corpus embeddings, DB, provider, worker.",
        not_measured=["aggregate generation quality", "automatic semantic entailment", "database authorization/isolation", "follow-ups", "multilingual quality"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-smoke", action="store_true",
                        help="Send only synthetic NK-16 and NK-21 with actual hybrid evidence to worker providers")
    args = parser.parse_args()
    print(json.dumps(run_evaluation(generate_smoke=args.generate_smoke), indent=2))
