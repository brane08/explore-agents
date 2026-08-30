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


# Fix 6 — runs must be bound to the (entry_id, version, model_profile) key;
# evidence gathered on a different version/profile must not cross the key.

def _keyed_runs(n: int, verdict: str = "clean", **key_overrides) -> list[dict]:
    keyed = dict(entry_id=KEY["entry_id"], entry_version=KEY["version"],
                model_profile=KEY["model_profile"])
    keyed.update(key_overrides)
    return [{"run_id": i, "invocation_id": f"inv{i}", "mode": "shadow",
             "verdict": verdict, "reason": "", "adjudicated_by": None, **keyed}
            for i in range(n)]


def test_runs_tagged_with_a_different_version_are_excluded_from_the_streak(tmp_catalog):
    stale = _keyed_runs(20, entry_version="0.0.1")  # not KEY["version"]
    with pytest.raises(ValueError, match="0 clean runs"):
        promote_to_validated(tmp_catalog, **KEY, runs=stale, threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


def test_runs_tagged_with_a_different_profile_are_excluded_from_the_streak(tmp_catalog):
    stale = _keyed_runs(20, model_profile="other-class-ref")
    with pytest.raises(ValueError, match="0 clean runs"):
        promote_to_validated(tmp_catalog, **KEY, runs=stale, threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


def test_only_key_matching_runs_count_toward_a_mixed_evidence_set(tmp_catalog):
    matching = _keyed_runs(20)
    other_version = _keyed_runs(5, entry_version="0.2.0")
    digest = promote_to_validated(tmp_catalog, **KEY, runs=matching + other_version,
                                  threshold=20, run_lockbuild=lambda root: None,
                                  commit=lambda p, m: None)
    assert digest == evidence_digest(matching)


# Fix 8 — align _trailing_clean with the store's supervised_streak: a
# trailing pending row is skipped, not treated as a streak-breaking gap.

def test_a_trailing_pending_run_does_not_zero_the_streak(tmp_catalog):
    runs = _runs(20) + [{"run_id": 20, "invocation_id": "open", "mode": "canary",
                         "verdict": "pending", "reason": "", "adjudicated_by": None}]
    digest = promote_to_validated(tmp_catalog, **KEY, runs=runs, threshold=20,
                                  run_lockbuild=lambda root: None,
                                  commit=lambda p, m: None)
    assert digest == evidence_digest(runs)  # digest carries the pending row too


def test_a_pending_run_is_never_itself_counted_toward_the_streak(tmp_catalog):
    clean = [{"run_id": i, "invocation_id": f"inv{i}", "mode": "shadow",
             "verdict": "clean", "reason": "", "adjudicated_by": None}
            for i in range(1, 20)]  # 19 clean runs, ids 1..19
    pending = [{"run_id": 0, "invocation_id": "open", "mode": "canary",
               "verdict": "pending", "reason": "", "adjudicated_by": None}]
    with pytest.raises(ValueError, match="19 clean runs"):
        promote_to_validated(tmp_catalog, **KEY, runs=clean + pending, threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)
