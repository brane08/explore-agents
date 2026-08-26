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


def test_console_chat_module_exposes_app_and_main():
    pytest.importorskip("textual")
    from textual.app import App

    if CONSOLE_DIR not in sys.path:
        sys.path.insert(0, CONSOLE_DIR)
    import chat

    assert hasattr(chat, "ChatApp")
    assert hasattr(chat, "main")
    assert issubclass(chat.ChatApp, App)


def test_input_has_focus_and_accepts_real_keypresses():
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import Input

    if CONSOLE_DIR not in sys.path:
        sys.path.insert(0, CONSOLE_DIR)
    import chat

    async def scenario():
        app = chat.ChatApp()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", Input)
            assert input_widget.has_focus

            cron_expression = "*/15 * * * *"
            for ch in cron_expression:
                await pilot.press(ch)
            await pilot.press("enter")

            assert app._awaiting == "start"

    asyncio.run(scenario())
