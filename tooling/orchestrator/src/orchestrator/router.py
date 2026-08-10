"""Routing cascade over the pinned-epoch lock (CATALOG §1, CHECKLISTS layer 3).

    task → match agent → post-match confirmation → invoke
         → match template (B1.5 — Phase 3)
         → tool coverage  → B1 assembly
         → residual gap   → PATTERN_UNRECOGNIZED (B2 backlog — Phase 2)

Hard-stop policy: no fuzzy fallback between tiers; every no-match emits a
structured §11 error. Stale entries are refused and queued. Tier checks are
transitive and *live* (recall pierces the epoch pin).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.config import Settings
from orchestrator.errors import StructuredError, normalize_residual
from orchestrator.lockload import entries_by_kind
from orchestrator.scoring import Scorer, as_batch_scorer
from orchestrator.store import Store
from orchestrator.trustcheck import TIER_ORDER, load_records, transitive_tier


@dataclass
class RoutingEvent:
    cascade_step: str
    entry_id: str | None = None
    confirmation_score: float | None = None
    note: str = ""

    def data(self) -> dict:
        out: dict = {"cascade_step": self.cascade_step}
        if self.entry_id is not None:
            out["entry_id"] = self.entry_id
        if self.confirmation_score is not None:
            out["confirmation_score"] = round(self.confirmation_score, 3)
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class RoutingResult:
    outcome: str                      # invoke-agent | assemble-b1 | error
    events: list[RoutingEvent] = field(default_factory=list)
    agent_entry: dict | None = None   # for invoke-agent
    tools: list[dict] = field(default_factory=list)  # for assemble-b1
    error: StructuredError | None = None


def allowed_tiers(user_sub: str, settings: Settings, operator: bool = False) -> set[str]:
    """User authorization joined against tiers before invoke (layer 3).
    Operators may exercise anything; everyone else only validated entries."""
    if operator or user_sub in settings.operator_users:
        return set(TIER_ORDER)
    return {"validated"}


def route(
    task_text: str,
    lock: dict,
    catalog_root: Path,
    user_sub: str,
    scorer: Scorer,
    settings: Settings,
    store: Store,
    operator: bool = False,
) -> RoutingResult:
    events: list[RoutingEvent] = []
    records = load_records(catalog_root)
    user_tiers = allowed_tiers(user_sub, settings, operator)
    # One route, one scorer cache: the entries scored in step 1 and step 3 are
    # independent of each other, so they go out concurrently instead of one
    # blocking round trip per catalog entry.
    scorer = as_batch_scorer(scorer)

    # --- 1. existing agent match -------------------------------------------
    # candidates above threshold, best first; stale ones are refused + queued
    # individually so a stale best match never shadows a healthy second-best
    agents = list(entries_by_kind(lock, "agent", "composite"))
    scored = sorted(
        zip(scorer.batch(task_text, [e.get("routing_summary", "") for e in agents]),
            agents),
        key=lambda t: (-t[0], t[1]["id"]),
    )
    for score, entry in scored:
        if score < settings.match_threshold:
            break
        if entry.get("stale"):
            # refused, queued for rebase/eval-rerun; try the next candidate
            store.queue_stale(entry["id"], entry.get("stale_reason", "stale"))
            events.append(RoutingEvent(
                "agent-match", entry["id"],
                note=f"STALE_ENTRY {entry['id']} {entry.get('stale_reason', '')}".strip(),
            ))
            continue
        confirmation = scorer(
            task_text,
            f"{entry.get('routing_summary', '')} {entry.get('detail', '')}".strip(),
        )
        events.append(RoutingEvent("agent-match", entry["id"], confirmation_score=confirmation))
        if confirmation >= settings.confirm_threshold:
            tier = transitive_tier(records, entry, lock, settings.model_profile)
            if tier not in user_tiers:
                # live (recalled or insufficient) tier — refuse, no fallback
                store.queue_stale(entry["id"], f"tier:{tier}")
                err = StructuredError("STALE_ENTRY", f"{entry['id']} refused at live tier {tier}")
                events.append(RoutingEvent("refused", entry["id"], note=err.context))
                return RoutingResult("error", events, error=err)
            return RoutingResult("invoke-agent", events, agent_entry=entry)
        break  # confirmation below threshold → treated as no-match; cascade continues

    # --- 2. template match (no templates until Phase 3) ----------------------
    events.append(RoutingEvent("template-match", note="no template entries"))

    # --- 3. B1 tool coverage --------------------------------------------------
    tools = []
    candidates: list[tuple[dict, str]] = []
    for entry in entries_by_kind(lock, "skill", "mcp-tool"):
        if entry.get("stale"):
            store.queue_stale(entry["id"], entry.get("stale_reason", "stale"))
            events.append(RoutingEvent(
                "b1-coverage", entry["id"],
                note=f"STALE_ENTRY {entry['id']} {entry.get('stale_reason', '')}".strip(),
            ))
            continue
        candidates.append((entry, " ".join([
            entry.get("routing_summary", ""),
            " ".join(entry.get("capability_tags", [])),
            entry.get("detail", ""),
        ])))
    scores = scorer.batch(task_text, [text for _, text in candidates])
    for (entry, _), score in zip(candidates, scores):
        live = transitive_tier(records, entry, lock, settings.model_profile)
        if score >= settings.coverage_threshold and live == "validated":
            tools.append((score, entry))
    if tools:
        tools.sort(key=lambda t: (-t[0], t[1]["id"]))
        chosen = [e for _, e in tools]
        events.append(RoutingEvent(
            "b1-coverage", note=",".join(e["id"] for e in chosen)))
        return RoutingResult("assemble-b1", events, tools=chosen)

    # --- 4. residual gap --------------------------------------------------------
    residual = normalize_residual(task_text)
    store.record_error("PATTERN_UNRECOGNIZED", residual)
    err = StructuredError("PATTERN_UNRECOGNIZED", residual)
    events.append(RoutingEvent("unrecognized", note=residual))
    return RoutingResult("error", events, error=err)
