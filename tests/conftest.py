"""Defaults every test runs under."""
import pytest


@pytest.fixture(autouse=True)
def _no_llm_hold(monkeypatch):
    """Lift the dated hold on Claude-backed work.

    `entities.PAUSED_UNTIL` holds real runs until a date Cedric set. Tests
    exercise the code, not the calendar, so they run with the hold lifted --
    and the two tests that check the hold set it themselves.
    """
    monkeypatch.setenv("MT_LLM_PAUSED_UNTIL", "")
