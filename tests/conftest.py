"""Defaults every test runs under."""
import pytest


@pytest.fixture(autouse=True)
def _no_holds(monkeypatch):
    """Lift both dated holds: Claude-backed work, and fetching.

    `entities.PAUSED_UNTIL` and `fetch.PAUSED_UNTIL` hold real runs until dates
    Cedric set. Tests exercise the code, not the calendar, so they run with the
    holds lifted -- and the tests that check a hold set it themselves.
    """
    monkeypatch.setenv("MT_LLM_PAUSED_UNTIL", "")
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "")
