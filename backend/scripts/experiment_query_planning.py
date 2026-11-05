"""Compare opt-in purpose queries with unchanged Stage 1 development rankings.

Run from ``backend`` with ``python -B scripts/experiment_query_planning.py``.
No provider or database is called. Labels are read only after every ranking is
finished; the experiment never opens the existing holdout.
"""

from __future__ import annotations

from datetime import date
import json
import math
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
    JUDGMENTS, case_input, score_groups, score_ranking, validate_judgments,
)
from scripts.evaluate_text_mvp import DATA_DIR, _read_jsonl, eligible_knowledge  # noqa: E402


def _working_set_mb() -> float | None:
    """Current Windows process RSS; include native PyTorch allocations."""
    if sys.platform != "win32":
        return None
    import ctypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
    ]
    ctypes.windll.psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return round(counters.WorkingSetSize / 1048576, 2)


def run_experiment() -> dict:
    as_of = date(2026, 11, 5)
    records = eligible_knowledge(_read_jsonl(DATA_DIR / "knowledge/records.jsonl"), as_of)
    inputs = [case_input(case) for case in _read_jsonl(JUDGMENTS)]
    initial_rss = _working_set_mb()
    started = time.perf_counter()
    embedder = get_embedder()
    model_load_ms = round((time.perf_counter() - started) * 1000, 3)
    vectors = embedder.embed_passages(["passage: " + item["text"] for item in records])
    after_embedding_rss = _working_set_mb()
    modes = ("baseline", "draft_only", "draft_and_followup")
    traces: dict[str, dict[str, dict]] = {mode: {} for mode in modes}
    for mode in modes:
        session = TracingSession(records, vectors, embedder.model_id, as_of)
        for item in inputs:
            queries, truncated = review._queries(item.text, embedder)
            if mode == "draft_and_followup" or (mode == "draft_only" and item.kind == "draft"):
                queries = augment_queries(queries, embedder)
            with patch.object(review, "_queries", return_value=(queries, truncated)):
                traces[mode][item.case_id] = _trace(session, item.text, embedder, as_of)

    labels = _read_jsonl(JUDGMENTS)
    validate_judgments(labels, {record["record_id"] for record in records})
    if [case_input(case) for case in labels] != inputs:
        raise ValueError("Inputs changed between rankings and scoring")
    results: dict[str, dict] = {}
    for mode in modes:
        candidate_hits = final_hits = available = searches = 0
        elapsed: list[float] = []
        changed: dict[str, dict] = {}
        for case in labels:
            trace = traces[mode][case["case_id"]]
            candidates = list(dict.fromkeys(source for search in trace["searches"]
                                            for source in search["candidate_union"]))
            candidate = score_groups(candidates, case["required_evidence_groups"])
            final = score_ranking(trace["final8"], case)
            candidate_hits += candidate["retrieved_groups"]
            final_hits += final["retrieved_groups"]
            available += final["retrievable_groups"]
            searches += trace["searched_queries"]
            elapsed.append(trace["elapsed_ms"])
            baseline = traces["baseline"][case["case_id"]]
            if trace["query_texts"] != baseline["query_texts"]:
                changed[case["case_id"]] = {
                    "kind": case["input"]["kind"],
                    "added_queries": trace["query_texts"][len(baseline["query_texts"]):],
                    "candidate_hits": candidate["retrieved_group_ids"],
                    "final_hits": final["retrieved_group_ids"],
                    "final8": trace["final8"],
                    "elapsed_ms": trace["elapsed_ms"],
                }
        results[mode] = {
            "cases": len(labels), "searched_queries": searches,
            "candidate_groups": f"{candidate_hits}/{available}",
            "final_groups": f"{final_hits}/{available}", "changed_cases": changed,
            "warm_elapsed_ms": {
                "mean": round(statistics.mean(elapsed), 3),
                "p95_nearest_rank": sorted(elapsed)[math.ceil(0.95 * len(elapsed)) - 1],
            },
            "working_set_mb": _working_set_mb(),
        }
    results["measurement_scope"] = {
        "model_load_ms": model_load_ms,
        "initial_working_set_mb": initial_rss,
        "after_model_and_corpus_embedding_mb": after_embedding_rss,
        "limits": "One serial in-process run over synthetic 45-record exact-cosine simulation; no database/provider/worker; timing can vary by system load.",
    }
    return results


if __name__ == "__main__":
    print(json.dumps(run_experiment(), indent=2, ensure_ascii=False))
