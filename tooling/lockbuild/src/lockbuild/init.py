"""`lockbuild init` — scaffold an empty catalog root (CATALOG §2 layout).

Idempotent and additive only: creates the content dirs plus empty
`tags.yaml`/`trust.yaml` if missing, never touches an existing file or
`catalog.lock.yaml` (that's `lockbuild build`'s job, run once content exists).
"""
from __future__ import annotations

from pathlib import Path

from lockbuild.schemas import DIR_KINDS

TAGS_TEMPLATE = (
    "# Controlled capability_tags vocabulary — lockbuild validates entries "
    "against this list.\n"
    "# Extend via PR (human-reviewed). Proposed tags from entry.draft.yaml "
    "require approval.\n"
    "tags: []\n"
)

TRUST_TEMPLATE = (
    "# Orchestrator-owned trust ledger. Written ONLY by tooling/certify or "
    "recorded human\n"
    "# override. Append-only. Key: (id, version, model_profile). See "
    "docs/CATALOG.md §7.\n"
    "records: []\n"
)


def bootstrap(root: Path) -> list[Path]:
    """Create the catalog skeleton under `root`. Returns paths it created."""
    created: list[Path] = []

    for dirname in DIR_KINDS:
        d = root / dirname
        if not d.is_dir():
            d.mkdir(parents=True)
            created.append(d)
        keep = d / ".gitkeep"
        if not any(d.iterdir()):
            keep.touch()
            created.append(keep)

    tags = root / "tags.yaml"
    if not tags.is_file():
        tags.parent.mkdir(parents=True, exist_ok=True)
        tags.write_text(TAGS_TEMPLATE, encoding="utf-8")
        created.append(tags)

    trust = root / "trust.yaml"
    if not trust.is_file():
        trust.parent.mkdir(parents=True, exist_ok=True)
        trust.write_text(TRUST_TEMPLATE, encoding="utf-8")
        created.append(trust)

    return created
