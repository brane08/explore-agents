"""certify CLI.

`certify conformance` runs the layer-12 fixture suite against a harness and,
with `--record`, writes the result to the catalog's conformance list. The list
is generated here and nowhere else: hand-adding a harness would assert
conformance nobody measured, which is exactly what CATALOG §10 forbids.
"""
from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
from pathlib import Path

import yaml

from certify.conformance import run_conformance
from certify.conformance_list import LIST_FILENAME, record


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

    args = parser.parse_args(argv)
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


if __name__ == "__main__":
    sys.exit(main())
