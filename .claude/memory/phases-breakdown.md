# agent-platform — phase breakdown as units of work

Companion to `phase-progress.md` (narrative). This file decomposes each ROADMAP phase
into **units of work** — the discrete things to do. Every unit is one of:

- **`code`** — implement a module / function / template / wiring.
- **`test`** — write an automated test (a [M] checklist item becomes a pytest; [H] items
  are review notes, not automated — flagged `[H]` and carried to the PR, no test unit).
- **`verify`** — run something and observe the result (suite passes, lock matches,
  exit-criteria demonstrated).

Ordering within a layer is TDD: **`test` before `code`**, `verify` last
(CLAUDE.md §3 — implement each [M] as a test first, then implement until green).

## Baseline (last verified 2026-07-18)

- Specs pinned at `3380709`. Re-confirm if `git log -1 -- docs/` is newer.
- Green: **38 lockbuild + 76 orchestrator + 69 certify = 183**. Lock matches tree.

```
uv run pytest tooling/lockbuild/tests -q     # 38
uv run pytest tooling/orchestrator/tests -q  # 76
uv run pytest tooling/certify/tests -q       # 69
uv run lockbuild verify --root .             # "lock matches tree"
```

(Suites run separately — combined runs collide on `conftest` module names across rootdirs.)

## Status legend

`done` · `partial` (some units in, some out — see notes) · `todo` (not started) ·
`blocked` (needs a decision/dependency, noted). `[H]` = human review note, no test unit.

---

## Phase 0 — deterministic foundation  **[done]**

Exit *(met)*: two skills + one mirrored MCP entry produce a verified lock; drift
simulation flips `stale` without failing the build.

| # | Type | Unit of work | Status |
|---|---|---|---|
| 0.1 | test | Layer 1 entry-authoring schema tests | done · `lockbuild/tests/test_layer1_authoring.py` |
| 0.2 | code | Entry schema + per-entry validation | done · `lockbuild/src/…` |
| 0.3 | test | Layer 2 lockbuild determinism + stale-flag tests | done · `lockbuild/tests/test_layer2_lockbuild.py` |
| 0.4 | code | Lockbuild stages 1–5, lazy stale-flags, LF-normalized output | done |
| 0.5 | test | Trust tier resolution (latest-per-key, recall≠no-record) | done · `lockbuild/tests/test_trust_semantics.py` *(early — layer 8, owned by P3)* |
| 0.6 | code | `trust.py` tier resolution (single impl, imported everywhere) | done |
| 0.7 | verify | Build-twice byte-diff + `lockbuild verify` + drift sim | done · CI gates green |

---

## Phase 1 — routing + B1  **[done]**

Exit *(met)*: a task routes to a B1 assembly, streams, registers, and re-routes to the
registration on repeat.

| # | Type | Unit of work | Status |
|---|---|---|---|
| 1.1 | test | Layer 3 routing cascade + post-match confirmation + §11 errors | done · `orchestrator/tests/test_layer3_routing.py` |
| 1.2 | code | Router over pinned-epoch lock, residual hygiene | done · `router.py` |
| 1.3 | test | Layer 4 B1 assembly + registration (config-hash identity) | done · `orchestrator/tests/test_layer4_b1.py` |
| 1.4 | code | `assembly.py` — assemble, register, re-route on repeat | done |
| 1.5 | test | Layer 11 session/auth + epoch pin | done · `orchestrator/tests/test_layer11_session.py` |
| 1.6 | code | Session/auth mode selection, epoch pin | done |
| 1.7 | test | Layer 10 (partial) — task console + SSE stream | done · `orchestrator/tests/test_layer10_ui.py` |
| 1.8 | code | FastAPI task console, SSE, htmx shim (`static/app.js`) | done |
| 1.9 | verify | End-to-end: route → stream → register → re-route | done |

### P1-residual — deferred outcomes (phase `done`, these parked)

Both ROADMAP P1 "pending design (small)". **Model decision made (2026-07-27): OpenRouter
or a locally served OpenAI-compatible model** — `ORCH_SCORER=openai` + `OPENAI_BASE_URL`.
The `Scorer` seam carries both paths (keyless local, gateway-namespaced ids). No longer
blocked on the decision; R.3 still needs labeled data.

| # | Type | Unit of work | Status |
|---|---|---|---|
| R.1 | test | Criteria-author produces behavioral criteria at routing (deterministic stub) | todo (unblocked) |
| R.2 | code | Wire criteria-author as an independent call on the `Scorer` seam | todo (unblocked) |
| R.3 | verify | Calibrate `confirm_threshold` (`config.py:20`, currently arbitrary 0.5) against real scorer + labeled routing data | todo — needs a labeled routing set (author it) |

