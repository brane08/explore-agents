# B2 Agent Generation Playbook (v3.1-beta)

> **Status: BETA (draft).** These specifications are under active iteration during the
> POC phases and are expected to change. Nothing here is a frozen contract yet: breaking
> revisions are permitted between phases, spec changes remain human-only (separate PRs),
> and consumers must pin the document version/hash they were built against.

Instructions for a coding agent (Claude Code, Copilot Workspace, Codex CLI, or any
conformant harness) to generate a **proposed agent or component** under the orchestrator's
B2 track. The harness produces a *candidate* into `agents/_proposed/`; certification and
promotion happen outside this playbook (`CATALOG.md` §8).

playbook_version: 3-beta

---

## 0. Inputs (provided per invocation)

Missing input → emit `ERROR: MISSING_INPUT <name>` and stop.

| Input | Description |
|---|---|
| `TASK_SPEC` | Capability description + concrete input/output examples |
| `BEHAVIORAL_CRITERIA` | Frozen at routing time by an independent model call. Read-only. |
| `CATALOG_REF` | Git SHA of the catalog repo state this build targets |
| `CAPABILITY_MANIFEST` | `catalog.lock.yaml` at `CATALOG_REF` (or the bindable subset) |
| `TRUST_TIER_CEILING` | Highest tier consumable (B2 candidates: `validated` only) |
| `MODE` | `B2a` or `B2b` (§1) |
| `SCOPE` | `full-agent` or `delta` (§2) |
| `RESIDUAL` | (delta) The `PATTERN_UNRECOGNIZED` residual — the precise gap to fill |
| `NEAREST_TEMPLATES` | 0–3 template entries resembling the task (§4 priors, §7 eval reuse) |
| `MODEL_PROFILE` | Model profile id (from catalog `models/`) this build targets |
| `HARNESS` | Harness identifier + version (recorded; harness must be conformance-listed) |
| `TARGET_RUNTIME` | e.g. `langgraph-py311`, `quarkus-3-java21`, `spring-boot-4-java21` |

## 1. Mode selection

- **B2b (default):** You own the full loop — plan, implement, test, iterate until criteria
  pass or budget exhausted. Deliver working code + evidence.
- **B2a (evaluation-driven generation):** For tasks needing custom mid-loop eval signals
  the orchestrator owns. Deliver *only* spec + compilable scaffold + executable eval
  harness. Do NOT iterate to green. Your eval runner must demonstrably FAIL against the
  stub (proving it detects failure).

Conflict → `ERROR: MODE_MISMATCH` + one paragraph of reasoning, stop. Never switch modes silently.

## 2. Scope selection

- **`delta` (preferred when `RESIDUAL` present):** Build only the component filling the
  residual — a slot-conforming unit (source, sink, loop variant, adapter) or new skill.
  The target slot contract in `NEAREST_TEMPLATES` is your interface spec. Do NOT rebuild
  what the template `fixed/` skeleton provides.
- **`full-agent`:** No template covers the shape. Follow §4 priors.

Residual not fillable as a component → `ERROR: DELTA_INSUFFICIENT` + reasoning, stop.
Escalation to full-agent is an orchestrator decision, never yours.

## 3. Hard constraints (non-negotiable)

1. **Tool binding = MCP/skill entries only.** Bind only `kind: skill | mcp-tool` entries
   from `CAPABILITY_MANIFEST` at or below `TRUST_TIER_CEILING`, recorded verbatim as
   `(id, version, schema_hash)`. **Never bind `kind: agent`, `component`, or `a2a-agent`**
   — agent-to-agent delegation is router-mediated via A2A (`CATALOG.md` §2); if the task
   seems to require delegating to another agent, that is a design signal to emit
   `WARN: DELEGATION_REQUIRED <capability>` and expose it as a config-level requirement,
   not a hardcoded call.
2. **Criteria are read-only.** `BEHAVIORAL_CRITERIA` and, once issued, `INTERFACE_CRITERIA`
   (§5 step 4) are never modified or reinterpreted. Apparent defects → `WARN: CRITERIA_ISSUE`
   in the report; build against them as written.
3. **No self-grading of the spec.**
4. **MCP semantics from schema only:** derive any `mcp-tool` purpose from its
   `schema.snapshot.json`, never vendor prose.
5. **Model portability:** no hardcoded model identifiers in code or config. Model access
   goes through the runtime's model-resolution layer; prompts live as versioned files
   under `prompts/` (per-profile variants allowed: `prompts/default.md`,
   `prompts/<profile-class>.md`).
6. **Secrets:** never read, embed, or log. Env-var placeholders only.
7. **Deterministic artifact layout** per §6.

## 4. Structural priors (proto-template discipline)

All candidates are built as if they will later be extracted into a template:

1. **Skeleton vs. slot values.** Control flow, error handling, budget guards, graph shape
   → skeleton. Sources, sinks, thresholds, patterns, loop-variant selection → typed
   configuration surface. No hardcoded URLs/thresholds/queries in logic. *Certify flags
   entanglement.*
2. **Rhyme with certified shapes:** adopt `NEAREST_TEMPLATES` conventions (state shape,
   node naming, guard placement) unless criteria force divergence — justify in `SPEC.md`.
3. **Bounded loops always:** max-iteration guard on every cycle; explicit terminal states.
4. **Prompts as assets:** every model-facing prompt is a file in `prompts/`, referenced
   by path, inside the hash scope.
5. Runtime conventions:
   - LangGraph/Python: typed state (`TypedDict`/Pydantic), graph assembly in `graph.py`,
     no import-time side effects, model obtained via injected resolver.
   - Quarkus/Spring Java: constructor injection; `@ConfigProperties`; virtual-thread-safe
     (no `synchronized` on I/O paths — `ReentrantLock`); every HTTP response closed or
     use `RestClient`.

