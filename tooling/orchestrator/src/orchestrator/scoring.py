"""Match / post-match-confirmation scorer seam.

`Scorer(task_text, candidate_text) -> 0..1`. Same pattern as the Completer
seam: a deterministic lexical stub for dev/tests, a model-backed scorer
selected by env when credentials exist. Any callable can be injected.

The criteria-author prompt (ROADMAP Phase 1 pending design) rides the same
seam when it lands — both are independent cheap model calls at routing time.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable

from orchestrator.openai_compat import build_client, resolve_endpoint

Scorer = Callable[[str, str], float]

_TOKEN = re.compile(r"[a-z]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) > 2}


def lexical_scorer(task_text: str, candidate_text: str) -> float:
    """Deterministic stub: token containment of the task in the candidate.
    Not a semantic judgment — a placeholder with the same interface."""
    task = _tokens(task_text)
    if not task:
        return 0.0
    return len(task & _tokens(candidate_text)) / len(task)


_PROMPT = (
    "Score 0.00-1.00 how completely this capability description covers the task. "
    "Reply with only the number.\nTask: {task}\nCapability: {cap}"
)


def anthropic_scorer(model: str | None = None) -> Scorer:
    from anthropic import Anthropic  # optional dependency, resolved lazily

    client = Anthropic()
    model = model or os.environ.get("ORCH_SCORER_MODEL", "claude-haiku-4-5-20251001")

    def score(task_text: str, candidate_text: str) -> float:
        message = client.messages.create(
            model=model,
            max_tokens=8,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(task=task_text, cap=candidate_text),
            }],
        )
        text = next(b.text for b in message.content if b.type == "text")
        try:
            return max(0.0, min(1.0, float(text.strip())))
        except ValueError:
            return 0.0

    return score


def openai_scorer(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    client=None,
) -> Scorer:
    """OpenAI / OpenAI-compatible chat-completions scorer.

    Uses `httpx` (lazy import — optional, same contract as `anthropic_scorer`), so
    any OpenAI-compatible endpoint works via `base_url`: OpenAI, OpenRouter, a
    proxy, or a locally served model (ollama / llama.cpp / vLLM). `client` is
    injectable for tests. Same 0..1 interface as the stub.

    Credential and endpoint rules live in `openai_compat` — shared with the
    Role A harness so the two seams cannot drift apart on them.
    """
    model = model or os.environ.get("ORCH_SCORER_MODEL") or "gpt-4o-mini"
    if client is None:
        client, headers = build_client(api_key, base_url, purpose="ORCH_SCORER=openai")
    else:
        _, headers = resolve_endpoint(api_key, base_url, purpose="ORCH_SCORER=openai")

    def score(task_text: str, candidate_text: str) -> float:
        resp = client.post(
            "/chat/completions",
            headers=headers,
            json={
                "model": model,
                "max_tokens": 8,
                "temperature": 0,
                "messages": [
                    {"role": "user", "content": _PROMPT.format(task=task_text, cap=candidate_text)}
                ],
            },
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        try:
            return max(0.0, min(1.0, float(text.strip())))
        except (ValueError, TypeError):
            return 0.0

    return score


def select_scorer() -> Scorer:
    backend = os.environ.get("ORCH_SCORER", "stub")
    if backend == "stub":
        return lexical_scorer
    if backend == "anthropic":
        return anthropic_scorer()
    if backend == "openai":
        return openai_scorer()
    raise ValueError(
        f"unknown ORCH_SCORER backend {backend!r}; have stub, anthropic, openai"
    )
