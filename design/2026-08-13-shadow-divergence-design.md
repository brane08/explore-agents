# Shadow-divergence metric and supervised invocation

Status: design, approved in session 2026-08-13. Blocks Phase 3 (ROADMAP §4, design-debt register).
Implements the mechanics behind CATALOG §7 "Quarantine runtime semantics" and CHECKLISTS layer 8.

This document is a design record, not a spec change. `docs/` is human-only; nothing here
edits CATALOG, CHECKLISTS, ROADMAP, or the B2 playbook.

## 1. The problem

CATALOG §7 says quarantined agents run in *supervised invocation* — shadow execution
alongside a validated agent handling the same task class, outputs diffed and never
returned to the caller — and that `quarantined → validated` requires N supervised runs
meeting a divergence/incident threshold. "Without supervised mode, quarantine is theater."

Two things block a direct implementation.

**No validated twin exists for the population that enters quarantine.** B2b agents are
generated from a `PATTERN_UNRECOGNIZED` residual — the cascade reached them precisely
because no agent, template, or tool combination covered the task. The first promoted
agent, `cron-next-fire-times`, has no counterpart to diff against. Shadow-diff is
available for template instances and re-certifications, not for fresh B2b output.

**"Divergence" is undefined for non-deterministic outputs.** Two prose answers can both be
correct and share no tokens. Any mechanical comparison either rejects correct behaviour or
accepts everything. Delegating the judgment to a model reintroduces the gate-bootstrapping
problem the certify honesty clause already refuses to hand-wave.

Today the router refuses quarantined agents outright: `STALE_ENTRY <id> refused at live
tier quarantined`. That is safe and it is also a dead end — quarantine cannot generate the
evidence that ends quarantine.

## 2. Decisions

1. **Two supervised modes, one evidence ledger, one promotion rule.** Shadow (mechanical)
   and canary (human-adjudicated) both emit the same row shape. The promotion rule reads
   only the verdict and never learns which mode produced it.
2. **Shadow is offered only for structured output.** Divergence is defined field-wise, on
   typed fields, under explicit predicates. Prose-output agents are ineligible for shadow.
3. **Prose and no-twin agents earn validation through canary.** The quarantined agent
   serves a real caller with instant fallback; an operator adjudicates each run. No judge
   model is introduced. A prose agent with no counterpart can only leave quarantine
   through N human adjudications — a deliberate cost, accepted over standing up an ungated
   judge.
4. **The streak is a clean streak.** N consecutive clean runs; any incident resets to
   zero. There is no tolerated divergence rate.
5. **Runs live in the orchestrator DB; only the promotion decision reaches `trust.yaml`,**
   carrying a content-addressed digest of the evidence set, written by certify.

## 3. The supervised run

A supervised run is one invocation of a quarantined entry that produces exactly one
ledger row:

```
supervised_run(
  entry_id, entry_version, model_profile,   -- the key, matching trust records
  invocation_id,                            -- joins to the existing trace store
  mode,                                     -- shadow | canary
  verdict,                                  -- clean | incident | pending | void
  reason,                                   -- divergent field, rejection note, void cause
  counterpart_id,                           -- shadow only
  adjudicated_by, adjudicated_at,           -- canary and void only
  created_at
)
```

### 3.1 Shadow

Preconditions: a validated counterpart exists, and the quarantined agent declares a
structured output contract.

The **counterpart is not a new matching problem**: it is the entry the cascade would have
routed this task to if the quarantined agent did not exist. Re-run the existing cascade
with quarantined entries excluded and take the winner. This reuses routing rather than
inventing a task-class registry, and it is the correct comparison by construction — it is
what the caller would otherwise have received.

The counterpart's output is returned to the caller. The quarantined agent runs on the same
input **after the response is committed**, on the existing background-task path. Two
properties follow structurally rather than by discipline: shadow never regresses caller
latency, and shadow output cannot reach the caller because the response is already sent.

Verdict is field-wise comparison under per-field predicates. Default predicate is **exact
match on every field**. Operators declare tolerances per entry (numeric epsilon, timestamp
skew, set-equality where order is not contractual). Any field failing its predicate ⇒
`incident`, with the field named in `reason`.

### 3.2 Canary

Preconditions: shadow unavailable, and the agent is safe to expose to a real caller.

Safety is read off existing catalog data rather than a new field. An agent with no
bindings, or only bindings the operator has marked read-only, is canary-eligible
automatically. A write-capable agent requires explicit per-task-class operator opt-in,
because canary returns real output and takes real actions on a real request.

The agent serves the caller normally. The row opens as `pending` and closes when an
operator verdicts it `clean` or `incident` on the canary screen.

### 3.3 Refusal

If neither mode is available the invocation is refused, as today — but with a structured
code naming the failed precondition (`no counterpart, prose output`; `write-capable, no
canary opt-in`). The current refusal is silent about *why*, so an agent can sit in
quarantine forever with nobody able to see that no evidence path exists for it.

## 4. The promotion rule

**Key.** The streak is keyed `(id, version, model_profile)`, exactly as trust records are.
A version bump or profile migration starts a fresh key at zero. This is not a new rule:
CATALOG §7 already requires an eval-rerun on class migration and forbids inherited
evidence. The streak is evidence, so it does not cross the key.

