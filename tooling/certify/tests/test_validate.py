"""Layer 8 [M] — quarantined → validated requires N supervised runs, no other path."""
from __future__ import annotations

import pytest
import yaml

from certify.validate import evidence_digest, promote_to_validated, _matches_key

KEY = dict(entry_id="cron-next-fire-times", version="0.1.0",
           model_profile="stub-class-ref")


def _runs(n: int, verdict: str = "clean") -> list[dict]:
    return [{"run_id": i, "invocation_id": f"inv{i}", "mode": "shadow",
             "verdict": verdict, "reason": "", "adjudicated_by": None,
             "entry_id": KEY["entry_id"], "entry_version": KEY["version"],
             "model_profile": KEY["model_profile"]}
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
                         "adjudicated_by": None, "entry_id": KEY["entry_id"],
                         "entry_version": KEY["version"],
                         "model_profile": KEY["model_profile"]}]
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
                         "verdict": "pending", "reason": "", "adjudicated_by": None,
                         "entry_id": KEY["entry_id"], "entry_version": KEY["version"],
                         "model_profile": KEY["model_profile"]}]
    digest = promote_to_validated(tmp_catalog, **KEY, runs=runs, threshold=20,
                                  run_lockbuild=lambda root: None,
                                  commit=lambda p, m: None)
    assert digest == evidence_digest(runs)  # digest carries the pending row too