---

## Phase 2 — B2b + human-gated certify  **[partial]**

Exit *(unmet)*: one capability via Role A → human-certified through the queue → promoted
atomically → routable.

### Done

| # | Type | Unit of work | Status |
|---|---|---|---|
| 2.1 | test | Layer 12 conformance fixtures (1 conformant + 11 cheating doubles) | done · `certify/tests/test_layer12_conformance.py` |
| 2.2 | code | `conformance.py` harness driver + fixtures | done |
| 2.3 | test | Conformance-list read+write (written only by suite runner) | done · `certify/tests/test_conformance_list.py` |
| 2.4 | code | `conformance_list.py` (read+write in one module) | done |
| 2.5 | code | Layer 6 B2 contract — 6 checks (`contract.py`) | done · **no standalone test**; exercised via 2.1 (`check_contract` in `conformance.py:123/204/251`) |
| 2.6 | test | B2b dispatch — §0 assembly, refusal paths, never-promotes | done · `orchestrator/tests/test_dispatch.py` |
| 2.7 | code | `dispatch.py` — Role A invocation behind `Harness` seam | done |

### Layer 7 — certify steps (each [M] item = a unit pair; [H] = review note)

All in `certify/steps.py` + `certify/tests/test_certify_steps.py` (33 tests). Numbering
below follows CHECKLISTS item order; CATALOG §8 step numbers noted.

| # | Type | Unit of work | Status |
|---|---|---|---|
| 2.8  | test | Step 1 (§8.1) — layout §6, `playbook_version` supported, harness listed, `entry.draft.yaml` present | done |
| 2.9  | code | `step_layout_manifest` (reuses layer-6 `check_layout`/`check_criteria_refs`) | done |
| 2.10 | test | Criteria-ref checks (frozen-input hashes; interface iff full-agent) | done (inside step 1) |
| 2.11 | code | — folded into `step_layout_manifest` | done |
| 2.12 | test | Step 2 (§8.2) — rebase: bound hashes current at promotion tree, else NEEDS_REBASE | done |
| 2.13 | code | `step_rebase` | done |
| 2.14 | test | Step 3 (§8.3) — `eval/` green on manifest `model_profile`, evidence captured | todo (unblocked — rides `Completer`/openai seam) |
| 2.15 | code | Eval runner behind seam | todo (unblocked) |
| 2.16 | test | Step 4 (§8.4) — evalmatrix applicability by mechanical signature match only; prose ignored; inapplicable rows carry `failed_check` | done (selection; *running* rows = step-3 seam) |
| 2.17 | code | `select_evalmatrix_rows` — row signature shape spec-unpinned, invented minimally ([H] note) | done |
| 2.18 | test | Steps 5+6 mechanical halves — structural (`slot_value_surface`, no agent invocation) + security static (binding audit vs ceiling, UI/model-id/secret, manifest-bypassing I/O) | done |
| 2.19 | code | `step_structural` + `step_security_static` (I/O scan scope spec-unpinned, [H] note) | done |
| 2.20 | test | §8.6 model-diversity — families differ, config hash recorded, local/stub profile passes under feasibility clause | done |
| 2.21 | code | `step_model_diversity` (mechanical family comparison) | done |
| 2.22 | [H] | Skeleton/slot-value separation genuine (entanglement review) | review note → PR |
| 2.23 | [H] | Security review findings recorded; human vs judge findings diffed | review note → PR (judge needs model) |
| 2.24 | test | Step 7 (§8.7) — atomic promotion; rollback-on-failure verified (no half-state) | done |
| 2.25 | code | `promote()` — publish-only-on-success; `summary_writer`/`run_lockbuild`/`commit` seams; traces content-addressed (location invented, [H] note); trust append at `quarantined` | done |

### Layer 10 — certify-queue meta screen

| # | Type | Unit of work | Status |
|---|---|---|---|
| 2.26 | test | Meta screen gated by operator role | done · P1 pattern + `test_certify_queue.py` (queue-specific) |
| 2.27 | test | Every §11 code (incl. WARNs) has a fragment renderer (coverage test) | done · P1 (`test_layer10_ui.py`) |
| 2.28 | test | SSE events persist 1:1; reconnect replays via `Last-Event-ID` | done · P1 (`test_layer10_ui.py`) |
| 2.29 | code | Certify-queue screen (UI-PLANE §3.2): list + detail w/ live mechanical-step evidence, binding audit, human-findings capture; promote = real atomic commit (sidecar retired in same commit); reject clears candidate | done · `webapp.py` + 2 templates, 6 tests incl. real end-to-end promotion |

