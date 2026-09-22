"""Shared test fixtures."""

import pytest

from bot import momentum


@pytest.fixture(autouse=True)
def isolate_trend_history(tmp_path, monkeypatch):
    """Redirect bot.momentum's persistent store to a throwaway path.

    Without this, any test that reaches ranker.rank_articles() (which now
    calls momentum.annotate_momentum on every cycle) would read/write the
    real data/trend_history.json, leaking state between test runs and
    polluting the repo's data directory.
    """
    monkeypatch.setattr(momentum, "TREND_HISTORY_PATH", tmp_path / "trend_history.json")
