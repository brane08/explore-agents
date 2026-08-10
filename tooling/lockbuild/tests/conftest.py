"""Fixture registration for this package's tests.

The shared helpers live in `catalog_fixtures` rather than here on purpose. Test
modules import them explicitly, and every test directory in this repo is put on
`sys.path` by pytest — so three modules all named `conftest` would resolve to
whichever one landed in `sys.modules` first, and a cross-package
`from conftest import ...` would silently read another package's fixtures
(it did: running the whole `tooling/` tree used to fail collection). Unique
module names remove the ambiguity; this file only re-exports so pytest still
registers the fixtures for this directory.
"""
from catalog_fixtures import *  # noqa: F401,F403
