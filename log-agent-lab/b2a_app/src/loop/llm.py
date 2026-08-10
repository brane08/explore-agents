"""
llm.py — provider-neutral text-completion seam for the generator.

The generator's prompt is plain text; the only provider-specific part is
"given a prompt, return the completion text". That seam is a `Completer`:
a synchronous callable ``(prompt: str) -> str``. Concrete providers are
adapted to it below.

Adding a provider = write one `Completer` builder and register it in
`_BUILDERS`. Nothing else in the loop changes. You can also inject your own
`Completer` directly (e.g. a local model, a test double) via
`generate_candidate(..., generator_fn=make_llm_backend(my_completer))`.

Provider + model are chosen from the environment:
  B2A_LLM_PROVIDER   anthropic | openai        (unset → inferred from creds)
  B2A_MODEL          model id for that provider (each backend has a default)
"""
from __future__ import annotations

import os
from typing import Callable

# A Completer turns a prompt into completion text. Sync, provider-agnostic.
Completer = Callable[[str], str]


class CompleterError(RuntimeError):
    """Raised when a provider backend can't be constructed."""


# ---------------------------------------------------------------------------
# Anthropic (Claude)
# ---------------------------------------------------------------------------

def anthropic_completer() -> Completer:
    """Claude via the official Anthropic SDK. Auth resolves from the
    environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant` profile)."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - import guard
        raise CompleterError("anthropic package not installed") from exc

    client = anthropic.Anthropic()
    model = os.environ.get("B2A_MODEL", "claude-opus-4-8")

    def complete(prompt: str) -> str:
        message = client.messages.create(
            model=model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        # content is a list of blocks; return the first text block's text
        return next(b.text for b in message.content if b.type == "text")

    return complete


# ---------------------------------------------------------------------------
# OpenAI-compatible — OpenAI, Ollama, vLLM, LM Studio, OpenRouter, local, ...
# ---------------------------------------------------------------------------

def openai_completer() -> Completer:
    """Any OpenAI-compatible chat endpoint. Point B2A_OPENAI_BASE_URL at a
    local/self-hosted server to use it without changing code."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - import guard
        raise CompleterError("openai package not installed") from exc

    client = OpenAI(
        base_url=os.environ.get("B2A_OPENAI_BASE_URL") or None,
        api_key=os.environ.get("B2A_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or "not-needed",  # local servers often ignore the key
    )
    model = os.environ.get("B2A_MODEL", "gpt-4o")

    def complete(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""

    return complete


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

_BUILDERS: dict[str, Callable[[], Completer]] = {
    "anthropic": anthropic_completer,
    "openai": openai_completer,
}


def select_completer() -> Completer | None:
    """Pick a Completer from B2A_LLM_PROVIDER, or infer one from available
    credentials. Returns None when nothing is configured — the caller then
    falls back to the deterministic stub generator (no API key needed)."""
    provider = os.environ.get("B2A_LLM_PROVIDER", "").strip().lower()
    if provider:
        builder = _BUILDERS.get(provider)
        if builder is None:
            raise CompleterError(
                f"Unknown B2A_LLM_PROVIDER={provider!r}. Known: {sorted(_BUILDERS)}"
            )
        return builder()

    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic_completer()
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("B2A_OPENAI_BASE_URL"):
        return openai_completer()
    return None
