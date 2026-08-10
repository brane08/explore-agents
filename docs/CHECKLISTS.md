# CHECKLISTS.md — Layered Verification Checklists (v1-beta)

> **Status: BETA (draft).** These specifications are under active iteration during the
> POC phases and are expected to change. Nothing here is a frozen contract yet: breaking
> revisions are permitted between phases, spec changes remain human-only (separate PRs),
> and consumers must pin the document version/hash they were built against.

Generic, reusable checklists per architecture layer. Companion to the B2 playbook (v3),
`CATALOG.md` (v3), and `UI-PLANE.md` (v1); terminology follows those documents.

Every item is tagged:
- **[M]** mechanical — enforceable by code (lockbuild, CI, certify tooling, runtime check).
  Target: 100% of [M] items automated; a failing [M] is a blocking gate, never a review note.
- **[H]** judgment — human or LLM-judge; must produce a recorded finding either way.

Usage: each layer's checklist is echoed in that layer's artifact (PR description, certify
record, promotion commit, conformance report) with per-item pass/fail — the same
echo-verbatim discipline as playbook §7.

---

## 1. Catalog entry authoring (per entry PR)

- [M] `entry.yaml` validates against the kind's JSON Schema; no unknown fields.
- [M] `id` globally unique; `version` valid semver and bumped when hash scope changed.
- [M] `capability_tags` ⊆ `tags.yaml` (or flagged `proposed: true`).
- [M] Hash-scope files present and complete for the kind (CATALOG §4 table).
- [M] No trust tier declared in the entry (trust is orchestrator-owned).
- [M] `bindings` reference only `skill|mcp-tool` kinds.
- [M] External kinds (`mcp-tool`, `a2a-agent`): snapshot file present; `routing_summary`
  marked as regenerated (provenance field), not hand/vendor-written.
- [H] `routing_summary` accurately and narrowly describes the capability (no
  self-advertising breadth — the misroute surface).
- [H] For skills: `impl/` validates its inputs (prompt-injection posture on any
  output-consuming script).

## 2. Lockbuild (every build)

- [M] Same tree ⇒ byte-identical lock (determinism check in CI: build twice, diff).
- [M] `agents/_proposed/` excluded; no `_proposed` entry appears in the lock.
- [M] Missing referenced entry ⇒ build FAILS (internal inconsistency).
- [M] Hash drift ⇒ `stale: true` + `stale_reason` (never a build failure).
- [M] Trust join keyed `(id, version, model_profile)`; absent record ⇒ `untrusted`.
- [M] Composite tier = min(members) unless explicit record; cycle detection passes.
- [M] Instance `instantiated_from` hash checked against current template hash.
- [M] `--refresh` outputs land on a PR branch, never `main`; refreshed summaries
  regenerated from schema/card.
- [M] Committed lock matches rebuilt lock (pre-merge diff gate).

## 3. Routing (per invocation)

- [M] Router input is the pinned-epoch lock (`session.catalog_ref`), nothing else.
- [M] Stale entries refused (`STALE_ENTRY` emitted, queued for rebase/eval-rerun).
- [M] Tier check transitive (instance→template, agent→bindings) at invocation time —
  recall pierces the epoch pin.
- [M] User authorization joined against allowed tiers/agents before invoke.
- [M] Cascade order respected: agent → template → B1 coverage → B2/ERROR; no automatic
  escalation on error (orchestrator decision required, error attached).
- [H] Post-match confirmation score threshold reviewed periodically against misroute
  incidents (threshold is policy, not truth).
- [M] Every no-match/partial-match emits a structured error from CATALOG §11; residuals
  pass the hygiene normalization before persistence.

## 4. B1 assembly & registration

- [M] All assembled tools `validated` at the resolved `model_profile`.
- [M] Generated system prompt stored as a versioned file; assembly identity =
  sha256(config) and deduplicates against existing registrations.
- [M] Registration entry tier = min(binding tiers); skips `_proposed/` (config-only rule:
  the assembly contains zero novel code).
- [H] Generated prompt contains no capability claims beyond the bound tools.

## 5. B1.5 instantiation (per instance)

- [M] Every required slot filled; every fill within its slot's accept-set
  (enum membership / deterministic validator / binding tag+tier filter).
- [M] Zero residual requirements: every extracted task requirement maps to a slot, else
  `PATTERN_UNRECOGNIZED` — no silent dropping.
- [M] No `value` slot carries Turing-complete content (validator is a plain callable —
  expressiveness ceiling).
- [M] Instance manifest records template `(id, version, hash)` + binding hashes verbatim.
- [M] Eval-rerun executed iff resolved profile ≠ template's certification profile.
- [M] UI = template `ui/` + slot values, or generic renderer; zero instance-local assets.

## 6. B2 candidate (harness-side)

Defined in playbook §7 (contract checklist, echoed verbatim, any false ⇒ not `COMPLETE`).
This layer's checklist **is** that list; do not fork it here — one source of truth.

