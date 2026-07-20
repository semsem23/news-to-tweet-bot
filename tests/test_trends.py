"""Tests for trending topic gating."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.trends import fetch_trending_tokens, matches_trending


class TestMatchesTrending:
    """Test headline-to-trending-topic matching logic."""

    def test_exact_match(self):
        """Exact match of all tokens should pass."""
        # Note: tokens are stemmed by tokenize(), so "election" becomes "elect"
        trending_sets = [{"trump", "elect"}]
        title = "Trump wins election"
        assert matches_trending(title, trending_sets, threshold=0.5)

    def test_partial_match_above_threshold(self):
        """Partial token overlap above threshold should pass."""
        # Trending set has stemmed tokens
        trending_sets = [{"trump", "elect", "vote"}]
        title = "Trump election results"  # tokenizes to {trump, elect, result}
        assert matches_trending(title, trending_sets, threshold=0.4)

    def test_partial_match_below_threshold(self):
        """Partial token overlap below threshold should fail."""
        trending_sets = [{"trump", "elect", "vote"}]
        title = "Trump election results"
        assert not matches_trending(title, trending_sets, threshold=0.7)

    def test_no_overlap(self):
        """Completely unrelated headline should fail."""
        trending_sets = [{"trump", "elect"}]
        title = "Weather forecast tomorrow"
        assert not matches_trending(title, trending_sets, threshold=0.3)

    def test_multiple_trending_sets_first_matches(self):
        """Match against first trending set should pass."""
        trending_sets = [
            {"trump", "elect"},
            {"weath", "forec"},
        ]
        title = "Trump wins election"
        assert matches_trending(title, trending_sets, threshold=0.5)

    def test_multiple_trending_sets_second_matches(self):
        """Match against second trending set should pass."""
        trending_sets = [
            {"trump", "elect"},
            {"weath", "forec"},
        ]
        title = "Weather forecast for tomorrow"
        assert matches_trending(title, trending_sets, threshold=0.5)

    def test_multiple_trending_sets_none_match(self):
        """No match against any trending set should fail."""
        trending_sets = [
            {"trump", "election"},
            {"weather", "forecast"},
        ]
        title = "Sports news today"
        assert not matches_trending(title, trending_sets, threshold=0.5)

    def test_empty_trending_sets_returns_true(self):
        """No trending data available should return True (don't gate)."""
        trending_sets = []
        title = "Any headline"
        assert matches_trending(title, trending_sets, threshold=0.3)

    def test_headline_with_no_tokens_returns_false(self):
        """Headline that tokenizes to nothing should fail."""
        trending_sets = [{"trump", "election"}]
        title = "a an the of"  # only stopwords
        assert not matches_trending(title, trending_sets, threshold=0.3)

    def test_case_insensitive_matching(self):
        """Matching should be case-insensitive."""
        trending_sets = [{"trump", "elect"}]
        title = "TRUMP WINS ELECTION"
        assert matches_trending(title, trending_sets, threshold=0.5)

    def test_stemming_matches_variants(self):
        """Stemming should match morphological variants."""
        trending_sets = [{"elect"}]  # stem of "election"
        title = "Trump election results"
        assert matches_trending(title, trending_sets, threshold=0.3)

    def test_threshold_boundary_at_0_5(self):
        """Test exact threshold boundary (similarity == 0.5)."""
        # title "abc def" -> tokens {abc, def}
        # trending set {abc, def, ghi, jkl} (4 items)
        # intersection {abc, def} = 2
        # union {abc, def, ghi, jkl} = 4
        # jaccard = 2/4 = 0.5
        trending_sets = [{"abc", "def", "ghi", "jkl"}]
        title = "abc def"
        assert matches_trending(title, trending_sets, threshold=0.5)

    def test_threshold_boundary_just_below(self):
        """Test just below threshold."""
        # title "abc def" -> tokens {abc, def}
        # trending set {abc, def, ghi, jkl, mno} (5 items)
        # intersection {abc, def} = 2
        # union {abc, def, ghi, jkl, mno} = 5
        # jaccard = 2/5 = 0.4 (below 0.5)
        trending_sets = [{"abc", "def", "ghi", "jkl", "mno"}]
        title = "abc def"
        assert not matches_trending(title, trending_sets, threshold=0.5)


class TestFetchTrendingTokens:
    """Test fetching and parsing trending topics from Google Trends."""

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_single_geo(self, mock_fetch_rss):
        """Fetch trending for a single geography."""
        mock_entry = MagicMock()
        mock_entry.title = "Trump election"
        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_fetch_rss.return_value = mock_feed

        result = fetch_trending_tokens(["US"])

        assert len(result) == 1
        # Tokenized title should include "trump" and "elect" (stem of "election")
        assert "trump" in result[0]
        mock_fetch_rss.assert_called_once_with("US")

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_multiple_geos(self, mock_fetch_rss):
        """Fetch trending for multiple geographies."""
        mock_entry_us = MagicMock()
        mock_entry_us.title = "Trump"
        mock_feed_us = MagicMock()
        mock_feed_us.entries = [mock_entry_us]

        mock_entry_gb = MagicMock()
        mock_entry_gb.title = "Brexit"
        mock_feed_gb = MagicMock()
        mock_feed_gb.entries = [mock_entry_gb]

        mock_fetch_rss.side_effect = [mock_feed_us, mock_feed_gb]

        result = fetch_trending_tokens(["US", "GB"])

        assert len(result) == 2
        assert mock_fetch_rss.call_count == 2

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_handles_empty_feed(self, mock_fetch_rss):
        """Empty feed should return empty token list."""
        mock_feed = MagicMock()
        mock_feed.entries = []
        mock_fetch_rss.return_value = mock_feed

        result = fetch_trending_tokens(["US"])

        assert len(result) == 0

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_handles_missing_title(self, mock_fetch_rss):
        """Entry without title should be skipped."""
        mock_entry = MagicMock(spec=[])  # No title attribute
        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_fetch_rss.return_value = mock_feed

        result = fetch_trending_tokens(["US"])

        assert len(result) == 0

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_handles_partial_failures(self, mock_fetch_rss):
        """If one geo fails, others should still succeed."""
        mock_feed_us = MagicMock()
        mock_entry_us = MagicMock()
        mock_entry_us.title = "Trump"
        mock_feed_us.entries = [mock_entry_us]

        def side_effect(geo):
            if geo == "US":
                return mock_feed_us
            else:
                raise RuntimeError("Network error")

        mock_fetch_rss.side_effect = side_effect

        result = fetch_trending_tokens(["US", "GB"])

        assert len(result) == 1  # Only US succeeded
        assert "trump" in result[0]

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_all_failures(self, mock_fetch_rss):
        """If all geos fail, return empty list."""
        mock_fetch_rss.side_effect = RuntimeError("Network error")

        result = fetch_trending_tokens(["US", "GB"])

        assert len(result) == 0

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_multiple_entries_per_geo(self, mock_fetch_rss):
        """Each entry in a feed is tokenized separately."""
        mock_entry1 = MagicMock()
        mock_entry1.title = "Trump election"
        mock_entry2 = MagicMock()
        mock_entry2.title = "Biden campaign"

        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry1, mock_entry2]
        mock_fetch_rss.return_value = mock_feed

        result = fetch_trending_tokens(["US"])

        assert len(result) == 2

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_whitespace_handling(self, mock_fetch_rss):
        """Whitespace in titles should be handled."""
        mock_entry = MagicMock()
        mock_entry.title = "  Trump   election  "
        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_fetch_rss.return_value = mock_feed

        result = fetch_trending_tokens(["US"])

        assert len(result) == 1
        assert "trump" in result[0]
        assert "elect" in result[0]

    @patch("bot.trends.fetch_trending_rss")
    def test_fetch_skips_empty_token_sets(self, mock_fetch_rss):
        """Entries that tokenize to nothing should be skipped."""
        mock_entry1 = MagicMock()
        mock_entry1.title = "a an the"  # Only stopwords
        mock_entry2 = MagicMock()
        mock_entry2.title = "Trump"

        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry1, mock_entry2]
        mock_fetch_rss.return_value = mock_feed

        result = fetch_trending_tokens(["US"])

        assert len(result) == 1  # Only the second entry


class TestIntegration:
    """Integration tests combining fetch and match."""

    @patch("bot.trends.fetch_trending_rss")
    def test_end_to_end_trending_match(self, mock_fetch_rss):
        """Full flow: fetch trends, then check if headline matches."""
        mock_entry = MagicMock()
        mock_entry.title = "Trump election 2024"
        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_fetch_rss.return_value = mock_feed

        trending = fetch_trending_tokens(["US"])
        assert len(trending) > 0

        title = "Donald Trump wins election"
        assert matches_trending(title, trending, threshold=0.3)

    @patch("bot.trends.fetch_trending_rss")
    def test_end_to_end_trending_no_match(self, mock_fetch_rss):
        """Full flow: fetch trends, headline doesn't match."""
        mock_entry = MagicMock()
        mock_entry.title = "Trump election"
        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_fetch_rss.return_value = mock_feed

        trending = fetch_trending_tokens(["US"])
        assert len(trending) > 0

        title = "Weather forecast for tomorrow"
        assert not matches_trending(title, trending, threshold=0.3)
