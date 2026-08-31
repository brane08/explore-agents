"""certify CLI `validate` — --runs file error handling."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from certify.cli import _git_commit, main


def _argv(tmp_catalog: Path, runs_path: Path) -> list[str]:
    return ["validate", "--root", str(tmp_catalog), "--entry", "cron-next-fire-times",
            "--version", "0.1.0", "--profile", "stub-class-ref",
            "--runs", str(runs_path), "--threshold", "1"]


def test_missing_runs_file_exits_cleanly(tmp_catalog):
    missing = tmp_catalog / "no-such-file.json"
    with pytest.raises(SystemExit, match="cannot read --runs file"):
        main(_argv(tmp_catalog, missing))


def test_malformed_json_exits_cleanly(tmp_catalog):
    bad = tmp_catalog / "runs.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit, match="not valid JSON"):
        main(_argv(tmp_catalog, bad))


def test_run_missing_expected_field_exits_cleanly(tmp_catalog):
    # key fields present (so the row is in-scope) but `verdict` missing —
    # exercises the KeyError path in _trailing_evidence, distinct from the
    # fail-closed _matches_key exclusion (test_validate.py covers that).
    bad = tmp_catalog / "runs.json"
    bad.write_text(
        '[{"run_id": 1, "entry_id": "cron-next-fire-times", '
        '"entry_version": "0.1.0", "model_profile": "stub-class-ref"}]',
        encoding="utf-8")
    with pytest.raises(SystemExit, match="malformed --runs data"):
        main(_argv(tmp_catalog, bad))


# M6 — the real _git_commit seam stages exactly the given paths, never -A.

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_git_commit_stages_only_the_given_paths_not_the_whole_tree(tmp_path):
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "certify-test")

    trust = repo / "trust.yaml"
    trust.write_text("records: []\n", encoding="utf-8")
    _git(repo, "add", "trust.yaml")
    _git(repo, "commit", "-q", "-m", "seed")

    trust.write_text("records: [{'tier': 'validated'}]\n", encoding="utf-8")
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("an operator's in-flight, unrelated edit\n", encoding="utf-8")

    _git_commit([trust], "certify: promote")

    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            check=True, capture_output=True, text=True).stdout
    assert "unrelated.txt" in status  # still untracked — never staged
    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--name-only",
                          "--pretty=format:"], check=True, capture_output=True,
                         text=True).stdout
    assert log.strip() == "trust.yaml"
