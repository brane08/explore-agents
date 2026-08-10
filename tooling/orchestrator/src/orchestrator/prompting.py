"""Shared prompt hygiene for the routing-time model calls.

Both routing-time seams put untrusted text in front of a model: the scorer sees
catalog `routing_summary`/`detail` (some of it authored by generated
candidates), the criteria author sees the user's task. Both defend the same way
— instructions in a system message, data inside delimiters it cannot close, a
length cap — so the control has one implementation instead of one per seam,
and a fix to it cannot land on only half the surface.
"""
from __future__ import annotations

from collections.abc import Iterable

# Catalog `detail` fields and user task specs are both unbounded; a single
# oversized one would otherwise blow the context (and the bill) of every route.
MAX_FIELD_CHARS = 4000


def fence(text: str, delims: Iterable[str], limit: int = MAX_FIELD_CHARS) -> str:
    """Neutralize the block delimiters and cap the length of one field.

    The angle bracket is replaced with a lookalike, so injected markup stays
    legible to the model as *content* — it is still read, just not obeyed.
    """
    clean = text or ""
    for delim in delims:
        clean = clean.replace(delim, delim.replace("<", "‹"))
    if len(clean) > limit:
        clean = clean[:limit] + "\n…[truncated]"
    return clean
