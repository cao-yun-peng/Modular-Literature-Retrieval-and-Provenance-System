"""Tests for the compatibility MCP entry point."""

import main as app_main


def test_main_delegates_to_stdio_server(monkeypatch) -> None:
    monkeypatch.setattr(app_main, "run_stdio_server", lambda: 17)

    assert app_main.main() == 17
