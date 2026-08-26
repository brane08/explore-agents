# Per-agent console TUI — design

Status: **approved**, not yet planned/implemented. This is a design record, not a spec
change — `docs/` stays human-only; nothing here edits ROADMAP, CATALOG, CHECKLISTS, or
UI-PLANE until the human-authored edits described below land.

## Problem

Today the only way to talk to a generated agent is through the orchestrator's FastAPI/
htmx Meta UI, or by scripting `src/graph.py` directly. There's no lightweight way for
someone to clone/receive a single generated agent and just start a conversation with it
from a terminal — the closest existing precedent is `eval/run.py`, which runs an agent
against fixture inputs but isn't an interactive chat surface.

## Constraint this design has to satisfy

`docs/UI-PLANE.md` §1: "Agent UI is always derived, never bespoke per agent" — the Meta
UI is generated from the manifest or inherited from a template's certified `ui/`, never
hand-built per agent. `docs/B2-agent-generation-playbook.md` §8 explicitly prohibits
shipping UI assets in a candidate and says certify rejects candidates containing UI
files.

Resolution: this is not a product UI. It's a bundled **testing/dev harness**, the same
category `eval/run.py` already occupies and that the playbook already permits — a
conversational counterpart to running fixtures, not an interaction surface the platform
serves to end users. It still needs one human-made carve-out (below) since the current
playbook text doesn't yet distinguish "UI asset" from "harness that happens to render
text in a terminal."

## Decisions

- **Location & shape:** `console/chat.py`, a sibling to `eval/run.py` inside each
  generated agent's own directory — not a new shared package.
- **Self-contained, no shared package.** No `tooling/agentconsole/`. Each agent's
  `console/chat.py` is copy-paste kin to every other agent's, duplicated rather than
  imported from a common base. It imports only that agent's own `src/graph.py` (or
  whatever the manifest names as the build/invoke entrypoint — the same convention
  `eval/run.py` already follows) plus Textual.
- **UI framework: Textual**, a new dependency (no existing per-agent dependency
  declaration mechanism yet — see Open questions). Chosen over a stdlib-only REPL.
- **Scope for v1: bindings-free agents only.** Agents with tool bindings/tool-calling
  are out of scope for this harness until a later phase.
- **No dependency on `tooling/orchestrator`.** The console never calls
  `run_invocation`/the executor seam — it drives the agent's graph directly, in-process.

## Architecture

`console/chat.py` is a single-file Textual app:

- One `Input` widget for the user's message, one scrolling log widget (`RichLog` or
  per-message `Static`) for the transcript.
- On submit, calls the agent's graph the same way `eval/run.py` does today (exact call
  shape confirmed against the real precedent at plan-writing time, not re-derived here).
- Renders the response into the log; keeps simple in-memory turn history for multi-turn
  agents. Session/thread-id handling follows whatever the agent's own graph already
  expects — no new state contract is invented by this design.
- Runs entirely locally: `python console/chat.py` starts a terminal chat session
  in-process. No server, no network call except whatever the graph's own nodes make
  (e.g. LLM calls).

## Data flow

```
User types in Input widget
  -> ChatApp handler
  -> src.graph invoke/astream call (in-process, same interpreter)
  -> response
  -> appended to RichLog
```

No IPC, no subprocess, no HTTP. If the agent's graph is checkpointed (Postgres
checkpointer per repo stack defaults), whether the console runs against a local/dev
checkpointer or a stateless per-turn mode needs to be confirmed against what
`cron-next-fire-times` actually assumes when the implementation plan is written — flagged
here, not resolved in this design.

## Error handling

Mirrors `eval/run.py`'s posture: a graph-invoke exception is caught at the app level and
rendered as error text in the log — it does not crash the TUI. The user can keep typing
or quit. No retry logic, no fallback model, no silent swallowing. `Ctrl+C`/`q` exits
cleanly, releasing whatever the graph itself opened (e.g. a checkpointer connection).

## Testing

Per-agent artifact, not platform code — no new `tooling/*/tests/` suite. In scope for
v1: a smoke check that `console/chat.py` is syntactically valid and importable without
side effects (mirroring whatever check, if any, `eval/run.py` already gets — confirmed
at plan time). Interactive TUI testing via Textual's `Pilot` harness is out of scope for
v1 (more machinery than a bindings-free harness warrants) — logged as a possible **[H]**
review-note item, not an **[M]** pytest.

## Required human follow-up (not done by this design)

- `docs/B2-agent-generation-playbook.md` §8 needs an edit carving out `console/chat.py`
  the same way `eval/run.py` is already permitted, so certify stops rejecting it as a UI
  asset. Exact text to be handed over once this design's plan is written.
- `docs/UI-PLANE.md` may need a note distinguishing this harness from "Agent UI" per §1,
  so a future reader doesn't read it as a violation of the derived-UI rule.

## Open questions (not blocking, logged for the plan/implementation session)

1. No agent currently declares its own Python dependencies independently of the
   platform's environment — where does "this agent's `console/chat.py` needs
   `textual`" get declared and installed? Needs resolving before/during implementation,
   not assumed here.
2. Exact checkpointer behavior for multi-turn state in the console (see Data flow).
3. Whether the eventual playbook carve-out should name `console/` as a directory (like
   `eval/`) or something narrower.
