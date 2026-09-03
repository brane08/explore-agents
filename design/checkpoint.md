# Checkpoint — 2026-09-02

Companion to `design/repo-brief.md`. Snapshot of what's built, what's designed
but unbuilt, and what's still open, as of `main` (`a30b3de`).

## Done and merged to `main`

- **Phase 0 — deterministic foundation.** `lockbuild` stages, `tags.yaml`, CI gates.
- **Phase 1 — routing + B1.** Router cascade, B1 runtime tool assembly, the FastAPI
  console.
- **Phase 2 — B2b + human-gated certify.** One real capability,
  `cron-next-fire-times`, went generation → `_proposed/` → certify → PR → merge →
  `quarantined` trust record, closing the phase's exit criteria (PR #3).
- Two review notes opened during Phase 2 closed after merge, both on `main` (PR #4 plus a
  direct fix): a `lockbuild` hashing determinism defect (`__pycache__` entering the entry
  content hash) and a truncated `routing_summary` in `cron-next-fire-times/entry.yaml`.
- **Phase 3, layer 8 only — supervised invocation** (PR #8). Shadow and canary modes
  writing one `supervised_run` evidence ledger; clean-streak promotion rule; the
  operator canary review screen; routing-time recall on demote; certify-side
  `promote_to_validated` with a content-addressed evidence digest. Also merged in the
  same PR (out of phase scope — see Open below): an agent console TUI for
  `cron-next-fire-times`, the `AGENTS.md`/`CLAUDE.md` compaction, and `.gitignore`.
- The `allowed_tiers` gap named in the previous checkpoint is closed: operators no
  longer get an unsupervised run of a `quarantined` agent — layer 3 tier authz is
  joined before quarantined dispatch.

## Designed, not yet built

- `design/2026-08-13-shadow-divergence-design.md` and its plan are now **executed for
  layer 8**. The plan's remaining reach (template graduation as a promotion path) is
  not built.
- **Phase 3 is not closed.** Its layers 5 (B1.5 instantiation: templates, slot forms,
  slot validators, `evalmatrix/`) and 9 (template graduation) are entirely unbuilt.
  `router.py` raises `NotImplementedError` on any `template` entry in the lock — a
  deliberate fail-loud guard, but it means the phase's exit criterion ("instance
  created via form, runs quarantined in shadow mode, promoted on threshold") is
  provably unmet.

## Logged, deferred, not designed

- `design/2026-08-13-mcp-discovery-indexing-future-phase.md` — an external "MCP
  Discovery & Indexing" work package, filed as a future-phase note rather than adopted.
  `CATALOG.md` §6 names this evolution as a deferred scaling trigger past ~500 catalog
  entries; the catalog holds 5. Two of the brief's decisions reverse standing
  `docs/CATALOG.md` policy and would need an explicit human spec edit first. Also
  logged there: a router token/candidate cap is needed regardless of retrieval
  approach — today's router scores every `skill`/`mcp-tool` entry with one uncapped
  model call per candidate.

## Open / pending

Findings from a three-way review of PR #8, run after it merged. All are defects in
`main`.

- **Canary requires a counterpart — likely a spec inversion, needs a human ruling.**
  `supervise.select_mode` refuses canary when no counterpart exists. Agents generated
  from an unrecognized residual have no counterpart by construction
  (`cron-next-fire-times` is the design's named example), so they have no evidence path
  at all and `quarantined → validated` is unreachable for them — the outcome CATALOG §7
  calls "quarantine is theater". The code reasons from §7's "instant fallback" phrase;
  the review reads §7's overall requirement plus design §2.3 as overriding it.
  **Not yet acted on.**
- **`certify validate` promotes on unverified evidence.** `--runs` reads an arbitrary
  JSON file; nothing binds those rows to the orchestrator's `supervised_run` table. A
  hand-written file of N clean rows promotes an agent. Relatedly, **nothing produces
  that JSON** — the promotion path has no end-to-end route, so the canary screen renders
  "eligible" with no way to act on it.
- **`created_at` / `adjudicated_at` are written but never read** — absent from
  `_RUN_COLS` and from the `SupervisedRun` dataclass, yet `certify/validate.py` prefers
  `created_at` ordering and hashes both fields. Every real digest hashes `null` for two
  of twelve fields, and the preferred ordering branch is unreachable from real data.
- **Hash-scope change without a version bump.** Adding `console/` and its test changed
  `cron-next-fire-times`'s content hash while `version` stayed `0.1.0`. Trust records
  and supervised-run rows key on `(id, version, model_profile)`, so pre- and
  post-change evidence share a key — what the "never inherited evidence" rule exists to
  prevent.
- **`textual` was added as a dependency without the ask** `CLAUDE.md` requires; its
  tests live in `agents/*/tests`, which CI does not run.
- **`stale_queue` has no reader** in production, yet `router.py` added a new write to it.
- Phase 4 (compounding: template graduation, model-profile migration) — unstarted,
  downstream of Phase 3.
- `docs/ROADMAP.md` §6 (design-debt register) carries deferrals beyond those above —
  not re-audited here.

## Fixed after PR #8 (branch `fix/supervision-operator-correctness`)

- The canary screen told the operator `trust.yaml is unchanged by this screen` while
  `canary_demote` committed an irreversible recall. Copy now states the actual effect.
- The void escape hatch (`incident -> void`, for infra-attributable failures that
  auto-close before anyone sees them) was permitted by the store but rendered only for
  `pending` rows, so it was unreachable from the UI.
- A shadow trace was readable by the caller whose task produced it: the shadow run
  executes on an invocation that caller owns, and authorization granted session owners.
  Shadow traces are now operator-only, since they carry the quarantined agent's real
  output that CATALOG §7 says is never returned to the caller.
