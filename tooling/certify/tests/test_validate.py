"""Layer 8 [M] — quarantined → validated requires N supervised runs, no other path."""
from __future__ import annotations

import pytest
import yaml

from certify.validate import evidence_digest, promote_to_validated

KEY = dict(entry_id="cron-next-fire-times", version="0.1.0",
           model_profile="stub-class-ref")


def _runs(n: int, verdict: str = "clean") -> list[dict]:
    return [{"run_id": i, "invocation_id": f"inv{i}", "mode": "shadow",
             "verdict": verdict, "reason": "", "adjudicated_by": None}
            for i in range(n)]


def test_digest_is_stable_across_key_order():
    a = [{"run_id": 1, "verdict": "clean", "invocation_id": "x"}]
    b = [{"verdict": "clean", "invocation_id": "x", "run_id": 1}]
    assert evidence_digest(a) == evidence_digest(b)


def test_digest_changes_when_the_evidence_changes():
    assert evidence_digest(_runs(3)) != evidence_digest(_runs(4))


def test_promotion_below_the_threshold_is_refused(tmp_catalog):
    with pytest.raises(ValueError, match="19 clean runs"):
        promote_to_validated(tmp_catalog, **KEY, runs=_runs(19), threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


def test_promotion_writes_one_validated_record_carrying_the_digest(tmp_catalog):
    digest = promote_to_validated(tmp_catalog, **KEY, runs=_runs(20), threshold=20,
                                  run_lockbuild=lambda root: None,
                                  commit=lambda p, m: None)

    records = yaml.safe_load((tmp_catalog / "trust.yaml").read_text())["records"]
    latest = records[-1]
    assert latest["id"] == "cron-next-fire-times"
    assert latest["tier"] == "validated"
    assert latest["model_profile"] == "stub-class-ref"
    assert digest in latest["evidence"]


def test_promotion_appends_and_never_rewrites_history(tmp_catalog):
    before = yaml.safe_load((tmp_catalog / "trust.yaml").read_text())["records"]

    promote_to_validated(tmp_catalog, **KEY, runs=_runs(20), threshold=20,
                         run_lockbuild=lambda root: None, commit=lambda p, m: None)

    after = yaml.safe_load((tmp_catalog / "trust.yaml").read_text())["records"]
    assert after[:len(before)] == before
    assert len(after) == len(before) + 1


def test_an_incident_in_the_tail_blocks_promotion(tmp_catalog):
    runs = _runs(19) + [{"run_id": 99, "invocation_id": "bad", "mode": "shadow",
                         "verdict": "incident", "reason": "field 'count' differs",
                         "adjudicated_by": None}]
    with pytest.raises(ValueError):
        promote_to_validated(tmp_catalog, **KEY, runs=runs, threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)
