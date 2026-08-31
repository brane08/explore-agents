# Repo brief — meta-agent platform

Plain-language orientation document. Not a spec — `docs/CATALOG.md`, `docs/ROADMAP.md`,
`docs/UI-PLANE.md`, `docs/CHECKLISTS.md`, and `docs/B2-agent-generation-playbook.md` are
the specs, human-only, versioned independently (each BETA, revisable between phases).
This file just orients a reader who hasn't read those five yet.

## What this is

A platform for a **meta-agent (orchestrator)** that, given a task, decides in order:

```
match an existing agent → match a template → cover it with existing tools (skills/MCP)
  → generate a new agent → give up with a structured, logged error
```

Each step down that cascade costs more and is trusted less, so the router always prefers
the cheapest option that actually covers the task, and a fuzzy match never silently
substitutes for a real one — an agent match gets a second, cheap confirmation call before
it's trusted, and anything the cascade truly can't place is logged as
`PATTERN_UNRECOGNIZED`, which is also the only trigger that's allowed to generate a new
agent.

## The catalog

Everything the router can reach — agents, templates, skills, MCP tools, models, A2A
agents — lives as data under content directories at the repo root (`skills/`, `mcp/`,
`a2a/`, `templates/`, `agents/`, `models/`), one directory per entry, each with an
`entry.yaml` plus kind-specific files (a schema snapshot for an MCP tool, prompts and
tests for an agent, etc). The router never reads these directly — it reads
**`catalog.lock.yaml`**, a single deterministic build artifact produced by `lockbuild`
from the whole tree. Deterministic means literally byte-identical for an identical tree:
CI builds it twice and diffs. This is what makes routing decisions auditable — the same
lock always produces the same route.

## Trust, not just presence

Being in the catalog isn't enough to be routed to. Every entry has a **tier** —
`untrusted` → `quarantined` → `validated` — recorded as an append-only ledger in
`trust.yaml`, keyed by `(id, version, model_profile)`. Only the latest record per key
counts, so a demotion ("recall") takes effect the moment it's written, even mid-session,
without rebuilding the lock. A composite entry's live tier is the minimum of what it's
built from — you can't get a `validated` result by wrapping quarantined parts.

New capability enters at `quarantined`, never `validated` directly. Getting out of
quarantine means proving it: **supervised invocation** — running the quarantined entry
alongside real traffic and comparing outputs (design in progress, see below) — until a
run streak clears a threshold, at which point `certify` (and only `certify`) writes the
promotion.

## Who writes what

- `catalog.lock.yaml` — written only by `lockbuild`, never by hand.
- `trust.yaml` — written only by `certify`, append-only, never rewritten.
- `docs/` — the five specs above — human-only; an agentic session never edits them, only
  reads them and asks the human to confirm when they've changed.
- `agents/_proposed/` — the landing zone for freshly generated candidates; belongs to the
  generation role, not the platform-building role (see roles below).

## Two roles that never share context

The same coding harness plays two different parts, deliberately kept apart:

- **Role A — generator.** Produces a new agent candidate from a residual gap. Its only
  context is the B2 playbook plus what the orchestrator injects for that one job — not
  this repo's specs, not `trust.yaml`, not certify internals. Kept minimal on purpose: an
  agent that doesn't know how certify works can't be tempted to game it.
- **Role B — platform builder.** Builds the platform itself — lockbuild, router, certify,
  UI plane — phase by phase against `ROADMAP.md`, one phase per session, `CHECKLISTS.md`'s
  `[M]` items as tests-first acceptance criteria.

## The pieces

| Piece | Job |
|---|---|
| `tooling/lockbuild/` | Parses the catalog tree, hashes each entry deterministically, resolves trust, produces `catalog.lock.yaml` |
| `tooling/certify/` | Human-gated promotion pipeline: generates a PR from a candidate, runs its eval suite, writes trust records |
| `tooling/orchestrator/` | FastAPI/htmx/SSE interaction plane: the router itself, the meta UI (task console, certify queue, canary review, catalog browser), the executor seam |

## Current state (see the companion checkpoint doc for the live snapshot)

Phases 0–2 (deterministic foundation, routing + B1, B2b + human-gated certify) are built
and merged. One real capability (`cron-next-fire-times`) has gone all the way through
generation → certify → `quarantined`. Phase 3 (templates, B1.5, quarantine's supervised-
invocation exit path) has an approved design and implementation plan but hasn't started
executing.