### Enablers landed en route (not numbered units)

- **Dispatch persists frozen §0 inputs** as `_proposed/<id>.inputs.yaml` sidecar —
  certify re-verifies criteria refs against the exact frozen text (`88c21cd`).
- **`schema_hash` drift fix**: lockbuild `ManifestBinding` + B1 assembly used `hash`;
  spec (CATALOG stage 4) + playbook + certify + dispatch say `schema_hash`. Caught by
  the first real end-to-end promotion (`a596dba`).

### Exit

| # | Type | Unit of work | Status |
|---|---|---|---|
| 2.30 | verify | One capability: Role A → queue → atomic promotion → routable | todo — needs creds + a conformance run (see below) |

**Model decision (2026-07-27): OpenRouter or a locally served OpenAI-compatible model.**
`ORCH_SCORER=openai` + `OPENAI_BASE_URL`; keyless local endpoints supported; diversity
gate reads family from the last path segment so gateway namespacing can't defeat it.
This unblocks every *scoring/judging/eval* unit — they are single chat-completion calls.

**Harness decision (2026-07-27): option (a) — `ORCH_HARNESS=openai-agent`.** One
structured completion returns the §6 file map; the harness writes it. Viable because
the playbook fixes the layout, so the model supplies content and never structure —
which lets every path it emits be treated as untrusted input and validated into the
scratch dir before any byte is written (all-or-nothing; a traversal attempt writes
nothing at all). Status is read back from the manifest, never from the model's claim.
Shared endpoint/credential rules in `orchestrator/openai_compat.py`.

**2.30 remaining prerequisites:**
1. Creds in `.env` (`ORCH_SCORER=openai`, `ORCH_HARNESS=openai-agent`, base_url/key). **Done
   2026-08-03** — OpenRouter key live, verified with a direct `openai_compat.build_client()`
   call (200 OK).
2. **Conformance run**: dispatch refuses any harness missing from `conformance.yaml`
   (CATALOG §10). Drive the layer-12 suite (1 conformant + 11 cheating doubles) against
   `openai-agent/1` and record the result — a real model may well fail doubles the stub
   passes, which is the suite working, not a defect. **Attempted 2026-08-03, not recorded —
   still `todo`.** Ran `certify conformance --harness openai-agent/1 --backend
   orchestrator.dispatch:openai_agent_harness` for real (not `--record`) across ~7 rounds,
   tuning `_AGENT_SYSTEM` in `dispatch.py` (`openai_agent_harness`) each round against actual
   failures, plus deterministic post-processing of `catalog_ref`/`behavioral_criteria_ref`/
   `interface_criteria_ref` in the manifest (hashed/passed through in code, never trusted from
   the model — same "artifacts not claims" principle as path validation). Result: neither
   `qwen/qwen3-coder` nor `deepseek/deepseek-v4-pro` (both via OpenRouter) clears all 7
   fixtures in the same run — pass rate is ~5-6/7 per run but which 2 fail is inconsistent
   run-to-run (temperature=0, but OpenRouter provider routing + model sampling still vary).
   No fixture fails on both models. **Not recorded as conformant** — would misrepresent
   "measured, not assumed" (CATALOG §10). Options for next attempt: try a stronger
   reasoning model; or a two-call harness (generate, then self-check against the §7
   checklist before finalizing) instead of one-shot — bigger change, not attempted.
   `dispatch.py`'s prompt fixes are still worth keeping regardless (moved qwen from 1/7 to a
   consistent 5-6/7).

   **Model comparison (2026-08-03/04, same tuned `_AGENT_SYSTEM` prompt, unrecorded runs
   unless noted):**

   | model | run 1 | run 2 | notes |
   |---|---|---|---|
   | `qwen/qwen3-coder` | ~5/7 | ~6/7 | which 2 fail varies run-to-run |
   | `deepseek/deepseek-v4-pro` | ~5/7 | ~6/7 | same variance pattern |
   | `minimax/minimax-m3` | 4/7 | — | worst of the set; `layout`+`criteria_bait`+`delta` failed |
   | `openai/gpt-5.6-luna-pro` | 6/7 (`layout` only) | 4/7 (`layout`+`budget_and_trace`+`delta`) | best single run so far |
   | `openai/qwen-plus` | 6/7 (`delta` only) | 5/7 (`criteria_bait`+`delta`) | `delta` fails most often across all models |

   `conformance.yaml` cannot hold this table — it's keyed by harness id only (no `model`
   field, `record()` upserts/overwrites per harness — see `certify/conformance_list.py:34`),
   so a `--record` only ever captures the latest attempt's harness-level pass/fail, not a
   per-model history. This table is the actual comparison; `conformance.yaml` is a live
   dispatch gate, not a leaderboard. No model has cleared 7/7 in any single run yet.
