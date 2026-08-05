# agent-platform — build progress (Role B)

Branch `ft/platform-phase0`, **unpushed**. Specs pinned at `3380709` (verbatim import).
If `git log -1 -- docs/` shows anything newer, confirm with the user before building.

## Status

| Phase | State |
|---|---|
| 0 — deterministic foundation | done, exit criteria proven |
| 1 — routing + B1 | done, exit criteria proven, code-reviewed |
| 2 — B2b + human-gated certify | **in progress** (see below) |
| 3 — templates/B1.5/quarantine | not started |
| 4 — graduation/migration | not started |

Suites (run separately — a combined run collides on `conftest`/helper module names
across rootdirs; CI already runs them as separate steps):

```
uv run pytest tooling/lockbuild/tests -q     # 38
uv run pytest tooling/orchestrator/tests -q  # 44
uv run pytest tooling/certify/tests -q       # 33
uv run lockbuild verify --root .             # lock matches tree
```

## Commits

```
3380709 spec import (the pinned SHA)
7407206 phase0 lockbuild + seeded catalog (layers 1-2)
a721ac0 log-agent-lab consumes the platform catalog root
5e6bc8d trust seed + stub model profile
ceca765 phase1 orchestrator (layers 3, 4, 10-partial, 11)
37b6639 fix(review) — 8 findings, 7 regression tests
dcc6320 phase2 conformance fixtures + contract checks (layers 6, 12)
624c948 ci: certify suite
a637488 phase2 B2b dispatch + conformance list
```

## Phase 2 — done

- `tooling/certify/`: `playbook.py` (§0 inputs, §6 layout, §7 items as data),
  `candidate.py`, `contract.py` (6 checks = layer 6), `conformance.py` (7 fixtures,
  layer 12), `conformance_list.py` (read+write in one module), `cli.py`.
- `orchestrator/dispatch.py`: §0 input assembly, Role A invocation behind the
  `Harness` seam, refusal paths. Never promotes.

## Phase 2 — remaining

1. **Certify steps 1–7** (CHECKLISTS layer 7, CATALOG §8): layout/manifest, rebase
   check, eval on the manifest's `model_profile`, nearest-template evalmatrix reuse
   by mechanical signature match only, structural review, security review with the
   model-diversity rule, atomic promotion commit (move + generated `entry.yaml` +
   trust record at `quarantined` + trace to content-addressed storage + lockbuild,
   one commit).
2. **Certify-queue meta screen** (UI-PLANE §3.2, layer 10).
3. **Exit**: one real capability generated via Role A, human-certified through the
   queue, promoted atomically, routable.

**Blocker**: steps 3, 6, 7 each need a real model (eval, judge, regenerated
`routing_summary`) and there are no LLM credentials in this environment. The
established answer everywhere else in this codebase is a seam + deterministic stub
(`Completer`, `Scorer`, `ToolInvoker`, `Store`, `Harness`); `ORCH_HARNESS=claude-cli`
selects the real backend. Decide with the user before building steps 3/6/7.

Phase 2 is large; CLAUDE.md's one-phase-per-session rule has already been stretched.

## Review notes to carry into the PR ([H] items)

- **Playbook §0 vs §3.1 contradict** on `TRUST_TIER_CEILING`: §0 says "highest tier
  consumable (B2 candidates: `validated` only)", §3.1 says bind entries "at or below"
  it — read literally, ceiling=validated permits everything, inverting the control.
  Implemented as validated-only (the §0 parenthetical). Wants a spec fix.
- **§11 has no code for "harness not conformance-listed"**; the vocabulary is closed
  and `errors.py` raises on unknown codes, so dispatch returns a refusal outside §11.
- **Conformance-list location is invented**: `<catalog_root>/conformance.yaml`. The
  specs require the list (§10, layer 7 step 1) but never say where it lives.

## Load-bearing decisions (don't re-litigate)

- **Trust tiers = latest record per `(id, version, model_profile)`**, not `min()` over
  the ledger. `min()` made promotion `quarantined → validated` impossible against an
  append-only ledger — it would have blocked all of Phase 2. One implementation in
  `lockbuild/trust.py`; the orchestrator imports it (two copies had already drifted).
- **A recall must be distinguishable from no record.** A B1 assembly's derived tier is
  only computed when `has_record()` is false; otherwise the explicit record wins.
- **Artifacts are the evidence, never the harness's claims** (CATALOG §10). Fixtures
  and dispatch both check what was written, not what was reported. The criteria-bait
  double emits the correct `WARN` and still fails, because the hash proves the edit.
- **The conformance suite is tested in both directions**: 1 conformant double passes,
  11 cheating doubles each fail. A fixture suite that only sees good input proves
  nothing.
- **Dispatch publishes only on success** — the harness writes to a scratch dir that is
  moved into `_proposed/` at the end, so a crash leaves no half-candidate.
- **The stub reports `PARTIAL`**, never `COMPLETE` — a stub claiming COMPLETE is the
  exact false-COMPLETE the gate exists to catch.
- `static/app.js` is a hand-written htmx-compatible shim (npm vendoring was denied);
  it reads the same `hx-*`/`sse-*` attributes so real htmx drops in with no template
  change.

## Hard rules that bit during the build

- `docs/`, `trust.yaml`, `catalog.lock.yaml` are never hand-edited. The 3 seed trust
  records exist only under explicit in-session user authorization, marked
  `granted_by: human-override:...`.
- Never write into `agents/_proposed/` as the developer — that is Role A's at runtime.
  Dispatch writing there is correct; tests use `tmp_path`.
- Role A never sees CATALOG/ROADMAP/this repo — minimal context is an anti-drift
  control (ROADMAP §0).

## Loose ends

- `tooling/orchestrator/src/orchestrator/conformance_list.py` — stray untracked dead
  file (consolidated into certify; the delete was denied at the permission prompt).
  Remove it.
- `enabledPlugins` in `~/.claude/settings.json` names `superpowers@claude-plugins-official`,
  which that marketplace does not ship — a dangling entry, unrelated to this project.
- Deferred: Docker packaging for log-agent-lab; real-model held-out pass rate for its
  B2b (needs creds).
