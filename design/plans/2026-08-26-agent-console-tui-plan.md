# Agent Console TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `agents/cron-next-fire-times` a self-contained terminal chat harness (`console/chat.py`) so someone can run that one agent directory and hold a conversation with it, without the orchestrator's web plane.

**Architecture:** A single-file Textual app, sibling to `eval/run.py`, that imports the agent's own `src/agent.py:run` directly (in-process, no subprocess/HTTP/IPC) and drives a two-step prompt (cron expression, then start timestamp) per turn, looping for further turns until the user quits.

**Tech Stack:** Python 3.11+, Textual (new dependency, declared only inside this agent's `console/` directory — not added to the workspace `pyproject.toml`), the agent's existing `src/agent.py`.

**Spec:** `design/2026-08-26-agent-console-tui-design.md`

## Global Constraints

- No shared `tooling/agentconsole/` package — `console/chat.py` is self-contained and copy-paste kin to every other agent's console, not imported from a common base (spec: Decisions).
- Imports only this agent's own `src/agent.py` (or graph entrypoint) plus Textual — no dependency on `tooling/orchestrator` or the Meta UI (spec: Decisions, Architecture).
- Runs entirely in-process and locally: no server, no network call beyond whatever the agent's own code makes (spec: Architecture) — this agent makes none.
- Bindings-free v1 scope only (spec: Decisions) — `cron-next-fire-times` has zero bindings, so no work needed to enforce this here beyond not adding any.
- A graph-invoke exception is caught at the app level and rendered as error text in the log; it never crashes the TUI (spec: Error handling).
- `console/chat.py` must be syntactically valid and importable without side effects (spec: Testing) — no code may run at import time outside an `if __name__ == "__main__":` guard.
- `docs/` stays human-only — this plan makes no edits to `docs/B2-agent-generation-playbook.md` or `docs/UI-PLANE.md`; the required carve-out text is handed to the user separately, not executed here.

---

### Task 1: Declare the Textual dependency inside the agent's `console/` directory

**Files:**
- Create: `agents/cron-next-fire-times/console/requirements.txt`
- Test: none (a plain data file; verified by Task 2's smoke test failing/passing on `textual` import)

**Interfaces:**
- Consumes: nothing.
- Produces: a `textual` version floor that Task 2 relies on being installable via `pip install -r agents/cron-next-fire-times/console/requirements.txt`.

There is no per-agent Python package or dependency mechanism today (`agents/` dirs are catalog data, never Python packages — `AGENTS.md`/`CLAUDE.md`), and the workspace root `pyproject.toml` only lists the three `tooling/*` packages as members. Adding `textual` there would make it a platform-wide dependency, which contradicts "self-contained per agent." Instead, declare it as a plain requirements file living with the console, so the agent directory stays portable on its own — install it only when you want to run that agent's console.

- [ ] **Step 1: Create the requirements file**

```text
textual>=0.60
```

Write this single line to `agents/cron-next-fire-times/console/requirements.txt`.

- [ ] **Step 2: Commit**

```bash
git add agents/cron-next-fire-times/console/requirements.txt
git commit -m "feat(cron-next-fire-times): declare console's textual dependency"
```

---

### Task 2: Write the console chat app

**Files:**
- Create: `agents/cron-next-fire-times/console/chat.py`
- Test: `agents/cron-next-fire-times/tests/test_console_chat.py` (Task 3)

**Interfaces:**
- Consumes: `src/agent.py:run(cron_expression: Any, start_timestamp: Any, config: Optional[AgentConfig] = None) -> Dict[str, Any]`, returning either `{"status": "ok", "cron_expression", "start_timestamp", "timezone", "fire_times": [str, ...]}` or `{"status": "error", "error": {"code": str, "message": str}}` (confirmed against `agents/cron-next-fire-times/src/agent.py` and `SPEC.md` §3 Output contract).
- Produces: `console.chat.ChatApp` (a `textual.app.App` subclass) and `console.chat.main() -> int`, both importable with no side effects, matching Task 3's test.

`run()` never raises for bad input per its own docstring and `SPEC.md` — every outcome is a result envelope — but the console still wraps the call in `try/except` per the spec's Error handling section, since a console harness must not assume every future agent's entrypoint is equally well-behaved.

This resolves the design spec's open question about checkpointer behavior for this agent: `cron-next-fire-times` is stateless and single-shot (no LangGraph checkpointer anywhere in `src/graph.py` or `src/agent.py`), so the console's multi-turn loop is purely in-app state (`self._cron_expression`, `self._awaiting`) — there is no external session/thread id to manage.

- [ ] **Step 1: Write the app**

Create `agents/cron-next-fire-times/console/chat.py`:

```python
#!/usr/bin/env python3
"""Standalone terminal chat harness for the cron-next-fire-times agent.

Self-contained: imports only this agent's own src/agent.py plus Textual.
No orchestrator, no network, no shared agentconsole package.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent import run  # noqa: E402

from textual.app import App, ComposeResult  # noqa: E402
from textual.widgets import Footer, Header, Input, RichLog  # noqa: E402

PROMPT_CRON = "Enter a cron expression (e.g. '*/15 * * * *'):"
PROMPT_START = "Enter a start timestamp (ISO-8601 UTC, e.g. 2026-08-12T10:07:00Z):"


class ChatApp(App):
    """Two-step chat: cron expression, then start timestamp, then the result."""

    BINDINGS = [("q", "quit", "Quit")]
    CSS = "Input { dock: bottom; }"

    def __init__(self) -> None:
        super().__init__()
        self._cron_expression = None
        self._awaiting = "cron"

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", wrap=True)
        yield Input(placeholder="cron expression", id="input")
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.write("cron-next-fire-times console. Ctrl+C or 'q' to quit.")
        log.write(PROMPT_CRON)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#input", Input).value = ""
        if not text:
            return

        log = self.query_one("#log", RichLog)
        log.write("> " + text)

        if self._awaiting == "cron":
            self._cron_expression = text
            self._awaiting = "start"
            log.write(PROMPT_START)
            return

        start_timestamp = text
        try:
            result = run(self._cron_expression, start_timestamp)
        except Exception as exc:  # noqa: BLE001 - console must not crash on a bad turn
            log.write("error: unhandled exception: %r" % exc)
        else:
            self._render_result(log, result)

        self._cron_expression = None
        self._awaiting = "cron"
        log.write(PROMPT_CRON)

    @staticmethod
    def _render_result(log: RichLog, result: dict) -> None:
        if result.get("status") == "ok":
            log.write("fire_times (%s):" % result.get("timezone"))
            for moment in result.get("fire_times", []):
                log.write("  " + moment)
        else:
            error = result.get("error", {})
            log.write("error [%s]: %s" % (error.get("code"), error.get("message")))


def main() -> int:
    ChatApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Manual smoke run**

```bash
pip install -r agents/cron-next-fire-times/console/requirements.txt
python agents/cron-next-fire-times/console/chat.py
```

Type `*/15 * * * *`, press Enter, type `2026-08-12T10:07:00Z`, press Enter. Expect five ascending UTC fire times printed, then the cron prompt again. Type an invalid expression (e.g. `not-a-cron`) followed by any timestamp and confirm an `error [INVALID_CRON]` line appears instead of a crash. Quit with `q`.

- [ ] **Step 3: Commit**

```bash
git add agents/cron-next-fire-times/console/chat.py
git commit -m "feat(cron-next-fire-times): add self-contained console chat harness"
```

---

### Task 3: Smoke-test the console module

**Files:**
- Create: `agents/cron-next-fire-times/tests/test_console_chat.py`

**Interfaces:**
- Consumes: `console.chat.ChatApp`, `console.chat.main` (Task 2).
- Produces: nothing further consumed downstream — this is the terminal test task for the plan.

Matches the spec's Testing section: a syntax/importability smoke check, no interactive `Pilot`-based TUI testing in v1. The syntax check runs with no dependencies (catches a broken file even where `textual` isn't installed); the import check is skipped when `textual` isn't installed rather than failing the suite, since `textual` is a console-only dependency, not a workspace dependency (Task 1).

- [ ] **Step 1: Write the failing test**

Create `agents/cron-next-fire-times/tests/test_console_chat.py`:

```python
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSOLE_DIR = os.path.join(ROOT, "console")
CHAT_PATH = os.path.join(CONSOLE_DIR, "chat.py")


def test_console_chat_is_syntactically_valid():
    with open(CHAT_PATH, encoding="utf-8") as handle:
        source = handle.read()
    ast.parse(source, filename=CHAT_PATH)


def test_console_chat_importable_without_side_effects():
    pytest.importorskip("textual")
    for path in (ROOT, os.path.join(ROOT, "src"), CONSOLE_DIR):
        if path not in sys.path:
            sys.path.insert(0, path)
    import chat

    assert hasattr(chat, "ChatApp")
    assert hasattr(chat, "main")
    assert issubclass(chat.ChatApp, object)
```

- [ ] **Step 2: Run it to verify the syntax test passes and the import test's skip/pass behavior is sane**

Run: `pytest agents/cron-next-fire-times/tests/test_console_chat.py -v`

Expected before Task 2 exists: both tests FAIL (`chat.py` not found / not importable). Run again after Task 2's file is in place:

Expected: `test_console_chat_is_syntactically_valid` PASSES. `test_console_chat_importable_without_side_effects` PASSES if `textual` is installed in the current environment, or is reported SKIPPED if it isn't — either is correct; a FAIL is not.

- [ ] **Step 3: Install textual locally and confirm the import test actually exercises the module (not just skipping)**

```bash
pip install -r agents/cron-next-fire-times/console/requirements.txt
pytest agents/cron-next-fire-times/tests/test_console_chat.py -v
```

Expected: both tests PASS (no SKIPPED).

- [ ] **Step 4: Commit**

```bash
git add agents/cron-next-fire-times/tests/test_console_chat.py
git commit -m "test(cron-next-fire-times): smoke-test the console chat module"
```

---

## Post-plan human follow-up (not part of this plan's tasks)

`docs/` is human-only for this session — none of the three tasks above touch it. Two edits are needed from you before this stops looking like a playbook violation to certify:

1. **`docs/B2-agent-generation-playbook.md` §8** — add a carve-out for `console/chat.py` (and its `console/requirements.txt`) alongside the existing `eval/` allowance, so certify stops treating it as a prohibited UI asset.
2. **`docs/UI-PLANE.md` §1** — optionally, a note distinguishing this harness from "Agent UI" so a future reader doesn't read it as a derived-UI-rule violation.

I can hand you exact diff text for either when you're ready; not drafted here since I can't apply it myself.
