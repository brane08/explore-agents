"""Supervised invocation (CATALOG §7 quarantine runtime semantics).

Quarantine is only meaningful if it can end: a quarantined entry runs under
supervision, and each run appends one row of evidence. Two modes produce rows —
shadow (mechanical, typed output diffed against the entry the cascade would
otherwise have routed to) and canary (the agent serves a real caller, a human
adjudicates). The promotion rule reads only the verdict, never the mode.

Design record: design/2026-08-13-shadow-divergence-design.md
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupervisionPlan:
    mode: str                       # shadow | canary | refuse
    counterpart: dict | None = None
    reason: str = ""


def is_read_only(entry: dict, read_only_bindings: set[str]) -> bool:
    """Canary returns real output and takes real actions on a real request, so
    eligibility is read off the bindings already in the lock rather than a new
    declaration: no bindings, or only bindings an operator has marked read-only."""
    return all(b["id"] in read_only_bindings for b in entry.get("bindings") or [])


def _structured(entry: dict) -> bool:
    return bool((entry.get("model_requirements") or {}).get("structured_output"))


def select_mode(entry: dict, *, counterpart: dict | None,
                read_only_bindings: set[str], canary_opt_in: set[str]) -> SupervisionPlan:
    # Shadow's background run executes the quarantined agent's real write
    # bindings just as canary's foreground run does ("takes real actions on a
    # real request" — design §3.2 applies verbatim to §3.1's background run).
    # The opt-in gate therefore applies before either mode is offered, not
    # only before canary.
    eligible = is_read_only(entry, read_only_bindings) or entry["id"] in canary_opt_in
    if counterpart is not None and _structured(entry):
        if eligible:
            return SupervisionPlan("shadow", counterpart=counterpart)
        return SupervisionPlan(
            "refuse",
            reason="write-capable without canary opt-in — no evidence path",
        )
    # canary needs a counterpart too: CATALOG §7 defines the mode as "canary
    # routing with human-visible results and instant fallback" — with no
    # counterpart there is nothing to fall back to, so an agent-attributable
    # failure would propagate the raw error straight to the real caller.
    if counterpart is not None and eligible:
        return SupervisionPlan("canary", counterpart=counterpart)
    missing = "no counterpart" if counterpart is None else "prose output"
    return SupervisionPlan(
        "refuse",
        reason=f"{missing}; write-capable without canary opt-in — no evidence path",
    )


@dataclass(frozen=True)
class FieldPredicate:
    """Operator-declared comparison rule for one output field.

    Predicates deliberately do not live in AGENT_MANIFEST.yaml: that contract
    belongs to the B2 playbook, and adding a required field would invalidate
    every candidate a conformant harness produces (design §5.1). The cost is
    that an agent with a legitimately varying field diverges once, an operator
    declares the tolerance, and the streak restarts.
    """
    kind: str = "exact"             # exact | numeric | set
    tolerance: float = 0.0


_DEFAULT_PREDICATE = FieldPredicate()


def _field_matches(a, b, predicate: FieldPredicate) -> bool:
    if predicate.kind == "numeric":
        try:
            return abs(float(a) - float(b)) <= predicate.tolerance
        except (TypeError, ValueError):
            return False
    if predicate.kind == "set":
        try:
            return sorted(a) == sorted(b)
        except TypeError:
            return False
    return a == b


def compare_outputs(quarantined: dict, counterpart: dict,
                    predicates: dict[str, FieldPredicate]) -> tuple[str, str]:
    """Field-wise comparison; any field failing its predicate is an incident.

    Fields are visited in sorted order so the recorded reason is the same on
    every run — a reason that depends on dict ordering is not evidence.
    """
    missing = sorted(set(counterpart) - set(quarantined))
    extra = sorted(set(quarantined) - set(counterpart))
    for name in missing:
        return "incident", f"field '{name}' missing from the quarantined output"
    for name in extra:
        return "incident", f"field '{name}' present only in the quarantined output"
    for name in sorted(counterpart):
        predicate = predicates.get(name, _DEFAULT_PREDICATE)
        if not _field_matches(quarantined[name], counterpart[name], predicate):
            return "incident", (f"field '{name}' differs: "
                                f"{quarantined[name]!r} vs {counterpart[name]!r}")
    return "clean", ""


def open_shadow(store, *, entry: dict, model_profile: str, invocation_id: str,
                counterpart_id: str) -> int:
    """Opened before the quarantined agent runs (invoke-ui#4): a crash between
    the run completing and the row committing must not leave zero evidence
    for a quarantined agent that served real work."""
    return store.open_supervised_run(
        entry_id=entry["id"], entry_version=entry["version"],
        model_profile=model_profile, invocation_id=invocation_id,
        mode="shadow", counterpart_id=counterpart_id,
    )


def run_shadow(store, *, run_id: int, counterpart_output: dict | None,
               shadow_output: dict | None, error: str | None,
               predicates: dict[str, FieldPredicate]) -> int:
    if counterpart_output is None:
        store.close_supervised_run(run_id, verdict="void", reason="counterpart_error")
    elif error is not None or shadow_output is None:
        store.close_supervised_run(run_id, verdict="incident",
                                   reason=error or "no output produced")
    else:
        verdict, reason = compare_outputs(shadow_output, counterpart_output, predicates)
        store.close_supervised_run(run_id, verdict=verdict, reason=reason)
    return run_id


def open_canary(store, *, entry: dict, model_profile: str, invocation_id: str) -> int:
    return store.open_supervised_run(
        entry_id=entry["id"], entry_version=entry["version"],
        model_profile=model_profile, invocation_id=invocation_id, mode="canary",
    )
