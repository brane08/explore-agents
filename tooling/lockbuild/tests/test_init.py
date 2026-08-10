from __future__ import annotations

from pathlib import Path

import yaml

from lockbuild.init import bootstrap
from lockbuild.schemas import DIR_KINDS


def test_bootstrap_creates_dirs_and_files(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    created = bootstrap(root)

    for dirname in DIR_KINDS:
        assert (root / dirname).is_dir()
    assert (root / "tags.yaml").is_file()
    assert (root / "trust.yaml").is_file()
    assert created  # something was actually written


def test_bootstrap_content_is_valid_empty_catalog(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    bootstrap(root)

    tags = yaml.safe_load((root / "tags.yaml").read_text(encoding="utf-8"))
    trust = yaml.safe_load((root / "trust.yaml").read_text(encoding="utf-8"))
    assert tags == {"tags": []}
    assert trust == {"records": []}


def test_bootstrap_is_idempotent_and_never_overwrites(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    bootstrap(root)

    (root / "tags.yaml").write_text("tags: [custom]\n", encoding="utf-8")
    (root / "skills" / "greet").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "greet" / "SKILL.md").write_text("hand-authored\n", encoding="utf-8")

    bootstrap(root)

    assert yaml.safe_load((root / "tags.yaml").read_text(encoding="utf-8")) == {"tags": ["custom"]}
    assert (root / "skills" / "greet" / "SKILL.md").read_text(encoding="utf-8") == "hand-authored\n"


def test_bootstrap_from_lockbuild_build_produces_empty_lock(tmp_path: Path) -> None:
    from lockbuild.build import build_lock

    root = tmp_path / "catalog"
    bootstrap(root)

    lock = build_lock(root, built_from="0" * 40, built_at="2026-07-15T00:00:00+00:00")
    assert lock["entries"] == []
