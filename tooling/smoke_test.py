#!/usr/bin/env python3
"""Standalone smoke test — no pytest, runs against the real catalog root.

Verifies the pieces a fresh deploy or a Day0/1/2 rollout needs to trust
before anything else: the committed lock still matches the tree, the
orchestrator app boots and its /health check is green. Not a substitute for
the pytest suites — this is what you run once against the target
environment (a container, a fresh checkout, a staging box) to confirm it's
alive.

    python tooling/smoke_test.py [--root PATH]

Exits 0 if every check passes, 1 otherwise. Prints one PASS/FAIL line per
check plus the failure detail so it's readable in CI logs or a terminal.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in ("lockbuild", "certify", "orchestrator"):
    sys.path.insert(0, str(REPO_ROOT / "tooling" / pkg / "src"))


def check_lockbuild_verify(root: Path) -> tuple[bool, str]:
    from lockbuild.build import verify_lock

    try:
        if verify_lock(root):
            return True, "catalog.lock.yaml matches the tree"
        return False, "catalog.lock.yaml does not match the tree — run `lockbuild build`"
    except Exception as exc:  # noqa: BLE001 — report, don't crash the runner
        return False, f"{exc}"


def check_orchestrator_health(root: Path) -> tuple[bool, str]:
    from fastapi.testclient import TestClient

    from orchestrator.config import Settings
    from orchestrator.webapp import create_app

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(catalog_root=root, db_path=Path(tmp) / "smoke.sqlite3")
        client = TestClient(create_app(settings))
        resp = client.get("/health")
        body = resp.json()
        if resp.status_code == 200 and body.get("status") == "ok":
            return True, f"checks={body['checks']}"
        return False, f"status={resp.status_code} body={body}"


def check_certify_importable() -> tuple[bool, str]:
    try:
        import certify.cli  # noqa: F401
        import certify.conformance  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc}"
    return True, "certify.cli, certify.conformance import cleanly"


CHECKS = [
    ("lockbuild verify", check_lockbuild_verify, True),
    ("orchestrator /health", check_orchestrator_health, True),
    ("certify importable", check_certify_importable, False),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()

    ok = True
    for name, fn, needs_root in CHECKS:
        try:
            passed, detail = fn(root) if needs_root else fn()
        except Exception as exc:  # noqa: BLE001 — a check crashing is still a FAIL
            passed, detail = False, f"raised {exc!r}"
        ok = ok and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name} — {detail}")

    print("smoke test: " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
