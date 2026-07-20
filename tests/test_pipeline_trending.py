"""Regression tests for pipeline trending match logic with multi-candidate cycling.

These tests exercise the REAL run_cycle() pipeline to ensure that if someone breaks
the candidate-matching loop back to only testing the first candidate (the bug we fixed),
these tests will catch it.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from bot.config import PARIS_TZ
from bot.models import Article


PARIS = ZoneInfo("Europe/Paris")


def make_article(title: str, source: str, hours_ago: float = 0.5) -> Article:
    """Create an Article object as fetcher.parse_entries() would produce."""
    now_utc = datetime.now(timezone.utc)
    age_utc = now_utc - timedelta(hours=hours_ago)
    age_paris = age_utc.astimezone(PARIS_TZ)
    return Article(
        title=title,
        raw_title=f"{title} - {source}",
        source=source,
        link=f"https://example.com/article-{hash(title) % 10000}",
        published_utc=age_utc.isoformat(),
        published_paris=age_paris.isoformat(),
    )


class TestPipelineTrendingMultiCandidate:
    """Test that run_cycle() cycles through multiple candidates for trending match."""

    @patch("bot.pipeline.history.save_history")
    @patch("bot.pipeline.history.prune_history")
    @patch("bot.pipeline.history.load_history")
    @patch("bot.pipeline.history.is_duplicate")
    @patch("bot.poster.post_story")
    @patch("bot.pipeline.trends.fetch_trending_tokens")
    @patch("bot.pipeline.fetcher.parse_entries")
    @patch("bot.pipeline.fetcher.fetch_active_feeds")
    def test_select_third_candidate_when_first_two_dont_match_trending(
        self, mock_fetch_feeds, mock_parse_entries, mock_trending,
        mock_post_story, mock_is_dup, mock_load_hist, mock_prune_hist, mock_save_hist
    ):
        """
        Regression test: when #1 and #2 don't match trending but #3 does,
        run_cycle() selects #3 (not skip the cycle).

        This test calls the REAL run_cycle() to verify the multi-candidate loop works.
        """
        from bot.pipeline import run_cycle

        # Setup: mock history (no prior posts)
        mock_load_hist.return_value = []
        mock_prune_hist.return_value = []
        mock_is_dup.return_value = False

        # Setup: mock articles (ordered by freshness to pass ranking)
        articles = [
            make_article("Story about politics and senators", "Reuters", 0.3),  # #1: won't match trending
            make_article("Story about weather and rain", "BBC", 0.4),           # #2: won't match trending
            make_article("Artificial intelligence breakthrough major", "AP", 0.2),  # #3: will match (freshest)
        ]
        mock_fetch_feeds.return_value = MagicMock(entries=[])
        mock_parse_entries.return_value = articles

        # Setup: mock trending data that matches only story #3
        trending_tokens = [
            {"artif", "intel", "break"},  # Matches "Artificial intelligence breakthrough"
        ]
        mock_trending.return_value = trending_tokens

        # Setup: mock post_story to return a tweet ID
        mock_post_story.return_value = "tweet_id_123"

        # Mock the client
        mock_client = MagicMock()

        # Call the real pipeline
        run_cycle(mock_client, dry_run=False)

        # Verify: post_story was called (story was selected and posted)
        assert mock_post_story.called, "post_story should have been called for the matching candidate"
        # Verify: the selected tweet should contain "Artificial intelligence" or stem "artif"
        posted_tweet = mock_post_story.call_args[0][1]  # second arg is the tweet text
        assert "Artificial intelligence" in posted_tweet.lower() or "artif" in posted_tweet.lower(), \
            f"Expected AI-related keywords in tweet, got: {posted_tweet}"

    @patch("bot.pipeline.history.save_history")
    @patch("bot.pipeline.history.prune_history")
    @patch("bot.pipeline.history.load_history")
    @patch("bot.pipeline.history.is_duplicate")
    @patch("bot.poster.post_story")
    @patch("bot.pipeline.trends.fetch_trending_tokens")
    @patch("bot.pipeline.fetcher.parse_entries")
    @patch("bot.pipeline.fetcher.fetch_active_feeds")
    def test_skip_when_all_candidates_fail_trending_match(
        self, mock_fetch_feeds, mock_parse_entries, mock_trending,
        mock_post_story, mock_is_dup, mock_load_hist, mock_prune_hist, mock_save_hist
    ):
        """
        Regression test: skip only happens when ALL top-N non-duplicate candidates
        fail to match trending (not just the first one).

        This test calls the REAL run_cycle() to verify skipping behavior.
        """
        from bot.pipeline import run_cycle

        # Setup: mock history (no prior posts)
        mock_load_hist.return_value = []
        mock_prune_hist.return_value = []
        mock_is_dup.return_value = False

        # Setup: mock articles that won't match any trending
        articles = [
            make_article("Story about politics", "Reuters", 0.3),
            make_article("Story about weather", "BBC", 0.4),
            make_article("Story about sports", "AP", 0.2),
        ]
        mock_fetch_feeds.return_value = MagicMock(entries=[])
        mock_parse_entries.return_value = articles

        # Setup: mock trending data that doesn't match any candidate
        trending_tokens = [
            {"quantum", "comput", "physic"},  # Matches none of our candidates
        ]
        mock_trending.return_value = trending_tokens

        # Mock the client
        mock_client = MagicMock()

        # Call the real pipeline
        run_cycle(mock_client, dry_run=False)

        # Verify: post_story was NOT called (all candidates failed, cycle skipped)
        assert not mock_post_story.called, "post_story should not be called when no candidates match trending"

    @patch("bot.pipeline.history.save_history")
    @patch("bot.pipeline.history.prune_history")
    @patch("bot.pipeline.history.load_history")
    @patch("bot.pipeline.history.is_duplicate")
    @patch("bot.poster.post_story")
    @patch("bot.pipeline.trends.fetch_trending_tokens")
    @patch("bot.pipeline.fetcher.parse_entries")
    @patch("bot.pipeline.fetcher.fetch_active_feeds")
    def test_select_first_matching_candidate(
        self, mock_fetch_feeds, mock_parse_entries, mock_trending,
        mock_post_story, mock_is_dup, mock_load_hist, mock_prune_hist, mock_save_hist
    ):
        """
        Regression test: select the FIRST candidate that matches trending,
        and stop looking (first match wins, don't test lower-ranked).

        This test calls the REAL run_cycle() to verify early termination.
        """
        from bot.pipeline import run_cycle

        # Setup: mock history (no prior posts)
        mock_load_hist.return_value = []
        mock_prune_hist.return_value = []
        mock_is_dup.return_value = False

        # Setup: mock articles where both #1 and #2 match trending
        articles = [
            make_article("Breaking news about artificial intelligence", "Reuters", 0.2),  # #1: will match, freshest
            make_article("More artificial intelligence news today", "BBC", 0.3),          # #2: would also match
        ]
        mock_fetch_feeds.return_value = MagicMock(entries=[])
        mock_parse_entries.return_value = articles

        # Setup: mock trending data
        trending_tokens = [
            {"artif", "intel"},  # Matches both candidates
        ]
        mock_trending.return_value = trending_tokens

        # Setup: mock post_story to return a tweet ID
        mock_post_story.return_value = "tweet_id_123"

        # Mock the client
        mock_client = MagicMock()

        # Call the real pipeline
        run_cycle(mock_client, dry_run=False)

        # Verify: post_story was called exactly once (not multiple times)
        assert mock_post_story.call_count == 1, \
            f"post_story should be called exactly once, was called {mock_post_story.call_count} times"
        # Verify: the selected tweet should be from the first article
        posted_tweet = mock_post_story.call_args[0][1]
        assert "Breaking" in posted_tweet, \
            f"Expected 'Breaking' from first article in tweet, got: {posted_tweet}"
