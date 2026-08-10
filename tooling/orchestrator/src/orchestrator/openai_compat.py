"""Shared OpenAI-compatible endpoint construction.

One place decides what "OpenAI-compatible" means here, so the `Scorer` seam and
the Role A harness cannot drift apart on credentials or endpoint selection.

A key is mandatory only against the default endpoint. An explicit base_url is
the operator saying "not api.openai.com" — OpenRouter, a proxy, or a locally
served model (ollama / llama.cpp / vLLM), the last of which has no credential
at all. No credential is ever invented, and no empty Authorization header is
sent: it is omitted entirely, which keyless servers require.
"""
from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def resolve_endpoint(
    api_key: str | None = None,
    base_url: str | None = None,
    *,
    purpose: str,
) -> tuple[str, dict[str, str]]:
    """Return `(base_url, headers)`. Raises if the default endpoint has no key."""
    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key and not base_url:
        raise RuntimeError(
            f"OPENAI_API_KEY required for {purpose} "
            "(or set OPENAI_BASE_URL for a keyless local endpoint)"
        )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return base_url or DEFAULT_BASE_URL, headers


def build_client(api_key: str | None = None, base_url: str | None = None,
                 *, purpose: str, timeout: float = 30.0):
    """`(httpx.Client, headers)` for an OpenAI-compatible chat endpoint."""
    import httpx  # optional dependency, resolved lazily

    resolved, headers = resolve_endpoint(api_key, base_url, purpose=purpose)
    return httpx.Client(base_url=resolved, timeout=timeout), headers