## 7. Certification & promotion (per candidate)

- [M] Layout matches playbook §6 exactly; `playbook_version` supported; `harness` on the
  conformance list; `entry.draft.yaml` present.
- [M] `behavioral_criteria_ref` / `interface_criteria_ref` equal hashes of the frozen
  inputs (criteria untouched); interface criteria present iff full-agent scope.
- [M] Rebase check: all bound hashes current at the promotion tree, else rebuild +
  eval-rerun first.
- [M] `eval/` green on the manifest's `model_profile`, evidence captured.
- [M] Nearest-template evalmatrix rows applied by mechanical signature match only.
- [M] No UI files, no hardcoded model ids, no secrets, no direct agent invocation paths
  (static scan).
- [M] Judge + criteria-author models differ in family from implementation profile
  (certify config hashes recorded).
- [H] Skeleton/slot-value separation genuine (entanglement review).
- [H] Security review findings recorded; human findings vs. judge findings diffed
  (feeds gate-graduation counters).
- [M] Promotion commit atomic: move + generated `entry.yaml` + trust record
  (`quarantined`) + trace to content-addressed storage + lockbuild, one commit.

## 8. Trust lifecycle

- [M] `trust.yaml` written only by certify or recorded human override; append-only history.
- [M] Tier records keyed `(id, version, model_profile)`; class migration = eval-rerun,
  never inherited evidence.
- [M] Quarantined agents run only in supervised mode (shadow diff or canary with
  fallback); outputs of shadow runs never returned to callers.
- [M] `quarantined → validated` requires N supervised runs under the divergence/incident
  threshold, or template graduation — no other path.
- [M] Demotion effective at routing time for all dependents (recall test: demote in
  staging, verify next invocation refuses).
- [H] Divergence threshold and N reviewed against incident history per task class.

## 9. Template graduation (per extraction)

- [M] ≥N structurally similar promoted candidates identified (signal recorded).
- [M] Skeleton factored into `impl/` + `prompts/` (+ optional `ui/`); all variance
  expressed as slots respecting the expressiveness ceiling.
- [M] `evalmatrix/` covers every enum variant × representative slot combos, on the
  reference profile per supported class; matrix inside hash scope.
- [M] Migrated lineage agents re-expressed as instances in a migration commit.
- [H] Template certification: security review of `impl/`, UI fragment lint (no external
  scripts, fixed context), matrix representativeness judgment.

## 10. UI plane

- [M] No agent/component ships UI files (certify static scan — playbook §8).
- [M] Template fragments: Jinja2 only, fixed context variables, no external script tags
  (lint rule).
- [M] Slot form POST and B1.5 instantiation execute the identical validation code path
  (single function, asserted by test).
- [M] Every CATALOG §11 code (including WARNs) has a fragment renderer (coverage test).
- [M] SSE events persist 1:1 as trace; reconnect replays via `Last-Event-ID` from
  persistence (drop-connection test: run continues, stream resumes complete).
- [M] Meta screens gated by operator role.

## 11. Session & auth

- [M] `catalog_ref` frozen at session creation; only recall pierces the pin (test both).
- [M] Cookie = signed session id only; all state server-side; history via checkpointer
  `thread_id` (no duplicated conversation state).
- [M] Exactly one auth mode active per deployment (BFF-trusted identity XOR direct OIDC).
- [M] SSE affinity or fanout configured; long-run timeout budget ≥ max agent runtime class.

## 12. Harness conformance (per harness/version)

- [M] Produces playbook §6 layout byte-exactly on a fixture task.
- [M] Emits honest `PARTIAL` on the unsatisfiable-criteria fixture.
- [M] Refuses the criteria-modification bait fixture (`WARN: CRITERIA_ISSUE`, criteria
  untouched by hash).
- [M] Respects turn/failure budgets; produces trace; contract checklist echoed with
  accurate values (spot-verified against artifacts).
- [M] Delta fixture: does not rebuild template-provided capability; emits
  `DELTA_INSUFFICIENT` on the non-component-shaped fixture.

## 13. Model resolution & migration

- [M] No concrete model id anywhere in agent/template code or config (static scan);
  requirements are capability predicates.
- [M] Resolver selects only `status: active` profiles satisfying all predicates; choice
  logged per invocation (profile id + policy reason).
- [M] Profile deprecation ⇒ stale-flag path for dependents; eval-rerun before first
  invocation on a new profile.
- [H] Resolver policy (cost/quality routing) reviewed against outcome metrics — the
  deferred resolver eval (CATALOG §12).

---

**Aggregation rule:** a change is releasable when every [M] item in every touched layer
is green in CI/tooling, and every [H] item has a recorded finding. [H] items are the
gate-graduation surface: any [H] category can become [M]-delegated to the certify judge
only via the CATALOG §8 honesty-clause criteria.
