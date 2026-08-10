"""CHECKLISTS.md layer 1 [M] — catalog entry authoring, enforced at lockbuild stage 1."""
from __future__ import annotations

import shutil

import pytest

from catalog_fixtures import edit_entry, w, wyaml
from lockbuild.build import build_lock
from lockbuild.errors import LockbuildError
from tests_util import build_text  # thin helper defined in tests_util.py


def test_valid_catalog_builds(catalog):
    lock = build_lock(catalog, built_from="0" * 40, built_at="2026-07-15T00:00:00+00:00")
    ids = {e["id"] for e in lock["entries"]}
    assert {"greet", "farewell", "echo-tool", "opus-class-ref",
            "report-template", "composed", "report-instance", "pair"} == ids


def test_unknown_field_fails(catalog):
    edit_entry(catalog, "skills/greet", flavour="spicy")
    with pytest.raises(LockbuildError, match="flavour"):
        build_text(catalog)


def test_unknown_kind_fails(catalog):
    edit_entry(catalog, "skills/greet", kind="wizard")
    with pytest.raises(LockbuildError, match="kind"):
        build_text(catalog)


def test_kind_dir_mismatch_fails(catalog):
    # a model entry authored under skills/ must not pass
    edit_entry(catalog, "skills/farewell", kind="model", provider="x",
               endpoint_class="local", capabilities={}, cost_class="low",
               profile_class="c", status="active")
    with pytest.raises(LockbuildError, match="skills/"):
        build_text(catalog)


def test_duplicate_id_fails(catalog):
    edit_entry(catalog, "skills/farewell", id="greet")
    with pytest.raises(LockbuildError, match="duplicate id"):
        build_text(catalog)


def test_bad_semver_fails(catalog):
    edit_entry(catalog, "skills/greet", version="v1")
    with pytest.raises(LockbuildError, match="semver"):
        build_text(catalog)


def test_unknown_capability_tag_fails(catalog):
    edit_entry(catalog, "skills/greet", capability_tags=["not-a-real-tag"])
    with pytest.raises(LockbuildError, match="not-a-real-tag"):
        build_text(catalog)


def test_proposed_tag_is_allowed(catalog):
    edit_entry(catalog, "skills/greet",
               capability_tags=[{"name": "brand-new-tag", "proposed": True}])
    lock = build_lock(catalog, built_from="0" * 40, built_at="x")
    greet = next(e for e in lock["entries"] if e["id"] == "greet")
    assert "brand-new-tag" in greet["capability_tags"]


def test_trust_tier_in_entry_fails(catalog):
    edit_entry(catalog, "skills/greet", trust_tier="validated")
    with pytest.raises(LockbuildError, match="trust_tier"):
        build_text(catalog)


def test_binding_to_agent_kind_fails(catalog):
    # bindings may reference only skill|mcp-tool (CATALOG §4 hard rule)
    edit_entry(catalog, "agents/composed",
               bindings=[{"id": "report-instance", "version": "1.0.0"}])
    with pytest.raises(LockbuildError, match="binding"):
        build_text(catalog)


def test_mcp_entry_missing_snapshot_fails(catalog):
    (catalog / "mcp" / "echo-tool" / "schema.snapshot.json").unlink()
    with pytest.raises(LockbuildError, match="schema.snapshot.json"):
        build_text(catalog)


def test_skill_missing_skill_md_fails(catalog):
    (catalog / "skills" / "greet" / "SKILL.md").unlink()
    with pytest.raises(LockbuildError, match="SKILL.md"):
        build_text(catalog)


def test_external_kind_requires_regenerated_provenance(catalog):
    edit_entry(catalog, "mcp/echo-tool", routing_summary_provenance=None)
    with pytest.raises(LockbuildError, match="provenance"):
        build_text(catalog)


def test_external_summary_comes_from_regenerated_file(catalog):
    lock = build_lock(catalog, built_from="0" * 40, built_at="x")
    echo = next(e for e in lock["entries"] if e["id"] == "echo-tool")
    assert echo["routing_summary"] == "Mirrored MCP tool echo-tool."