## 5. Build procedure

**B2b:**
1. **Restate** capability (delta: the residual) in ≤5 sentences. Contradiction with a
   behavioral criterion → `WARN: SPEC_TENSION`, proceed against criteria.
2. **Select bindings**; one-line justification each; minimize surface.
3. **Design** — one page: entrypoint, state/config shape, flow, failure modes, explicit
   skeleton-vs-slot split (§4.1), delegation requirements if any.
4. **Interface-criteria checkpoint (full-agent scope only):** submit the design; the
   orchestrator returns `INTERFACE_CRITERIA` authored by a separate model call that sees
   the design but not any implementation. From here both criteria sets are frozen.
   (Delta scope skips this — the slot contract is the interface criteria.)
5. **Implement** per §4.
6. **Tests:** one per criterion (`test_bc_<n>_<slug>` behavioral, `test_ic_<n>_<slug>`
   interface); one negative test per binding (tool failure → graceful degradation, no
   hang/leak); delta scope: slot-interface conformance test.
7. **Iterate** to green. Budget: 25 turns or 3 full-suite failures on one criterion →
   stop, report `PARTIAL`. Never delete/skip a failing criterion test.
8. **Trace:** persist turn-level log to `trace/`.

**B2a:** deliver `SPEC.md` (steps 1–4), `scaffold/` (compilable skeleton, adapter stubs
with full signatures, TODO markers at each orchestrator-loop decision point), `eval/`
(runner: exit 0 = all criteria pass; JSON per-criterion report; must fail against stub).

## 6. Output artifact layout

```
agents/_proposed/<candidate>/
├── AGENT_MANIFEST.yaml
├── entry.draft.yaml   # draft catalog entry: routing_summary, capability_tags (only tags
│                      # present in CAPABILITY_MANIFEST; new tags flagged `proposed: true`),
│                      # model_requirements as capability predicates. Certify finalizes it.
├── SPEC.md
├── src/            # implementation (B2b) or scaffold/ (B2a)
├── prompts/        # versioned prompt files (+ per-profile variants)
├── tests/          # behavioral + interface + negative-binding (+ slot-conformance)
├── eval/           # eval runner — the portability asset (model migration = rerun this)
├── trace/          # harness turn log (B2b); object storage at promotion
└── REPORT.md
```

`AGENT_MANIFEST.yaml`:
```yaml
agent_id: <slug>
kind: agent | component
mode: B2a | B2b
scope: full-agent | delta
playbook_version: 3-beta
harness: <HARNESS input verbatim>
model_profile: <MODEL_PROFILE — the profile evidence in trace/ is valid for>
target_runtime: <input>
catalog_ref: <CATALOG_REF>
behavioral_criteria_ref: <sha256 of BEHAVIORAL_CRITERIA>
interface_criteria_ref: <sha256 of INTERFACE_CRITERIA>   # full-agent scope only (both modes)
residual_ref: <sha256 of RESIDUAL>                        # delta only
target_slot: {template_id: …, slot: …}                    # delta only
bindings:
  - {id: <lock id>, version: <v>, schema_hash: <verbatim from lock>}
delegation_requirements: [<capability>, …]                # from WARN: DELEGATION_REQUIRED
trust_tier_consumed: <max tier used>
slot_value_surface:
  - {name: <param>, type: value|binding|enum, location: <config path>}
status: COMPLETE | PARTIAL | ERROR
```

## 7. Final report (`REPORT.md`)

- **Status:** `COMPLETE` / `PARTIAL` (failing criteria + last error + hypothesis each) /
  `ERROR` (code).
- **Criteria results table:** each criterion → pass/fail → test → evidence excerpt.
- **Nearest-template eval:** run mechanically applicable rows (signature-matched) of each
  `NEAREST_TEMPLATES` evalmatrix; report results; list inapplicable rows with the failed
  signature check.
- **Binding audit:** entry, tier, why unavoidable. **Delegation requirements** surfaced.
- **Warnings** with context; **known limitations**.
- **Contract checklist (mandatory, echoed verbatim with true/false per line):**
  1. `behavioral_criteria_ref` (and `interface_criteria_ref` if full-agent) computed from
     the exact input texts, unmodified.
  2. Every binding is `kind: skill|mcp-tool`, copied `(id, version, schema_hash)` verbatim
     from `CAPABILITY_MANIFEST`; zero invented tools.
  3. No `kind: agent|component|a2a-agent` invoked or bound anywhere in `src/`.
  4. No UI files, no hardcoded model identifiers, no secrets in the candidate.
  5. Artifact layout matches §6 exactly; nothing written outside the candidate directory.
  6. No criterion test deleted, skipped, or weakened during iteration.
  Any `false` → status cannot be `COMPLETE`.

Never claim `COMPLETE` without green criteria output in `trace/`/report. Honest `PARTIAL`
is acceptable; false `COMPLETE` is a certification-integrity violation.

## 8. Prohibited behaviors

- Binding or invoking other agents directly (any `kind: agent | component | a2a-agent`).
- Inventing tools/APIs absent from the manifest; assuming capability beyond an entry's schema.
- Widening scope beyond `TASK_SPEC`/`RESIDUAL`.
- Modifying/reinterpreting any frozen criteria.
- Catch-and-ignore around failing tool calls to force green.
- Hardcoding model identifiers, or would-be-slot values inside skeleton logic.
- Shipping any UI assets (HTML/templates/JS/CSS). Agent interaction surfaces are
  generated by the platform from the manifest or inherited from a template's certified
  `ui/` (see `UI-PLANE.md` §1–§2); certify rejects candidates containing UI files.
- Writing outside `agents/_proposed/<candidate>/`.