**Counting.** Promotion requires N consecutive `clean` rows on one key. Any `incident`
resets the count to zero. `incident` covers a mechanical divergence, an operator
rejection, and an agent-attributable execution error.

**Voiding, not allowances.** Infrastructure failures — tool plane down, timeout, upstream
error — would otherwise punish the agent for something it did not do. An operator may void
a row, recording who and why. A voided row counts toward neither the streak nor the reset,
and remains in the ledger and in the evidence digest. This preserves the no-allowance
property: there is no tolerance percentage to tune, only an auditable human statement that
a specific run did not measure the agent.

A counterpart error in shadow auto-voids with reason `counterpart_error` — there was
nothing to compare against.

**N.** Default 20, mirroring the honesty clause's existing gate-graduation counter,
configurable per task class. CHECKLISTS layer 8 already carries `[H] Divergence threshold
and N reviewed against incident history per task class`, so N remains a reviewed human
judgment rather than a constant asserted here.

**Who writes it.** The orchestrator records rows and never writes `trust.yaml`. On
reaching the streak the key becomes *eligible*, surfaced on the canary screen. A human
triggers promotion; certify then computes a content-addressed digest over the exact
evidence set, writes the `validated` trust record carrying that digest, runs lockbuild,
and commits atomically — the same shape as the existing promote. Reaching N does not
auto-promote: gate autonomy is earned, and nothing here has earned it.

## 5. Components

| Where | What |
|---|---|
| `orchestrator/supervise.py` *(new)* | Mode selection, the `supervise()` wrapper around `run_invocation`, field-wise divergence comparison |
| `orchestrator/store.py` | `supervised_run` table; queries for current streak per key and for eligible keys |
| `orchestrator/trustcheck.py` | Quarantined stops being a terminal refusal; returns a supervised directive |
| `orchestrator/webapp.py` | `GET /canary/{agent_id}` and `POST /canary/{agent_id}/demote` (already in the UI-PLANE §5 endpoint contract), plus adjudicate / void actions |
| `certify` | `promote_to_validated()` + CLI subcommand: digest evidence, write trust record, lockbuild, atomic commit |

`supervise()` wraps the executor seam rather than living in the router. The executor is
already a single seam with one emitter and one sink; the wrapper is testable without a
model by injecting a fake executor and asserting the ledger row. It also keeps the
"shadow output is never returned" rule in exactly one place, which layer 8 makes an [M]
item. Routing is left alone deliberately — the router is what recall protects, and every
change there risks the recall test.

### 5.1 Where field predicates deliberately do not live

The obvious home for per-field predicates is the candidate's `AGENT_MANIFEST.yaml`. They
are not going there. That file's contract is defined by the B2 playbook, a data artifact
that must not be edited, and adding a required field would silently invalidate every
candidate a conformant harness produces.

Consequence, accepted: an agent whose output legitimately varies (a timestamp, a float)
diverges on its first shadow run, the operator adds a tolerance, and the streak restarts.
Noisier than declaring predicates up front, and it keeps the harness contract frozen while
making every tolerance an auditable human decision rather than a self-assessment by the
generating model.

## 6. Error handling

| Condition | Outcome |
|---|---|
| Counterpart errors in shadow | Row auto-voided, reason `counterpart_error` |
| Quarantined agent crashes in shadow | `incident` — attributable to the agent |
| Tool-plane outage / timeout | Operator void |
| Adjudication never arrives | Row stays `pending`; counts for nothing; backlog visible on the canary screen |
| Neither mode available | Invocation refused with the failed precondition named |

## 7. Testing

Every layer 8 [M] item becomes an automated test, and all run without a model —
`supervise()` takes the executor as an injected seam, so tests drive fake outputs and
assert ledger rows.

| Layer 8 [M] item | Test |
|---|---|
| Quarantined agents run only in supervised mode | A quarantined entry invoked directly never reaches unsupervised `run_invocation`; asserted on the call, not the output |
| Shadow outputs never returned to callers | Shadow output distinguishable from the counterpart's; response carries the counterpart's, trace carries the shadow's |
| `quarantined → validated` requires N supervised runs (or template graduation), no other path | N−1 clean ⇒ not eligible; N ⇒ eligible; tier edit outside certify rejected. Template graduation is the spec's second path and stays unimplemented here (§8), so the test asserts the streak path only and that nothing else promotes |
| Tier records keyed `(id, version, model_profile)`; evidence never inherited | Build a streak, bump version, assert streak reads zero |
| `trust.yaml` written only by certify; append-only | Orchestrator has no write path (asserted structurally); certify appends, never rewrites |
| Demotion effective at routing time | Demote mid-session; next invocation refuses without a lock rebuild |

Rules this design adds, each needing its own test: incident resets the streak; a voided row
counts for neither streak nor reset; counterpart error auto-voids; refusal carries a code
naming the failed precondition; a write-capable agent without opt-in is refused rather
than canaried.

## 8. What this design does not establish

The tests above verify the mechanism. Whether N=20 clean runs actually predicts a safe
agent is not a testable claim — it is the [H] review item, and the honesty clause's answer
is that the numbers are reviewed against incident history rather than trusted because they
appear in a config file.

Out of scope here, tracked elsewhere: router slot-extraction prompting and its eval set
(ROADMAP §4, the other Phase 3 pending-design item); template graduation as an alternative
path out of quarantine (CATALOG §9); execution of promoted agents under a real model
profile, which the current stub executor cannot do.
