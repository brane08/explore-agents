"""
Tests for the provider-neutral LLM seam (loop/llm.py) and its wiring into
the generator. No real provider is contacted — a fake Completer is injected.
"""
from __future__ import annotations

import ast

import pytest

from loop import llm
from loop.generator import generate_candidate, make_llm_backend
from loop.spec import TaskSpec, ToolSchema

_SPEC = TaskSpec(
    task_description="Analyse error rate",
    mcp_server_url="http://localhost:8000",
    available_tools=[ToolSchema(name="es_search", description="Search", input_schema={})],
    eval_case_ids=["search_error_level"],
    max_iterations=5,
)


def _fake_completer(captured: list[str]):
    """A Completer that records the prompt and returns a fixed valid agent."""
    def complete(prompt: str) -> str:
        captured.append(prompt)
        return "```python\nprint('hello from any provider')\n```"
    return complete


def test_make_llm_backend_returns_prompt_and_stripped_source():
    captured: list[str] = []
    backend = make_llm_backend(_fake_completer(captured))
    prompt, source = backend(_SPEC, feedback="")
    assert captured == [prompt]                 # completer saw the built prompt
    assert "```" not in source                  # fences stripped
    ast.parse(source)                           # valid python


def test_backend_is_provider_agnostic_through_generate_candidate(tmp_path):
    captured: list[str] = []
    backend = make_llm_backend(_fake_completer(captured))
    result = generate_candidate(_SPEC, iteration=0, generator_fn=backend,
                                generated_base_dir=tmp_path)
    assert result.path.read_text().startswith("print(")
    assert "es_search" in result.prompt_sent    # tool surfaced in the prompt


def test_select_completer_none_without_config(monkeypatch):
    for var in ("B2A_LLM_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "B2A_OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    assert llm.select_completer() is None        # → caller falls back to stub


def test_select_completer_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("B2A_LLM_PROVIDER", "nope")
    with pytest.raises(llm.CompleterError, match="Unknown"):
        llm.select_completer()


def test_select_completer_dispatches_by_provider(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("B2A_LLM_PROVIDER", "openai")
    sentinel = object()
    monkeypatch.setitem(llm._BUILDERS, "openai", lambda: sentinel)
    assert llm.select_completer() is sentinel