def test_a_pending_run_is_never_itself_counted_toward_the_streak(tmp_catalog):
    clean = [{"run_id": i, "invocation_id": f"inv{i}", "mode": "shadow",
             "verdict": "clean", "reason": "", "adjudicated_by": None,
             "entry_id": KEY["entry_id"], "entry_version": KEY["version"],
             "model_profile": KEY["model_profile"]}
            for i in range(1, 20)]  # 19 clean runs, ids 1..19
    pending = [{"run_id": 0, "invocation_id": "open", "mode": "canary",
               "verdict": "pending", "reason": "", "adjudicated_by": None,
               "entry_id": KEY["entry_id"], "entry_version": KEY["version"],
               "model_profile": KEY["model_profile"]}]
    with pytest.raises(ValueError, match="19 clean runs"):
        promote_to_validated(tmp_catalog, **KEY, runs=clean + pending, threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


# M1 — quarantined -> validated is the only path; no other tier promotes.

def test_an_untrusted_entry_is_refused(tmp_catalog):
    trust = tmp_catalog / "trust.yaml"
    yaml.safe_dump({"records": []}, trust.open("w"))
    with pytest.raises(ValueError, match="untrusted, not quarantined"):
        promote_to_validated(tmp_catalog, **KEY, runs=_runs(20), threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


def test_an_already_validated_entry_is_refused(tmp_catalog):
    data = yaml.safe_load((tmp_catalog / "trust.yaml").read_text())
    data["records"].append({**KEY, "id": KEY["entry_id"], "tier": "validated",
                            "granted_by": "prior-run"})
    (tmp_catalog / "trust.yaml").write_text(yaml.safe_dump(data, sort_keys=True))
    with pytest.raises(ValueError, match="validated, not quarantined"):
        promote_to_validated(tmp_catalog, **KEY, runs=_runs(20), threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


def test_an_entry_with_no_trust_record_at_all_is_refused(tmp_catalog):
    with pytest.raises(ValueError, match="untrusted, not quarantined"):
        promote_to_validated(tmp_catalog, entry_id="never-certified", version="0.1.0",
                             model_profile="stub-class-ref", runs=_runs(20), threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


# M2 — a lockbuild or commit failure must leave trust.yaml/lock untouched.

def test_a_lockbuild_failure_rolls_back_trust_yaml(tmp_catalog):
    before = (tmp_catalog / "trust.yaml").read_text()

    def boom(root):
        raise RuntimeError("stale binding elsewhere in the tree")

    with pytest.raises(RuntimeError, match="rolled back"):
        promote_to_validated(tmp_catalog, **KEY, runs=_runs(20), threshold=20,
                             run_lockbuild=boom, commit=lambda p, m: None)
    assert (tmp_catalog / "trust.yaml").read_text() == before


def test_a_commit_failure_rolls_back_trust_yaml_and_lock(tmp_catalog):
    before_trust = (tmp_catalog / "trust.yaml").read_text()
    lock_path = tmp_catalog / "catalog.lock.yaml"
    lock_path.write_text("entries: []\n", encoding="utf-8")
    before_lock = lock_path.read_text()

    def write_lock(root):
        lock_path.write_text("entries: [{'id': 'mutated'}]\n", encoding="utf-8")

    def boom(paths, message):
        raise RuntimeError("git commit failed")

    with pytest.raises(RuntimeError, match="rolled back"):
        promote_to_validated(tmp_catalog, **KEY, runs=_runs(20), threshold=20,
                             run_lockbuild=write_lock, commit=boom)
    assert (tmp_catalog / "trust.yaml").read_text() == before_trust
    assert lock_path.read_text() == before_lock


def test_a_lockbuild_failure_when_no_lock_existed_leaves_no_lock_behind(tmp_catalog):
    lock_path = tmp_catalog / "catalog.lock.yaml"
    assert not lock_path.exists()

    def write_then_fail(root):
        lock_path.write_text("entries: []\n", encoding="utf-8")
        raise RuntimeError("boom after writing the lock")

    with pytest.raises(RuntimeError, match="rolled back"):
        promote_to_validated(tmp_catalog, **KEY, runs=_runs(20), threshold=20,
                             run_lockbuild=write_then_fail, commit=lambda p, m: None)
    assert not lock_path.exists()


# M3 — a run missing a key field entirely is excluded, not treated as pre-scoped.

def test_matches_key_excludes_a_run_missing_key_fields():
    assert _matches_key({"run_id": 1, "verdict": "clean"}, **KEY) is False


def test_a_run_missing_key_columns_is_excluded_from_the_streak(tmp_catalog):
    unkeyed = [{"run_id": i, "invocation_id": f"inv{i}", "mode": "shadow",
               "verdict": "clean", "reason": "", "adjudicated_by": None}
              for i in range(20)]  # no entry_id/entry_version/model_profile
    with pytest.raises(ValueError, match="0 clean runs"):
        promote_to_validated(tmp_catalog, **KEY, runs=unkeyed, threshold=20,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


# M4 — the digest binds the key, the counterpart, and only the trailing window.

def test_digest_differs_for_a_different_key_even_with_identical_run_shape(tmp_catalog):
    a = _keyed_runs(20)
    b = _keyed_runs(20, entry_id="a-different-entry")
    assert evidence_digest(a) != evidence_digest(b)


def test_digest_changes_when_the_counterpart_changes():
    a = [{"run_id": 1, "verdict": "clean", "invocation_id": "x", "counterpart_id": "c1"}]
    b = [{"run_id": 1, "verdict": "clean", "invocation_id": "x", "counterpart_id": "c2"}]
    assert evidence_digest(a) != evidence_digest(b)


def test_digest_is_stable_under_run_list_reordering():
    runs = _runs(5)
    assert evidence_digest(runs) == evidence_digest(list(reversed(runs)))


def test_digest_excludes_evidence_before_a_streak_resetting_incident(tmp_catalog):
    # 20 clean, an incident, then 20 more clean (most recent) — only the
    # trailing 20 should be in the digest, not the pre-incident 20.
    old_clean = _keyed_runs(20)
    for i, r in enumerate(old_clean):
        r["run_id"] = i
    incident = _keyed_runs(1, verdict="incident")
    incident[0]["run_id"] = 20
    new_clean = _keyed_runs(20)
    for i, r in enumerate(new_clean):
        r["run_id"] = 21 + i

    digest = promote_to_validated(tmp_catalog, **KEY,
                                  runs=old_clean + incident + new_clean, threshold=20,
                                  run_lockbuild=lambda root: None, commit=lambda p, m: None)
    assert digest == evidence_digest(new_clean)
    assert digest != evidence_digest(old_clean + incident + new_clean)


# M5 — ordering prefers created_at; a mixed/uncomparable run_id fails closed.

def test_ordering_prefers_created_at_over_run_id(tmp_catalog):
    # run_id ordering (descending) would put "run-9" before "run-10" and
    # miss the trailing incident; created_at (ISO-8601, sorts lexically)
    # gets the real order right.
    runs = [{"run_id": f"run-{i}", "invocation_id": f"inv{i}", "mode": "shadow",
            "verdict": "clean", "reason": "", "adjudicated_by": None,
            "created_at": f"2026-01-01T00:00:{i:02d}Z", "entry_id": KEY["entry_id"],
            "entry_version": KEY["version"], "model_profile": KEY["model_profile"]}
            for i in range(1, 10)]
    incident = [{"run_id": "run-10", "invocation_id": "inv10", "mode": "shadow",
                "verdict": "incident", "reason": "diverged", "adjudicated_by": None,
                "created_at": "2026-01-01T00:00:10Z", "entry_id": KEY["entry_id"],
                "entry_version": KEY["version"], "model_profile": KEY["model_profile"]}]
    with pytest.raises(ValueError, match="0 clean runs"):
        promote_to_validated(tmp_catalog, **KEY, runs=runs + incident, threshold=9,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)


def test_mixed_run_id_types_with_no_created_at_raises_value_error(tmp_catalog):
    runs = _keyed_runs(5)
    runs[0]["run_id"] = "not-an-int"  # rest are ints from _keyed_runs
    with pytest.raises(ValueError, match="uncomparable"):
        promote_to_validated(tmp_catalog, **KEY, runs=runs, threshold=5,
                             run_lockbuild=lambda root: None, commit=lambda p, m: None)
