"""CATALOG §11 structured error vocabulary + residual hygiene.

Error records persist only *normalized capability statements*, never raw user
text — the persisted corpus is the B2 backlog / template-gap signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CODES = (
    "PATTERN_UNRECOGNIZED",
    "SLOT_UNFILLED",
    "SLOT_INVALID",
    "DELTA_INSUFFICIENT",
    "MODE_MISMATCH",
    "MISSING_INPUT",
    "WARN_DELEGATION_REQUIRED",
    "WARN_CRITERIA_ISSUE",
    "WARN_SPEC_TENSION",
    "STALE_ENTRY",
)

WARN_CODES = tuple(c for c in CODES if c.startswith("WARN_"))


@dataclass
class StructuredError:
    code: str
    context: str = ""

    def __post_init__(self) -> None:
        if self.code not in CODES:
            raise ValueError(f"unknown error code {self.code!r}")


_STOPWORDS = frozenset(
    "a an and are as at be by can could do for from get give how i in is it its "
    "me my of on or our please should show that the their them then this to us "
    "want we what when which will with would you your".split()
)
_TOKEN = re.compile(r"[a-z]+")


def normalize_residual(task_text: str, max_terms: int = 8) -> str:
    """Hygiene pass: strip raw user text down to a normalized capability
    statement (lowercase capability terms, deduplicated, sorted, capped) —
    no quoted strings, numbers, or identifiers survive."""
    terms = sorted(
        {t for t in _TOKEN.findall(task_text.lower()) if t not in _STOPWORDS and len(t) > 2}
    )[:max_terms]
    return "capability needed: " + (", ".join(terms) if terms else "(none extracted)")
