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
from textual.binding import Binding  # noqa: E402
from textual.widgets import Footer, Header, Input, RichLog  # noqa: E402

PROMPT_CRON = "Enter a cron expression (e.g. '*/15 * * * *'):"
PROMPT_START = "Enter a start timestamp (ISO-8601 UTC, e.g. 2026-08-12T10:07:00Z):"


class ChatApp(App):
    """Two-step chat: cron expression, then start timestamp, then the result."""

    AUTO_FOCUS = "#input"
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]
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
        log.write("cron-next-fire-times console. Ctrl+Q to quit.")
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
            self._render_result(log, result)
        except Exception as exc:  # noqa: BLE001 - console must not crash on a bad turn
            log.write("error: unhandled exception: %r" % exc)

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
