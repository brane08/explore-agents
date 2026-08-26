# MCP Discovery & Indexing — future-phase note

Status: **deferred, not scheduled**. Logged for the phase that trips CATALOG.md §6's
scaling gate ("beyond ~500 entries, an embedding index over `routing_summary` fronts the
router"). Not a current-phase item. This is a design record, not a spec change — `docs/`
stays human-only; nothing here edits ROADMAP, CATALOG, CHECKLISTS, or UI-PLANE.

## Source

External work-package brief, "MCP Discovery & Indexing" (2026-08-13), proposing file-based
KB manifests, build-time hybrid BM25+vector indexing, no-LLM-in-the-hot-path retrieval,
index versioning against the embedding model, and an MCP connection layer for live
capability verification.

## Fit against this repo, as of today

Catalog size: 2 skills, 1 mcp-tool, 1 agent template-eligible, 1 model — nowhere near the
500-entry gate. Building the index now would be premature; CATALOG.md §6 already names
this exact evolution path as deferred-by-design, not unscoped.

Roughly a third of the brief already exists: `mcp/<id>/{entry.yaml, schema.snapshot.json,
routing_summary.md}` mirroring, schema hashing, vendor-prose stripping (tool-description
poisoning control, CATALOG §4), and routing-summary regeneration on drift are all live in
lockbuild Stage 2. What's new is the hybrid index, its versioning/invalidation, and an
MCP `tools/list`/`initialize` live-verification layer — none of which exist today.

Two of the brief's "settled decisions" reverse standing decisions rather than extend them,
and need explicit re-approval (a `docs/` edit) before any implementation, not just an
add-on design doc:

- **No LLM in the router hot path.** Today's router scores every `skill`/`mcp-tool`
  candidate with a live model call (or a lexical stub) — semantic judgment is a feature,
  not a placeholder. Adopting the brief trades that away for determinism.
- **MCP registry as a projected view of enterprise catalogs (CMDB / gateway inventory).**
  CATALOG.md §2 states "No bespoke tool integrations outside MCP" and treats mirrored
  snapshots as the only source of truth. Live projection from external catalogs is a new
  external dependency the current design deliberately avoids.

## Added requirement for whenever this phase is scoped

**Strict per-invocation token cap on router scoring**, independent of the indexing
question. The current router linearly scans every `skill`+`mcp-tool` entry and issues one
model-scoring call per candidate (`router.py`, the B1 tool-coverage step) — cost and
latency grow unbounded with catalog size, and nothing today caps how many entries or how
many tokens a single routing decision can consume. This needs to be bounded — a top-k
candidate cap before scoring, a hard token budget per routing decision, or both — as part
of (or ahead of) whatever retrieval mechanism this future phase settles on. Call this out
explicitly in that phase's design session; it applies whether the eventual retrieval layer
is the brief's hybrid index or something else.

## Open questions for that future design session

1. Why would embedding-index scaling trigger before the ≤500-entry gate, if at all?
2. Is dropping the LLM from routing-time tool matching an intentional trade (perf vs.
   semantic precision), and does it require a `docs/CATALOG.md` §2 edit first?
3. Does MCP registry integration get its own ROADMAP phase, or stay deferred like trust
   scoring (ROADMAP design-debt register)?
4. Is the projected-catalog KB read-only cache, or a second source of truth that can
   diverge from the lock — and if the latter, how does that square with §2's non-goal?
5. What's the actual per-request token/candidate cap, and where is it enforced —
   pre-scoring (candidate shortlist) or as a hard ceiling on the scorer call itself?
