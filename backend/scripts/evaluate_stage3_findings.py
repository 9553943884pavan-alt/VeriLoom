"""Replay synthetic development model candidates through baseline/current validators.

This measures validator behavior on authored candidates, not live LLM quality.
Run from backend: python -m scripts.evaluate_stage3_findings
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

from app.rag.review_validation import validate_review

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "data/evaluation/finding_candidates_stage3_dev.jsonl"
BASELINE_COMMIT = "6278953"


def _baseline_validator():
    source = subprocess.run(
        ["git", "show", f"{BASELINE_COMMIT}:backend/app/rag/review_validation.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "stage3_baseline_validator.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("stage3_baseline_validator", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.validate_review


def score(cases: list[dict], validator) -> dict:
    true_positive = false_positive = false_negative = 0
    clean_false_positive = unwanted_cited = no_op_edits = rejected = 0
    support_correct = support_total = 0
    latencies = []
    per_case = []
    for case in cases:
        passages = {UUID(key): value for key, value in case["passages"].items()}
        start = time.perf_counter()
        try:
            result = validator(case["candidate"], revision_text=case["draft"],
                               supplied_passages=passages)
        except (ValueError, TypeError):
            result = None
        latencies.append((time.perf_counter() - start) * 1000)
        if result is None:
            rejected += 1
        predicted = {(item.candidate.category, item.candidate.quote)
                     for item in result.findings} if result else set()
        expected = {(item["category"], item["quote"]) for item in case["expected"]}
        true_positive += len(predicted & expected)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)
        if not expected and predicted:
            clean_false_positive += 1
        if result:
            for finding in result.findings:
                item = finding.candidate
                if item.suggested_edit is not None and item.suggested_edit == item.quote:
                    no_op_edits += 1
                key = f"{item.category}:{item.quote}"
                if key in case["expected_support"]:
                    support_total += 1
                    support_correct += item.evidence_status == case["expected_support"][key]
                if (item.category, item.quote) not in expected and item.evidence:
                    unwanted_cited += 1
        per_case.append({"case_id": case["case_id"], "predicted": len(predicted),
                         "expected": len(expected), "rejected": result is None})
    return {
        "cases": len(cases), "true_positive": true_positive,
        "false_positive": false_positive, "false_negative": false_negative,
        "precision": round(true_positive / (true_positive + false_positive), 4)
        if true_positive + false_positive else None,
        "recall": round(true_positive / (true_positive + false_negative), 4)
        if true_positive + false_negative else None,
        "clean_false_positive_cases": clean_false_positive,
        "unwanted_cited_findings": unwanted_cited,
        "evidence_status_accuracy_on_matched_findings": round(support_correct / support_total, 4)
        if support_total else None,
        "no_op_edits": no_op_edits, "schema_or_validator_rejections": rejected,
        "mean_validator_latency_ms": round(statistics.mean(latencies), 4),
        "per_case": per_case,
    }


def main() -> None:
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(json.dumps({
        "dataset": "authored synthetic development candidate replay",
        "limitations": "Not live LLM quality, broad semantic entailment, or independent holdout.",
        "baseline_commit": BASELINE_COMMIT,
        "baseline": score(cases, _baseline_validator()),
        "stage3": score(cases, lambda *args, **kwargs:
                        validate_review(*args, **kwargs, quality_filter=True)),
    }, indent=2))


if __name__ == "__main__":
    main()
