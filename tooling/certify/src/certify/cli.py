"""certify CLI.

`certify conformance` runs the layer-12 fixture suite against a harness and,
with `--record`, writes the result to the catalog's conformance list. The list
is generated here and nowhere else: hand-adding a harness would assert
conformance nobody measured, which is exactly what CATALOG §10 forbids.

`certify validate` promotes a quarantined entry to validated based on supervised
run evidence from the orchestrator.
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from certify.conformance import run_conformance
from certify.conformance_list import LIST_FILENAME, record
from certify.validate import promote_to_validated


def _load_backend(spec: str):
    """'module:attr' → callable. Keeps certify free of any dependency on the
    orchestrator that owns the real backends."""
    module_name, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit(f"--backend must be 'module:attr', got {spec!r}")
    try:
        return getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise SystemExit(f"cannot load backend {spec!r}: {exc}") from exc


def _run_lockbuild(root: Path) -> None:
    from lockbuild.build import LOCK_FILENAME, build_lock, render_lock
    (root / LOCK_FILENAME).write_text(render_lock(build_lock(root)), encoding="utf-8")


def _git_commit(paths: list[Path], message: str) -> None:
    """Stage exactly `paths` — never `-A`, which would sweep in whatever else
    is dirty in the checkout and make the promotion commit unauditable
    (CATALOG §8 step 7: "commit atomically" means atomic to the promotion,
    not to the whole tree)."""
    root = paths[0].parent if paths else Path(".")
    existing = [str(p) for p in paths if p.exists()]
    subprocess.run(["git", "-C", str(root), "add", "--", *existing],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message], check=True, capture_output=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="certify")
    sub = parser.add_subparsers(dest="command", required=True)

    conf = sub.add_parser("conformance", help="run the layer-12 fixture suite")
    conf.add_argument("--root", type=Path, default=Path("."), help="catalog root")
    conf.add_argument("--harness", required=True, help="harness id + version, e.g. claude-code/2.0.1")
    conf.add_argument("--backend", required=True,
                      help="'module:attr' seam, e.g. orchestrator.dispatch:claude_cli_harness")
    conf.add_argument("--lock", type=Path, default=None,
                      help="catalog.lock.yaml to drive fixtures with (default: <root>/catalog.lock.yaml)")
    conf.add_argument("--record", action="store_true",
                      help="write the outcome to the catalog conformance list")

    val = sub.add_parser("validate", help="promote quarantined to validated from supervised evidence")
    val.add_argument("--root", type=Path, default=Path("."), help="catalog root")
    val.add_argument("--entry", required=True, help="entry id")
    val.add_argument("--version", required=True, help="entry version")
    val.add_argument("--profile", required=True, help="model profile")
    val.add_argument("--runs", type=Path, required=True, help="JSON file with run dicts")
    val.add_argument("--threshold", type=int, required=True, help="minimum clean runs required")

    args = parser.parse_args(argv)

    if args.command == "conformance":
        lock_path = args.lock or (args.root / "catalog.lock.yaml")
        if not lock_path.is_file():
            raise SystemExit(f"no lock at {lock_path}")
        lock = yaml.safe_load(lock_path.read_text(encoding="utf-8")) or {}

        with tempfile.TemporaryDirectory(prefix="conformance-") as scratch:
            report = run_conformance(_load_backend(args.backend), args.harness,
                                     lock, Path(scratch))
        print(report.echo())

        if args.record:
            record(args.root, args.harness, report.conformant, report.echo())
            print(f"recorded in {args.root / LIST_FILENAME}")
        return 0 if report.conformant else 1

    elif args.command == "validate":
        try:
            runs = json.loads(args.runs.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SystemExit(f"cannot read --runs file {args.runs}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--runs file {args.runs} is not valid JSON: {exc}") from exc
        try:
            digest = promote_to_validated(
                args.root,
                entry_id=args.entry,
                version=args.version,
                model_profile=args.profile,
                runs=runs,
                threshold=args.threshold,
                run_lockbuild=_run_lockbuild,
                commit=_git_commit,
            )
            print(f"promoted {args.entry} to validated: {digest}")
            return 0
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        except KeyError as exc:
            raise SystemExit(f"malformed --runs data, missing field {exc}") from exc
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    sys.exit(main())
