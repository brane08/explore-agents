"""The harness conformance list (CATALOG §10) — one reader, one writer.

`certify conformance --record` writes it; dispatch and certify step 1 read it
to refuse unlisted harnesses. Both live here so the format cannot drift into
two implementations that disagree about what "listed" means.

The file is generated, never hand-authored: an entry added by hand asserts
conformance nobody measured.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

LIST_FILENAME = "conformance.yaml"


def load_listed(catalog_root: Path) -> dict[str, dict]:
    """Conformant harness id -> record. Absent file = empty list, not an error:
    a fresh catalog has certified no harness yet."""
    path = Path(catalog_root) / LIST_FILENAME
    if not path.is_file():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {r["harness"]: r for r in doc.get("harnesses", [])
            if r.get("conformant") is True}


def is_listed(catalog_root: Path, harness: str) -> bool:
    return harness in load_listed(catalog_root)


def record(catalog_root: Path, harness: str, conformant: bool, report: str) -> Path:
    """Upsert one harness's result. A re-run that now fails must be able to
    *unlist* a harness, so this replaces rather than appends."""
    path = Path(catalog_root) / LIST_FILENAME
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}
    harnesses = [h for h in doc.get("harnesses", []) if h.get("harness") != harness]
    harnesses.append({
        "harness": harness,
        "conformant": conformant,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report": report,
    })
    doc["harnesses"] = sorted(harnesses, key=lambda h: h["harness"])
    path.write_text(yaml.safe_dump(doc, sort_keys=True), encoding="utf-8")
    return path