3. Then the end-to-end run: dispatch → queue → human promotion → routable.

---

## Phase 3 — templates + B1.5 + quarantine  **[todo]**

Exit *(unmet)*: an instance created via form runs quarantined in shadow, is promoted on
threshold, recalled instantly on demotion.

| # | Type | Unit of work | Status |
|---|---|---|---|
| 3.1 | test | Layer 5 — slot-form POST and B1.5 instantiation share the identical validation code path | todo |
| 3.2 | code | B1.5 instantiation + slot validation (single function) | todo |
| 3.3 | code | First template (slots, `evalmatrix/`, optional `ui/`) | todo |
| 3.4 | test | Layer 8 — quarantine/promote/recall transitions | partial — tier resolution + recall≠no-record done (0.5); transitions/screens todo |
| 3.5 | code | Trust lifecycle transitions + routing-time recall | todo |
| 3.6 | test | Layer 10 — canary-review screen (shadow + canary) | todo |
| 3.7 | code | Supervised invocation (shadow + canary) + review screen | todo |
| 3.8 | verify | Instance via form → shadow quarantine → promote on threshold → recall on demotion | todo |

**Blocked on design (own session):** shadow-divergence metric; router slot-extraction
prompting + eval set.

**Pending design: generic htmx clarify UI (proposed 2026-07-31).** Idea: extend the
task console so ambiguous routing outcomes ask the user to pick among skill/playbook
options instead of silently falling through the cascade, reusing the existing SSE
trace/fragment-renderer pattern (layer 10). Three seams identified in `router.py`:

- **Ambiguous agent-match** (`router.py:93-107`): today `confirmation >= confirm_threshold`
  is a hard binary — below it just `break`s and the cascade continues with no signal to
  the user. A band between `match_threshold` and `confirm_threshold` could return a new
  `RoutingResult` outcome (`"clarify"`) carrying the already-scored top-N `scored`
  candidates (line 77-81) with their `routing_summary`, rendered as pick-one options.
- **B1 tool coverage** (`router.py:112-129`): `tools` is already a scored, sorted list;
  same clarify pattern could let the user swap a tool before auto-assembly.
- **Residual gap** (`router.py:131-136`): `PATTERN_UNRECOGNIZED` is today a hard stop
  (no fallback, by documented policy). This is the actual "generate an agent" seam —
  ask the user to confirm/refine the residual, then feed it into the existing `dispatch()`
  B2b path as a live UI action instead of an offline call.

Wiring shape: new `outcome` values threaded through `webapp.py` `/tasks`, rendered as
htmx fragments matching the existing `errors/{code}.html.j2` pattern, POSTing the user's
choice to a resume-routing endpoint.

**Why this is Phase 3+, not a slot-in fix:** it's a contract change to `RoutingResult`/
the cascade's documented hard-stop policy (schema/architecture-level — needs its own
plan). The clarify-and-pick pattern for #1/#2 is structurally the same problem as B1.5
slot-forms (3.1-3.2 above) — building it now would likely duplicate that work rather
than precede it. Branch #3 (generate-new-agent) is additionally blocked on 2.30's
prerequisites (Role A creds + conformance run) regardless of UI readiness. Recommend
revisiting once 3.1-3.2 (slot-form validation path) land, so the clarify UI can reuse
the same form-rendering code path instead of inventing a second one.

---

## Phase 4 — compounding  **[todo]**

Exit *(unmet)*: a B2 lineage graduates; an instance migrates profiles via eval-rerun
alone; trust records show `(id, version, model_profile)` keys.

| # | Type | Unit of work | Status |
|---|---|---|---|
| 4.1 | test | Layer 9 — template graduation pipeline (signal → extraction → migration) | todo |
| 4.2 | code | Graduation pipeline (migration commits) | todo |
| 4.3 | test | Layer 13 — profile swap via eval-rerun alone; `(id, version, model_profile)` keys | todo |
| 4.4 | code | Second model profile + eval-rerun migration; resolver with logged policy | todo |
| 4.5 | verify | Lineage graduates; instance migrates profile via eval-rerun | todo |

**Blocked on design:** resolver-policy eval; residual-corpus hygiene spec.

---

## Loose ends (not phase work)

| # | Type | Unit of work | Status |
|---|---|---|---|
| L.1 | code | Delete stray `orchestrator/src/orchestrator/conformance_list.py` (dead; real one in `certify/`) | todo (delete was denied at a prior prompt) |
