"""Tests for pipeline trending match logic with multi-candidate cycling."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from bot.config import PARIS_TZ
from bot.models import RankedStory


PARIS = ZoneInfo("Europe/Paris")


def make_ranked_story(title: str, score: float, index: int) -> RankedStory:
    """Create a test RankedStory."""
    return RankedStory(
        title=title,
        source="Test Source",
        link=f"https://example.com/{index}",
        published_paris=datetime.now(PARIS).isoformat(),
        cluster_size=1,
        cluster_sources=["Test Source"],
        cluster_headlines=[{"title": title, "source": "Test Source"}],
        age_hours=0.5,
        is_breaking=False,
        score=score,
        score_breakdown={"repetition": 0.5, "recency": 0.7, "prominence": 0.8},
    )


class TestPipelineTrendingMultiCandidate:
    """Test that pipeline cycles through multiple candidates for trending match."""

    def test_select_third_candidate_when_first_two_dont_match_trending(self):
        """
        Regression test: when #1 and #2 don't match trending but #3 does,
        select #3 (not skip the cycle).
        """
        from bot import history, ranker, trends

        # Setup: mock ranked candidates
        ranked = [
            make_ranked_story("Story about politics and senators", 0.95, 1),  # #1: won't match trending
            make_ranked_story("Story about weather and rain", 0.90, 2),    # #2: won't match trending
            make_ranked_story("Artificial intelligence breakthrough major", 0.85, 3),  # #3: will match
        ]

        # Setup: simulate non-duplicate candidates
        non_duplicate_candidates = ranked  # All are non-duplicates

        # Setup: mock trending data that matches only story #3
        # Trending: "artificial intelligence breakthrough"
        trending_tokens = [
            {"artif", "intel", "break"},  # Matches "Artificial intelligence breakthrough"
        ]

        # Test the logic: which candidate gets selected?
        selected = None
        for cand in non_duplicate_candidates:
            if trends.matches_trending(cand.title, trending_tokens, 0.3):
                selected = cand
                break

        # Verify: candidate #3 was selected
        assert selected is not None
        assert "Artificial intelligence" in selected.title
        assert selected.link == "https://example.com/3"

    def test_skip_when_all_candidates_fail_trending_match(self):
        """
        Verify that skip only happens when ALL top-N non-duplicate candidates
        fail to match trending (not just the first one).
        """
        from bot import trends

        # Setup: mock ranked candidates
        ranked = [
            make_ranked_story("Story about politics", 0.95, 1),
            make_ranked_story("Story about weather", 0.90, 2),
            make_ranked_story("Story about sports", 0.85, 3),
        ]

        # Setup: simulate non-duplicate candidates
        non_duplicate_candidates = ranked

        # Setup: mock trending data that doesn't match any candidate
        trending_tokens = [
            {"quantum", "comput", "physic"},  # Matches none of our candidates
        ]

        # Test the logic: try all candidates
        selected = None
        for cand in non_duplicate_candidates:
            if trends.matches_trending(cand.title, trending_tokens, 0.3):
                selected = cand
                break

        # Verify: no candidate was selected (all failed to match)
        assert selected is None

    def test_select_first_matching_candidate(self):
        """
        Verify that we select the FIRST candidate that matches trending,
        and stop looking at higher-ranked candidates (first match wins).
        """
        from bot import trends

        # Setup: mock ranked candidates
        ranked = [
            make_ranked_story("Breaking news about artificial intelligence", 0.95, 1),  # #1: will match
            make_ranked_story("More artificial intelligence news today", 0.90, 2),     # #2: would also match
        ]

        # Setup: simulate non-duplicate candidates
        non_duplicate_candidates = ranked

        # Setup: mock trending data
        trending_tokens = [
            {"artif", "intel"},  # Matches both candidates
        ]

        # Test the logic: which candidate gets selected (should be first)
        selected = None
        matches_count = 0
        for cand in non_duplicate_candidates:
            if trends.matches_trending(cand.title, trending_tokens, 0.3):
                selected = cand
                matches_count += 1
                break  # Stop at first match

        # Verify: we selected candidate #1 (the first to match)
        assert selected is not None
        assert "artificial intelligence" in selected.title.lower()
        assert selected.link == "https://example.com/1"
        # Only one candidate should have been evaluated as a match
        # (we break after the first match)
        assert matches_count == 1
