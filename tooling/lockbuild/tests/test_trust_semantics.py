"""Trust-ledger semantics (CATALOG §7): append-only, latest record wins."""
from __future__ import annotations

import pytest

from catalog_fixtures import BUILT_AT, BUILT_FROM, edit_entry, w, wyaml
from lockbuild.build import build_lock
from lockbuild.hashing import entry_hash
from lockbuild.refresh import RefreshOnMainError, refresh
from lockbuild.trust import effective_tier


def rec(eid, tier, profile="opus-class-ref", version="1.0.0"):
    return {"id": eid, "version": version, "model_profile": profile,
            "tier": tier, "granted_by": "t", "evidence": "e"}


def entry(lock, eid):
    return next(e for e in lock["entries"] if e["id"] == eid)


def test_promotion_by_append_takes_effect():
    records = [rec("x", "quarantined"), rec("x", "validated")]
    assert effective_tier(records, "x", "1.0.0", "opus-class-ref") == "validated"


def test_recall_by_append_takes_effect():
    records = [rec("x", "validated"), rec("x", "untrusted")]
    assert effective_tier(records, "x", "1.0.0", "opus-class-ref") == "untrusted"


def test_profile_exact_when_known():
    records = [rec("x", "validated", profile="a"), rec("x", "quarantined", profile="b")]
    assert effective_tier(records, "x", "1.0.0", "a") == "validated"
    assert effective_tier(records, "x", "1.0.0", "b") == "quarantined"
    assert effective_tier(records, "x", "1.0.0", "c") == "untrusted"
    # profile unknown (lock join of manifest-less entries): conservative min
    # over the latest record of each profile
    assert effective_tier(records, "x", "1.0.0", None) == "quarantined"


def test_b1_explicit_recall_is_never_rederived(catalog):
    """A recorded recall (tier: untrusted) must not be overridden by the
    min(binding tiers) derivation."""
    edit_entry(catalog, "agents/composed", assembly="b1")
    wyaml(catalog / "trust.yaml", {"records": [
        rec("greet", "validated"),
        rec("echo-tool", "validated"),
        rec("composed", "untrusted"),      # the recall
    ]})
    lock = build_lock(catalog, built_from=BUILT_FROM, built_at=BUILT_AT)
    assert entry(lock, "composed")["trust_tier"] == "untrusted"


def test_hash_cannot_forge_file_boundaries(tmp_path):
    """Length-prefixed hashing: content embedding separator-like bytes must
    not collide with a differently-shaped tree."""
    one = tmp_path / "one"
    (one / "impl").mkdir(parents=True)
    w(one / "SKILL.md", "s")
    (one / "impl" / "a").write_bytes(b"x\x00impl/b\x00y")

    two = tmp_path / "two"
    (two / "impl").mkdir(parents=True)
    w(two / "SKILL.md", "s")
    (two / "impl" / "a").write_bytes(b"x")
    (two / "impl" / "b").write_bytes(b"y")

    assert entry_hash(one, "skill") != entry_hash(two, "skill")


def test_build_artifacts_do_not_enter_the_hash(tmp_path):
    """Certify step 3 executes the candidate's tests, which leaves `__pycache__`
    beside the sources. Those bytes vary with interpreter, pytest version and
    whether the suite ever ran, so hashing them makes the entry hash depend on
    the build machine: a lock built after an eval run never matches a clean CI
    checkout of the same commit."""
    entry_dir = tmp_path / "agent"
    (entry_dir / "src").mkdir(parents=True)
    w(entry_dir / "AGENT_MANIFEST.yaml", "agent_id: a")
    w(entry_dir / "src" / "agent.py", "x = 1")
    clean = entry_hash(entry_dir, "agent")

    cache = entry_dir / "src" / "__pycache__"
    cache.mkdir()
    (cache / "agent.cpython-314.pyc").write_bytes(b"\x00compiled")

    assert entry_hash(entry_dir, "agent") == clean


def test_refresh_refused_on_detached_head(catalog):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(catalog), *args],
                       check=True, capture_output=True)

    git("init", "-q", "-b", "work")
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed")
    git("checkout", "-q", "--detach")     # CI checkout default shape
    with pytest.raises(RefreshOnMainError):
        refresh(catalog, fetch=lambda e: {"name": "echo-tool"})
