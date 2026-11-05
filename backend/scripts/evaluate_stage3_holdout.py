"""Archived one-shot Stage 3 holdout scorer; its labels are evaluation-only."""

import hashlib
import json
import tempfile
from pathlib import Path
from uuid import UUID

from app.rag.review_validation import validate_review
from scripts.evaluate_stage3_findings import _baseline_validator

R = Path(__file__).resolve().parents[2]

D = R / "data/evaluation/finding_candidates_stage3_holdout.jsonl"
Q = R / "data/evaluation/stage3_holdout_receipt.json"
receipt = json.loads(Q.read_text(encoding="utf-8"))
assert receipt["status"] == "frozen_before_scoring"
assert hashlib.sha256(D.read_bytes()).hexdigest() == receipt["dataset_sha256"]
for p in (
    "backend/app/rag/review_validation.py",
    "backend/app/rag/evidence_support.py",
):
    assert (
        hashlib.sha256((R / p).read_bytes()).hexdigest()
        == receipt["candidate_sha256_at_commit"][p]
    ), p
cases = [json.loads(x) for x in D.read_text(encoding="utf-8").splitlines() if x]
assert len(cases) == receipt["case_count"]
# Independently authored semantic labels: an exact quote may still fail to support a violation.
semantically_supported = {"H02", "H03", "H06", "H07", "H08", "H10", "H13"}


def assess(name, validator):
    tp = fp = fn = noop = rejected = exact = semantic = cited = anchor_errors = 0
    coverage = {}
    downgraded = 0
    per = []
    rejection_kinds = {}
    for case in cases:
        passages = {UUID(k): v for k, v in case["passages"].items()}
        try:
            out = validator(
                case["candidate"],
                revision_text=case["draft"],
                supplied_passages=passages,
            )
        except (ValueError, TypeError) as exc:
            out = None
            rejection_kinds[case["case_id"]] = type(exc).__name__
        if out is None:
            rejected += 1
        predicted = (
            {(x.candidate.category, x.candidate.quote) for x in out.findings}
            if out
            else set()
        )
        expected = {(x["category"], x["quote"]) for x in case["expected"]}
        t = predicted & expected
        p = predicted - expected
        n = expected - predicted
        tp += len(t)
        fp += len(p)
        fn += len(n)
        if out:
            coverage[out.coverage] = coverage.get(out.coverage, 0) + 1
            if out.coverage != case["candidate"]["coverage"]:
                downgraded += 1
            for item in out.findings:
                c = item.candidate
                if c.suggested_edit is not None and c.suggested_edit == c.quote:
                    noop += 1
                if c.quote is not None and case["draft"].count(c.quote) == 1:
                    if (
                        item.anchor_start is None
                        or item.anchor_end is None
                        or case["draft"][item.anchor_start : item.anchor_end] != c.quote
                    ):
                        anchor_errors += 1
                for e in c.evidence:
                    cited += 1
                    if (
                        e.passage_id in passages
                        and e.supporting_quote in passages[e.passage_id]
                    ):
                        exact += 1
                    if (
                        case["case_id"] in semantically_supported
                        and (c.category, c.quote) in expected
                    ):
                        semantic += 1
        per.append(
            {
                "case_id": case["case_id"],
                "group": case["group"],
                "false_positives": [list(x) for x in sorted(p)],
                "false_negatives": [list(x) for x in sorted(n)],
                "rejected": out is None,
                "coverage": out.coverage if out else None,
            }
        )
    return {
        "cases": len(cases),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "no_op_edits": noop,
        "schema_or_validator_rejections": rejected,
        "rejection_kinds": rejection_kinds,
        "citation_exact_quotes": exact,
        "citation_count": cited,
        "semantic_supporting_citations": semantic,
        "citation_support_rate": round(semantic / cited, 4) if cited else None,
        "evidence_coverage": coverage,
        "coverage_downgrades": downgraded,
        "anchor_validation_errors": anchor_errors,
        "per_case": per,
    }


base = assess("baseline", _baseline_validator())
current = assess(
    "stage3", lambda *a, **k: validate_review(*a, **k, quality_filter=True)
)
result = {
    "assessment": "frozen_independent_stage3_synthetic_candidate_replay",
    "dataset_sha256": receipt["dataset_sha256"],
    "candidate_commit": receipt["candidate_commit"],
    "baseline_commit": receipt["baseline_commit"],
    "scoring_command": r".\backend\.venv\Scripts\python.exe $env:TEMP\stage3_holdout_score.py",
    "baseline": base,
    "stage3_opt_in": current,
    "limitations": [
        "Authored synthetic candidate replay; does not measure live LLM generation.",
        "Ground truth is author assigned; semantic citation support is judged at finding level.",
        "Validation rejects the whole payload for invalid schema or inexact citations.",
        "Do not tune against this assessed holdout.",
    ],
}
print(json.dumps(result, ensure_ascii=False, indent=2))

Path(tempfile.gettempdir()).joinpath("stage3_holdout_score_output.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
)
