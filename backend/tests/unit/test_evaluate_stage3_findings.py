"""The development replay stays deterministic and keeps labels out of prompts."""

import json

from scripts.evaluate_stage3_findings import CASES, _baseline_validator, score
from app.rag.review_validation import validate_review


def test_stage3_replay_reduces_authored_unwanted_findings_without_losing_issues():
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    before = score(cases, _baseline_validator())
    after = score(cases, lambda *args, **kwargs:
                  validate_review(*args, **kwargs, quality_filter=True))
    assert after["true_positive"] == before["true_positive"] == 5
    assert after["false_positive"] == 0 < before["false_positive"]
    assert after["no_op_edits"] == 0 < before["no_op_edits"]
    assert after["schema_or_validator_rejections"] == before["schema_or_validator_rejections"] == 1
