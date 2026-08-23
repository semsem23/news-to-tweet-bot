"""Tests for pipeline interval gating and cycle behavior."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from bot.models import PostedEntry
from bot.pipeline import run_cycle


class TestIntervalGate:
    def test_interval_gate_skips_recent_post(self):
        """If last post was <90 min ago, cycle should skip during non-dry-run."""
        client = MagicMock()
        now_utc = datetime.now(timezone.utc)
        recent_post_time = now_utc - timedelta(minutes=60)

        recent_entry = PostedEntry(
            link="https://example.com/old",
            title="Old story",
            posted_at=recent_post_time.isoformat(),
        )

        with patch("bot.pipeline.history.load_history") as mock_load:
            with patch("bot.pipeline.history.prune_history") as mock_prune:
                with patch("bot.pipeline.fetcher.fetch_all_feeds") as mock_fetch:
                    mock_load.return_value = [recent_entry]
                    mock_prune.return_value = [recent_entry]

                    run_cycle(client, dry_run=False)

                    # fetch_all_feeds should NOT have been called when interval gate blocks
                    mock_fetch.assert_not_called()

    def test_interval_gate_allows_old_post(self):
        """If last post was >90 min ago, cycle should proceed."""
        client = MagicMock()
        now_utc = datetime.now(timezone.utc)
        old_post_time = now_utc - timedelta(minutes=120)

        old_entry = PostedEntry(
            link="https://example.com/old",
            title="Old story",
            posted_at=old_post_time.isoformat(),
        )

        with patch("bot.pipeline.history.load_history") as mock_load:
            with patch("bot.pipeline.history.prune_history") as mock_prune:
                with patch("bot.pipeline.fetcher.fetch_all_feeds") as mock_fetch:
                    with patch("bot.pipeline.fetcher.merge_and_dedup_articles") as mock_merge:
                        with patch("bot.pipeline.log"):
                            mock_load.return_value = [old_entry]
                            mock_prune.return_value = [old_entry]
                            mock_fetch.side_effect = RuntimeError("Test: fetch failed as expected")

                            run_cycle(client, dry_run=False)

                            # fetch_all_feeds SHOULD have been called (fetch error is ok for this test)
                            mock_fetch.assert_called_once()

    def test_dry_run_skips_interval_gate(self):
        """In dry-run mode, interval gate should be skipped."""
        client = MagicMock()
        now_utc = datetime.now(timezone.utc)
        recent_post_time = now_utc - timedelta(minutes=30)

        recent_entry = PostedEntry(
            link="https://example.com/old",
            title="Old story",
            posted_at=recent_post_time.isoformat(),
        )

        with patch("bot.pipeline.history.load_history") as mock_load:
            with patch("bot.pipeline.history.prune_history") as mock_prune:
                with patch("bot.pipeline.fetcher.fetch_all_feeds") as mock_fetch:
                    with patch("bot.pipeline.log"):
                        mock_load.return_value = [recent_entry]
                        mock_prune.return_value = [recent_entry]
                        mock_fetch.side_effect = RuntimeError("Test: fetch failed as expected")

                        run_cycle(client, dry_run=True)

                        # In dry-run, should attempt to fetch (not skip due to interval gate)
                        mock_fetch.assert_called_once()

    def test_empty_history_allows_post(self):
        """If history is empty (first post), cycle should proceed."""
        client = MagicMock()

        with patch("bot.pipeline.history.load_history") as mock_load:
            with patch("bot.pipeline.history.prune_history") as mock_prune:
                with patch("bot.pipeline.fetcher.fetch_all_feeds") as mock_fetch:
                    with patch("bot.pipeline.log"):
                        mock_load.return_value = []
                        mock_prune.return_value = []
                        mock_fetch.side_effect = RuntimeError("Test: fetch failed as expected")

                        run_cycle(client, dry_run=False)

                        # fetch_all_feeds SHOULD be called (no history to gate on)
                        mock_fetch.assert_called_once()
