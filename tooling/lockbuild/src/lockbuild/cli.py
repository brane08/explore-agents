from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lockbuild.build import LOCK_FILENAME, build_lock, render_lock, verify_lock
from lockbuild.errors import LockbuildError
from lockbuild.init import bootstrap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lockbuild")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="scaffold an empty catalog root (dirs + tags.yaml + trust.yaml)")
    p_init.add_argument("--root", type=Path, default=Path("."))

    p_build = sub.add_parser("build", help="rebuild catalog.lock.yaml from the tree")
    p_build.add_argument("--root", type=Path, default=Path("."))
    p_build.add_argument("--out", type=Path, default=None)

    p_verify = sub.add_parser("verify", help="fail if the committed lock differs from a rebuild")
    p_verify.add_argument("--root", type=Path, default=Path("."))
    p_verify.add_argument("--lock", type=Path, default=None)

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            created = bootstrap(args.root)
            if created:
                for p in created:
                    print(f"created {p}")
            else:
                print(f"{args.root} already scaffolded")
            return 0
        if args.command == "build":
            out = args.out or args.root / LOCK_FILENAME
            out.write_text(render_lock(build_lock(args.root)), encoding="utf-8")
            print(f"wrote {out}")
            return 0
        if args.command == "verify":
            if verify_lock(args.root, args.lock):
                print("lock matches tree")
                return 0
            print("catalog.lock.yaml does not match the tree — run `lockbuild build`",
                  file=sys.stderr)
            return 2
    except LockbuildError as exc:
        print(f"lockbuild failed:\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
