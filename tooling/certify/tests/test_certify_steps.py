"""Certify steps 1-2 (CATALOG §8, CHECKLISTS layer 7) — model-independent.

Step 1: layout & manifest — §6 structure, playbook_version supported, harness on
the conformance list, entry.draft.yaml present, criteria refs match frozen inputs.
Step 2: rebase check — bound hashes current at the promotion tree, else NEEDS_REBASE.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from certify.conformance_list import record
from certify.playbook import HarnessInputs, criteria_hash
from certify.candidate import load_candidate
from certify.evalrun import EVIDENCE_NAME, load_eval_evidence, persist_eval_evidence
from certify.steps import step_eval, step_layout_manifest, step_rebase
from certify_fixtures import LOCK, write_candidate


CRITERIA = "BC-1: only error-level records are returned.\nBC-2: tool failure degrades."


def _inputs(**over) -> HarnessInputs:
    base = dict(
        task_spec="List the error-level log entries.",
        behavioral_criteria=CRITERIA,
        catalog_ref="0" * 40,
        capability_manifest=LOCK,
        trust_tier_ceiling="validated",
        mode="B2b",
        scope="full-agent",
        model_profile="stub-class-ref",
        harness="stub-harness",
        target_runtime="langgraph",
    )
    base.update(over)
    return HarnessInputs(**base)


@pytest.fixture
def catalog_root(tmp_path: Path) -> Path:
    """A catalog root whose conformance list contains the stub harness."""
    root = tmp_path / "catalog"
    root.mkdir()
    record(root, "stub-harness", conformant=True, report="fixtures green")
    return root


@pytest.fixture
def candidate_dir(tmp_path: Path) -> Path:
    return tmp_path / "_proposed" / "log-error-lister"


def _load(candidate_dir: Path, inputs: HarnessInputs):
    write_candidate(candidate_dir, inputs)
    return load_candidate(candidate_dir)


# --- step 1: layout & manifest ----------------------------------------------

def test_step1_passes_on_conformant_candidate(candidate_dir, catalog_root):
    inputs = _inputs()
    result = step_layout_manifest(_load(candidate_dir, inputs), inputs, catalog_root)
    assert result.passed, result.detail


def test_step1_fails_when_harness_not_listed(candidate_dir, catalog_root):
    inputs = _inputs(harness="unlisted-harness")
    result = step_layout_manifest(_load(candidate_dir, inputs), inputs, catalog_root)
    assert not result.passed
    assert "conformance" in result.detail.lower()


def test_step1_fails_on_unsupported_playbook_version(candidate_dir, catalog_root):
    inputs = _inputs()
    write_candidate(candidate_dir, inputs)
    manifest = candidate_dir / "AGENT_MANIFEST.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("3-beta", "99-gamma"),
        encoding="utf-8")
    result = step_layout_manifest(load_candidate(candidate_dir), inputs, catalog_root)
    assert not result.passed
    assert "playbook_version" in result.detail


def test_step1_fails_when_entry_draft_missing(candidate_dir, catalog_root):
    inputs = _inputs()
    write_candidate(candidate_dir, inputs)
    (candidate_dir / "entry.draft.yaml").unlink()
    result = step_layout_manifest(load_candidate(candidate_dir), inputs, catalog_root)
    assert not result.passed


def test_step1_fails_on_criteria_ref_mismatch(candidate_dir, catalog_root):
    inputs = _inputs()
    write_candidate(candidate_dir, inputs, criteria_text="BC-1: rewritten.")
    result = step_layout_manifest(load_candidate(candidate_dir), inputs, catalog_root)
    assert not result.passed
    assert "criteria" in result.detail.lower()


def test_step1_fails_on_unloadable_candidate(tmp_path, catalog_root):
    empty = tmp_path / "empty-candidate"
    empty.mkdir()
    result = step_layout_manifest(load_candidate(empty), _inputs(), catalog_root)
    assert not result.passed


# --- step 2: rebase check ---------------------------------------------------

def test_step2_passes_when_bound_hashes_current(candidate_dir):
    inputs = _inputs()
    result = step_rebase(_load(candidate_dir, inputs), LOCK)
    assert result.passed, result.detail


def test_step2_fails_when_bound_hash_moved(candidate_dir):
    inputs = _inputs()
    candidate = _load(candidate_dir, inputs)
    moved = copy.deepcopy(LOCK)
    next(e for e in moved["entries"] if e["id"] == "es_search")["hash"] = "moved"
    result = step_rebase(candidate, moved)
    assert not result.passed
    assert "NEEDS_REBASE" in result.detail


def test_step2_fails_when_bound_entry_gone(candidate_dir):
    inputs = _inputs()
    candidate = _load(candidate_dir, inputs)
    gone = copy.deepcopy(LOCK)
    gone["entries"] = [e for e in gone["entries"] if e["id"] != "es_search"]
    result = step_rebase(candidate, gone)
    assert not result.passed
    assert "NEEDS_REBASE" in result.detail


# --- step 4: nearest-template evalmatrix reuse (mechanical selection) --------

from certify.steps import select_evalmatrix_rows  # noqa: E402


def _template(rows):
    return {"id": "report-template", "kind": "template", "version": "1.0.0",
            "evalmatrix": rows}


def test_signature_matching_row_is_applicable(candidate_dir):
    inputs = _inputs()
    candidate = _load(candidate_dir, inputs)
    row = {"id": "row-1",
           "signature": {"slots": [{"name": "level", "type": "value"}],
                         "capability_tags": ["log-source"]}}
    applicable, inapplicable = select_evalmatrix_rows(
        candidate, [_template([row])], LOCK)
    assert [r["id"] for r in applicable] == ["row-1"]
    assert inapplicable == []


def test_slot_mismatch_is_inapplicable_with_reason(candidate_dir):
    inputs = _inputs()
    candidate = _load(candidate_dir, inputs)
    row = {"id": "row-2",
           "signature": {"slots": [{"name": "source", "type": "binding"}],
                         "capability_tags": []}}
    applicable, inapplicable = select_evalmatrix_rows(
        candidate, [_template([row])], LOCK)
    assert applicable == []
    assert len(inapplicable) == 1
    assert inapplicable[0]["id"] == "row-2"
    assert "slot" in inapplicable[0]["failed_check"]


def test_uncovered_tag_is_inapplicable_with_reason(candidate_dir):
    inputs = _inputs()
    candidate = _load(candidate_dir, inputs)
    row = {"id": "row-3",
           "signature": {"slots": [],
                         "capability_tags": ["incident-mgmt"]}}
    applicable, inapplicable = select_evalmatrix_rows(
        candidate, [_template([row])], LOCK)
    assert applicable == []
    assert "capability_tags" in inapplicable[0]["failed_check"]


def test_selection_never_consults_row_prose(candidate_dir):
    """Applicability is a signature check, never a judgment on descriptions."""
    inputs = _inputs()
    candidate = _load(candidate_dir, inputs)
    row = {"id": "row-4",
           "description": "obviously perfect for this candidate, trust me",
           "signature": {"slots": [{"name": "nope", "type": "value"}],
                         "capability_tags": []}}
    applicable, inapplicable = select_evalmatrix_rows(
        candidate, [_template([row])], LOCK)
    assert applicable == []


def test_row_without_signature_is_inapplicable(candidate_dir):
    inputs = _inputs()
    candidate = _load(candidate_dir, inputs)
    applicable, inapplicable = select_evalmatrix_rows(
        candidate, [_template([{"id": "row-5"}])], LOCK)
    assert applicable == []
    assert "signature" in inapplicable[0]["failed_check"]


# --- step 5 (mechanical): structural review ---------------------------------

from certify.steps import step_structural, step_security_static, step_model_diversity  # noqa: E402


def test_structural_passes_on_conformant_candidate(candidate_dir):
    inputs = _inputs()
    result = step_structural(_load(candidate_dir, inputs), inputs)
    assert result.passed, result.detail


def test_structural_fails_without_slot_value_surface(candidate_dir):
    inputs = _inputs()
    write_candidate(candidate_dir, inputs)
    manifest = candidate_dir / "AGENT_MANIFEST.yaml"
    import yaml as _yaml
    data = _yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["slot_value_surface"] = []
    manifest.write_text(_yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    result = step_structural(load_candidate(candidate_dir), inputs)
    assert not result.passed
    assert "slot_value_surface" in result.detail


def test_structural_fails_on_direct_agent_invocation(candidate_dir):
    inputs = _inputs()
    write_candidate(candidate_dir, inputs,
                    src_body="from platform import invoke\n\n\n"
                             "def run():\n    return invoke('shipped-agent', {})\n")
    result = step_structural(load_candidate(candidate_dir), inputs)
    assert not result.passed


# --- step 6 (mechanical): security static scan ------------------------------

def test_security_passes_on_conformant_candidate(candidate_dir):
    inputs = _inputs()
    result = step_security_static(_load(candidate_dir, inputs), inputs)
    assert result.passed, result.detail


def test_security_fails_on_binding_above_ceiling(candidate_dir):
    inputs = _inputs()
    write_candidate(candidate_dir, inputs, bindings=["field_stats"])  # quarantined
    result = step_security_static(load_candidate(candidate_dir), inputs)
    assert not result.passed


def test_security_fails_on_manifest_bypassing_io(candidate_dir):
    inputs = _inputs()
    write_candidate(candidate_dir, inputs,
                    src_body="import socket\n\n\ndef run():\n"
                             "    return socket.create_connection(('h', 80))\n")
    result = step_security_static(load_candidate(candidate_dir), inputs)
    assert not result.passed
    assert "I/O" in result.detail


def test_security_fails_on_secret(candidate_dir):
    inputs = _inputs()
    write_candidate(candidate_dir, inputs,
                    extra_files={"src/cfg.py": "KEY = 'sk-abcdefghijklmnop1234'\n"})
    result = step_security_static(load_candidate(candidate_dir), inputs)
    assert not result.passed


# --- step 6: model-diversity rule -------------------------------------------

IMPL_PROFILE_REAL = {"id": "haiku-class", "kind": "model", "provider": "anthropic",
                     "profile_class": "claude-class"}
IMPL_PROFILE_STUB = {"id": "stub-class-ref", "kind": "model", "provider": "local",
                     "profile_class": "stub-class"}


def test_diversity_passes_when_families_differ():
    result = step_model_diversity(
        judge_model="gpt-4o-mini", criteria_model="gemini-2.0-flash",
        impl_profile=IMPL_PROFILE_REAL)
    assert result.passed, result.detail
    assert "config_hash=" in result.detail


def test_diversity_fails_when_judge_shares_impl_family():
    result = step_model_diversity(
        judge_model="claude-haiku-4-5", criteria_model="gpt-4o-mini",
        impl_profile=IMPL_PROFILE_REAL)
    assert not result.passed
    assert "judge" in result.detail


def test_diversity_fails_when_criteria_author_shares_impl_family():
    result = step_model_diversity(
        judge_model="gpt-4o-mini", criteria_model="claude-haiku-4-5",
        impl_profile=IMPL_PROFILE_REAL)
    assert not result.passed
    assert "criteria" in result.detail


def test_diversity_indeterminate_impl_family_passes_with_note():
    result = step_model_diversity(
        judge_model="gpt-4o-mini", criteria_model="gpt-4o",
        impl_profile=IMPL_PROFILE_STUB)
    assert result.passed
    assert "indeterminate" in result.detail


def test_diversity_fails_when_models_unpinned():
    result = step_model_diversity(
        judge_model="", criteria_model="gpt-4o-mini",
        impl_profile=IMPL_PROFILE_REAL)
    assert not result.passed


def test_diversity_sees_through_namespaced_model_ids():
    """OpenRouter-style `provider/model` ids must not defeat the gate: the
    family is the model, not the routing vendor. `anthropic/claude-3.5-sonnet`
    judging a claude-class implementation is the same-family self-agreement the
    rule exists to catch."""
    result = step_model_diversity(
        judge_model="anthropic/claude-3.5-sonnet", criteria_model="openai/gpt-4o-mini",
        impl_profile=IMPL_PROFILE_REAL)
    assert not result.passed, result.detail
    assert "judge" in result.detail


def test_diversity_passes_for_namespaced_ids_of_different_families():
    result = step_model_diversity(
        judge_model="openai/gpt-4o-mini", criteria_model="meta-llama/llama-3.1-70b",
        impl_profile=IMPL_PROFILE_REAL)
    assert result.passed, result.detail


def test_diversity_handles_local_tagged_model_ids():
    """ollama ids carry a `:tag` suffix — family is still the model name."""
    result = step_model_diversity(
        judge_model="llama3.1:8b", criteria_model="qwen2.5-coder:7b",
        impl_profile=IMPL_PROFILE_REAL)
    assert result.passed, result.detail


def test_diversity_config_hash_is_deterministic():
    r1 = step_model_diversity(judge_model="gpt-4o-mini",
                              criteria_model="gemini-2.0-flash",
                              impl_profile=IMPL_PROFILE_REAL)
    r2 = step_model_diversity(judge_model="gpt-4o-mini",
                              criteria_model="gemini-2.0-flash",
                              impl_profile=IMPL_PROFILE_REAL)
    assert r1.detail == r2.detail


# --- step 7: atomic promotion ------------------------------------------------

import hashlib

from certify.steps import promote  # noqa: E402


def _summary_writer(spec: str, manifest) -> str:
    return "Regenerated: lists error-level log entries from bound sources."


@pytest.fixture
def promo_root(tmp_path: Path) -> Path:
    """Catalog root with tags/trust and one proposed candidate."""
    import yaml as _yaml
    root = tmp_path / "cat"
    (root / "agents" / "_proposed").mkdir(parents=True)
    (root / "tags.yaml").write_text(_yaml.safe_dump(
        {"tags": ["log-source", "log-analysis"]}), encoding="utf-8")
    (root / "trust.yaml").write_text(_yaml.safe_dump(
        {"records": []}), encoding="utf-8")
    cdir = root / "agents" / "_proposed" / "log-error-lister"
    write_candidate(cdir, _inputs())
    # Promotion is downstream of certification: step 3 has run and left its
    # evidence, exactly as it would before an operator sees the promote button.
    assert step_eval(load_candidate(cdir)).passed
    return root


def _promote(root, **over):
    calls = {"lockbuild": 0, "commits": []}

    def fake_lockbuild(r):
        calls["lockbuild"] += 1

    kwargs = dict(
        catalog_root=root,
        candidate_dir=root / "agents" / "_proposed" / "log-error-lister",
        summary_writer=_summary_writer,
        run_lockbuild=fake_lockbuild,
        commit=lambda paths, msg: calls["commits"].append((sorted(map(str, paths)), msg)),
    )
    kwargs.update(over)
    return promote(**kwargs), calls


def test_promotion_moves_candidate_and_generates_entry(promo_root):
    import yaml as _yaml
    result, calls = _promote(promo_root)
    assert result.passed, result.detail
    promoted = promo_root / "agents" / "log-error-lister"
    assert promoted.is_dir()
    assert not (promo_root / "agents" / "_proposed" / "log-error-lister").exists()
    entry = _yaml.safe_load((promoted / "entry.yaml").read_text(encoding="utf-8"))
    assert entry["routing_summary"] == _summary_writer("", None)  # regenerated, not draft
    assert not (promoted / "entry.draft.yaml").exists()


def test_promotion_writes_quarantined_trust_record(promo_root):
    import yaml as _yaml
    result, _ = _promote(promo_root)
    assert result.passed
    records = _yaml.safe_load((promo_root / "trust.yaml").read_text(encoding="utf-8"))["records"]
    assert len(records) == 1
    rec = records[0]
    assert (rec["id"], rec["tier"]) == ("log-error-lister", "quarantined")
    assert rec["model_profile"] == "stub-class-ref"


def test_promotion_moves_trace_content_addressed(promo_root):
    result, _ = _promote(promo_root)
    assert result.passed
    promoted = promo_root / "agents" / "log-error-lister"
    assert not (promoted / "trace").exists()
    stored = list((promo_root / "traces").rglob("*"))
    files = [p for p in stored if p.is_file()]
    assert files, "trace files must be stored content-addressed"
    for f in files:
        assert f.parent.name == hashlib.sha256(f.read_bytes()).hexdigest()


def test_promotion_runs_lockbuild_and_commits_once(promo_root):
    result, calls = _promote(promo_root)
    assert result.passed
    assert calls["lockbuild"] == 1
    assert len(calls["commits"]) == 1
    paths, _msg = calls["commits"][0]
    joined = " ".join(paths)
    assert "trust.yaml" in joined and "agents" in joined


def test_promotion_refuses_unapproved_tag(promo_root):
    import yaml as _yaml
    draft = promo_root / "agents" / "_proposed" / "log-error-lister" / "entry.draft.yaml"
    data = _yaml.safe_load(draft.read_text(encoding="utf-8"))
    data["capability_tags"] = ["log-analysis", "brand-new-tag"]
    draft.write_text(_yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    result, calls = _promote(promo_root)
    assert not result.passed
    assert "brand-new-tag" in result.detail
    assert (promo_root / "agents" / "_proposed" / "log-error-lister").is_dir()
    assert calls["commits"] == []


def test_promotion_grant_is_attributed_to_a_specific_run(promo_root):
    """A constant `granted_by` across every promotion would make trust.yaml's
    append-only history useless for audit — CATALOG §7's own example
    (`certify-run-2026-07-12T09:14Z`) ties a grant to the run that made it."""
    import re as _re
    import yaml as _yaml
    result, _ = _promote(promo_root)
    assert result.passed, result.detail
    rec = _yaml.safe_load((promo_root / "trust.yaml").read_text(encoding="utf-8"))["records"][0]
    assert _re.match(r"certify-run-\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z", rec["granted_by"])


def test_promotion_survives_leftover_source_cleanup_failure(promo_root, monkeypatch):
    """A successful promotion (dest + trust.yaml + lock durably written) must
    not be rolled back just because deleting the now-disposable _proposed/
    source afterward happened to fail — that would destroy the source with no
    promoted copy to show for it, the exact half-state atomicity exists to
    prevent."""
    import shutil as _shutil
    import yaml as _yaml

    real_rmtree = _shutil.rmtree
    candidate_dir = promo_root / "agents" / "_proposed" / "log-error-lister"

    def flaky_rmtree(path, *a, **kw):
        if Path(path) == candidate_dir:
            raise OSError("simulated cleanup failure")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(_shutil, "rmtree", flaky_rmtree)
    result, calls = _promote(promo_root)

    assert result.passed, result.detail
    assert "failed to remove leftover" in result.detail
    promoted = promo_root / "agents" / "log-error-lister"
    assert promoted.is_dir(), "promotion itself must still stand"
    records = _yaml.safe_load((promo_root / "trust.yaml").read_text(encoding="utf-8"))["records"]
    assert len(records) == 1 and records[0]["tier"] == "quarantined"
    assert calls["commits"], "a durable promotion must still be committed"


def test_promotion_failure_leaves_no_half_state(promo_root):
    import yaml as _yaml

    def exploding_lockbuild(root):
        raise RuntimeError("lock inconsistency")

    result, calls = _promote(promo_root, run_lockbuild=exploding_lockbuild)
    assert not result.passed
    # source intact, nothing published, trust rolled back, no commit
    assert (promo_root / "agents" / "_proposed" / "log-error-lister").is_dir()
    assert not (promo_root / "agents" / "log-error-lister").exists()
    records = _yaml.safe_load((promo_root / "trust.yaml").read_text(encoding="utf-8"))["records"]
    assert records == []
    assert calls["commits"] == []


# --- step 7 gate: eval evidence (layer 7 [M]) ---------------------------------

def _candidate_dir(root: Path) -> Path:
    return root / "agents" / "_proposed" / "log-error-lister"


def test_promotion_refuses_a_candidate_whose_eval_never_ran(promo_root):
    (_candidate_dir(promo_root) / "trace" / EVIDENCE_NAME).unlink()
    result, calls = _promote(promo_root)
    assert not result.passed
    assert "step 3 has not run" in result.detail
    assert not (promo_root / "agents" / "log-error-lister").exists()
    assert calls["commits"] == []


def test_promotion_refuses_red_eval_evidence(promo_root):
    cdir = _candidate_dir(promo_root)
    evidence = load_eval_evidence(cdir)
    evidence.update(passed=False, detail="failing criteria: ['BC-2']")
    persist_eval_evidence(cdir, evidence)
    result, _ = _promote(promo_root)
    assert not result.passed
    assert "BC-2" in result.detail


def test_promotion_refuses_evidence_from_another_profile(promo_root):
    """Layer 8: a class migration reruns eval; it never inherits the evidence."""
    cdir = _candidate_dir(promo_root)
    evidence = load_eval_evidence(cdir)
    evidence["model_profile"] = "some-other-profile"
    persist_eval_evidence(cdir, evidence)
    result, _ = _promote(promo_root)
    assert not result.passed
    assert "some-other-profile" in result.detail


def test_trust_record_points_at_the_stored_eval_evidence(promo_root):
    """The record cites the artifact rather than asserting the gate passed: the
    digest is the traces/ directory the evidence was filed under."""
    import yaml as _yaml
    result, _ = _promote(promo_root)
    assert result.passed, result.detail
    record = _yaml.safe_load(
        (promo_root / "trust.yaml").read_text(encoding="utf-8"))["records"][0]
    digest = record["evidence"].split("eval=")[1].split()[0]
    assert (promo_root / "traces" / digest / EVIDENCE_NAME).is_file()
