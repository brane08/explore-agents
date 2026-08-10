"""The conformance list (CATALOG §10) — generated only by measurement.

Certify step 1 and B2b dispatch both gate on this file, so the properties that
matter are: only a passing run lists a harness, and a later failing run can
take the listing away.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from certify.cli import main
from certify.conformance_list import LIST_FILENAME, is_listed, load_listed, record
from certify_fixtures import LOCK, conformant_harness, liar_harness


def test_absent_list_lists_nobody(tmp_path: Path):
    assert load_listed(tmp_path) == {}
    assert not is_listed(tmp_path, "anything/1")


def test_only_conformant_records_are_listed(tmp_path: Path):
    record(tmp_path, "good/1", True, "report")
    record(tmp_path, "bad/1", False, "report")
    assert is_listed(tmp_path, "good/1")
    assert not is_listed(tmp_path, "bad/1"), "a recorded failure must not list the harness"


def test_a_failing_rerun_unlists_a_harness(tmp_path: Path):
    record(tmp_path, "drifty/1", True, "passed")
    assert is_listed(tmp_path, "drifty/1")
    record(tmp_path, "drifty/1", False, "regressed")
    assert not is_listed(tmp_path, "drifty/1")
    doc = yaml.safe_load((tmp_path / LIST_FILENAME).read_text(encoding="utf-8"))
    assert len(doc["harnesses"]) == 1, "re-running a harness upserts, never appends"


def _write_lock(root: Path) -> None:
    (root / "catalog.lock.yaml").write_text(yaml.safe_dump(LOCK), encoding="utf-8")


def test_cli_records_a_conformant_harness_and_exits_zero(tmp_path: Path):
    _write_lock(tmp_path)
    code = main(["conformance", "--root", str(tmp_path), "--harness", "good/1",
                 "--backend", "certify_fixtures:conformant_harness", "--record"])
    assert code == 0
    assert is_listed(tmp_path, "good/1")


def test_cli_refuses_to_list_a_cheating_harness(tmp_path: Path):
    _write_lock(tmp_path)
    code = main(["conformance", "--root", str(tmp_path), "--harness", "liar/1",
                 "--backend", "certify_fixtures:liar_harness", "--record"])
    assert code == 1, "a cheating harness must fail the command"
    assert not is_listed(tmp_path, "liar/1")


def test_cli_without_record_writes_nothing(tmp_path: Path):
    _write_lock(tmp_path)
    main(["conformance", "--root", str(tmp_path), "--harness", "good/1",
          "--backend", "certify_fixtures:conformant_harness"])
    assert not (tmp_path / LIST_FILENAME).exists()
