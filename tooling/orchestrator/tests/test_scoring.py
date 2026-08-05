"""Scorer seam: stub default, backend selection, OpenAI-compatible scorer.

The real HTTP call is mocked via an injected httpx client — no creds, no network.
"""
from __future__ import annotations

import httpx
import pytest

from orchestrator.scoring import lexical_scorer, openai_scorer, select_scorer


def _mock_client(reply: str) -> httpx.Client:
    """httpx client whose every request returns an OpenAI-shaped chat completion."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


# --- backend selection ------------------------------------------------------

def test_select_scorer_defaults_to_stub(monkeypatch):
    monkeypatch.delenv("ORCH_SCORER", raising=False)
    assert select_scorer() is lexical_scorer


def test_select_scorer_stub_explicit(monkeypatch):
    monkeypatch.setenv("ORCH_SCORER", "stub")
    assert select_scorer() is lexical_scorer


def test_select_scorer_openai(monkeypatch):
    monkeypatch.setenv("ORCH_SCORER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    scorer = select_scorer()
    assert callable(scorer) and scorer is not lexical_scorer


def test_select_scorer_unknown_backend_hard_errors(monkeypatch):
    monkeypatch.setenv("ORCH_SCORER", "bogus")
    with pytest.raises(ValueError, match="bogus"):
        select_scorer()


# --- openai-compatible scorer ----------------------------------------------

def test_openai_scorer_parses_score():
    score = openai_scorer(model="gpt-x", api_key="sk", client=_mock_client("0.82"))
    assert score("summarize this", "summarization capability") == pytest.approx(0.82)


def test_openai_scorer_clamps_out_of_range():
    assert openai_scorer(api_key="k", client=_mock_client("1.7"))("t", "c") == 1.0
    assert openai_scorer(api_key="k", client=_mock_client("-0.3"))("t", "c") == 0.0


def test_openai_scorer_non_numeric_reply_is_zero():
    assert openai_scorer(api_key="k", client=_mock_client("about 0.5"))("t", "c") == 0.0


def test_openai_scorer_requires_api_key_against_the_default_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        openai_scorer(client=_mock_client("0.5"))


def test_local_endpoint_needs_no_key_and_sends_no_auth_header(monkeypatch):
    """A locally served OpenAI-compatible model (ollama/llama.cpp/vLLM) has no
    key. An explicit base_url is the operator saying 'not api.openai.com'; the
    key requirement must not force a fake credential."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "0.6"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://local/v1")
    assert openai_scorer(model="qwen2.5", client=client)("t", "c") == pytest.approx(0.6)
    assert seen["auth"] is None, "no credential must be invented for a local endpoint"


def test_openai_scorer_honours_base_url_and_model(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        import json
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "0.4"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw/v1")
    openai_scorer(model="local-model", api_key="tok", client=client)("t", "c")
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer tok"
    assert seen["model"] == "local-model"
