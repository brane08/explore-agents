"""Role A harness over an OpenAI-compatible model (ORCH_HARNESS=openai-agent).

One structured completion returns the §6 file map; the harness writes it. This
is viable precisely because the playbook fixes the layout — the model is not
choosing paths, so path choice can be treated as untrusted input and refused.

Every model call is mocked (httpx.MockTransport): no creds, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from certify.playbook import HarnessInputs, criteria_hash
from orchestrator.dispatch import openai_agent_harness, select_harness

LOCK = {"lock_version": 3, "entries": [
    {"id": "log-reader", "kind": "skill", "version": "1.0.0", "hash": "h" * 64,
     "trust_tier": "validated", "routing_summary": "reads logs",
     "capability_tags": ["logs"], "detail": "d"},
]}


def _inputs(**over) -> HarnessInputs:
    base = dict(
        task_spec="list error-level log records",
        behavioral_criteria="BC-1: only ERROR records.\n",
        catalog_ref="a" * 40,
        capability_manifest=LOCK,
        trust_tier_ceiling="validated",
        mode="B2b",
        scope="full-agent",
        residual=None,
        nearest_templates=[],
        model_profile="stub-class-ref",
        harness="openai-agent/1",
        target_runtime="langgraph-py311",
    )
    base.update(over)
    return HarnessInputs(**base)


def _manifest_yaml(status: str = "PARTIAL") -> str:
    return yaml.safe_dump({
        "agent_id": "b2b-test", "kind": "agent", "mode": "B2b",
        "scope": "full-agent", "playbook_version": "3-beta",
        "harness": "openai-agent/1", "model_profile": "stub-class-ref",
        "target_runtime": "langgraph-py311", "catalog_ref": "a" * 40,
        "behavioral_criteria_ref": criteria_hash("BC-1: only ERROR records.\n"),
        "interface_criteria_ref": criteria_hash("IC-1: returns a result object."),
        "bindings": [{"id": "log-reader", "version": "1.0.0", "schema_hash": "h" * 64}],
        "trust_tier_consumed": "validated", "slot_value_surface": [],
        "status": status,
    }, sort_keys=True)


def _client(payload, capture: dict | None = None) -> httpx.Client:
    """Client whose completion returns `payload` as the assistant message."""
    content = payload if isinstance(payload, str) else json.dumps(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.update(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 10},
        })

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")


# --- happy path -------------------------------------------------------------

def test_writes_the_returned_file_map(tmp_path: Path):
    files = {"AGENT_MANIFEST.yaml": _manifest_yaml(),
             "SPEC.md": "# Spec\n",
             "src/graph.py": "def run(client, **p):\n    return {}\n"}
    outcome = openai_agent_harness(
        _inputs(), tmp_path, client=_client({"files": files, "codes": []}))

    assert outcome.status == "PARTIAL", outcome.codes
    assert (tmp_path / "SPEC.md").read_text() == "# Spec\n"
    assert (tmp_path / "src" / "graph.py").is_file(), "nested paths must be created"


def test_status_comes_from_the_written_manifest_not_the_model_claim(tmp_path: Path):
    """CATALOG §10: artifacts are the evidence. A model claiming COMPLETE while
    writing a PARTIAL manifest must not be able to upgrade its own result."""
    outcome = openai_agent_harness(
        _inputs(), tmp_path,
        client=_client({"files": {"AGENT_MANIFEST.yaml": _manifest_yaml("PARTIAL")},
                        "status": "COMPLETE", "codes": []}))
    assert outcome.status == "PARTIAL"


def test_warn_codes_are_surfaced(tmp_path: Path):
    outcome = openai_agent_harness(
        _inputs(), tmp_path,
        client=_client({"files": {"AGENT_MANIFEST.yaml": _manifest_yaml()},
                        "codes": ["WARN: bound one tool only"]}))
    assert any("WARN" in c for c in outcome.codes)


# --- the model's paths are untrusted input ----------------------------------

@pytest.mark.parametrize("bad", [
    "../escaped.py",
    "../../etc/passwd",
    "/etc/passwd",
    "src/../../out.py",
])
def test_paths_escaping_the_scratch_dir_are_refused(tmp_path: Path, bad: str):
    out = tmp_path / "scratch"
    out.mkdir()
    outcome = openai_agent_harness(
        _inputs(), out,
        client=_client({"files": {bad: "pwned", "AGENT_MANIFEST.yaml": _manifest_yaml()}}))

    assert outcome.status == "ERROR", f"{bad!r} must be refused"
    assert not (tmp_path / "escaped.py").exists()
    assert not (tmp_path / "out.py").exists()
    # refusal is total: a rejected map writes nothing at all
    assert not (out / "AGENT_MANIFEST.yaml").exists()


def test_absolute_path_writes_nothing_outside(tmp_path: Path):
    out = tmp_path / "scratch"
    out.mkdir()
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("original")
    openai_agent_harness(
        _inputs(), out, client=_client({"files": {str(sentinel): "pwned"}}))
    assert sentinel.read_text() == "original"


# --- malformed model output --------------------------------------------------

def test_non_json_reply_is_an_error(tmp_path: Path):
    outcome = openai_agent_harness(_inputs(), tmp_path, client=_client("I refuse."))
    assert outcome.status == "ERROR"
    assert any("parseable" in c or "JSON" in c for c in outcome.codes)


def test_missing_manifest_is_an_error(tmp_path: Path):
    outcome = openai_agent_harness(
        _inputs(), tmp_path, client=_client({"files": {"SPEC.md": "# Spec\n"}}))
    assert outcome.status == "ERROR"


def test_fenced_json_is_tolerated(tmp_path: Path):
    """Models wrap JSON in ``` fences constantly; that is formatting, not failure."""
    body = json.dumps({"files": {"AGENT_MANIFEST.yaml": _manifest_yaml()}})
    outcome = openai_agent_harness(
        _inputs(), tmp_path, client=_client(f"```json\n{body}\n```"))
    assert outcome.status == "PARTIAL", outcome.codes


def test_missing_input_refuses_before_calling_the_model(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("model must not be called on incomplete §0 inputs")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    outcome = openai_agent_harness(_inputs(task_spec=""), tmp_path, client=client)
    assert outcome.status == "ERROR"
    assert any("MISSING_INPUT" in c for c in outcome.codes)


# --- prompt + selection ------------------------------------------------------

def test_prompt_carries_the_playbook_and_frozen_inputs(tmp_path: Path, monkeypatch):
    playbook = tmp_path / "pb.md"
    playbook.write_text("# PLAYBOOK BODY MARKER\n", encoding="utf-8")
    monkeypatch.setenv("ORCH_PLAYBOOK", str(playbook))
    seen: dict = {}
    out = tmp_path / "o"
    out.mkdir()
    openai_agent_harness(
        _inputs(), out,
        client=_client({"files": {"AGENT_MANIFEST.yaml": _manifest_yaml()}}, capture=seen))

    prompt = "\n".join(m["content"] for m in seen["messages"])
    assert "PLAYBOOK BODY MARKER" in prompt
    assert "list error-level log records" in prompt
    assert "BC-1: only ERROR records." in prompt
    assert "a" * 40 in prompt                      # CATALOG_REF pin travels
    assert "log-reader" in prompt                  # CAPABILITY_MANIFEST travels


def test_select_harness_exposes_the_backend(monkeypatch):
    monkeypatch.setenv("ORCH_HARNESS", "openai-agent")
    assert select_harness() is openai_agent_harness
