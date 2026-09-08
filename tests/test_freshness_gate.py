"""Tests for momentum-aware freshness gate in enforce_top_story_freshness."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.config import (
    TREND_LEAD_MOMENTUM_FLOOR,
    TREND_MAX_LEAD_AGE_HOURS,
)
from bot.ranker import enforce_top_story_freshness
from bot.models import RankedStory

PARIS = ZoneInfo("Europe/Paris")


def make_story(
    title: str,
    age_hours: float,
    momentum: float = 0.0,
    score: float = 0.8,
) -> RankedStory:
    """Create a RankedStory with the given parameters for testing."""
    return RankedStory(
        title=title,
        source="Reuters",
        link=f"https://example.com/{title[:30]}",
        published_paris=(datetime.now(timezone.utc) - timedelta(hours=age_hours)).astimezone(PARIS).isoformat(),
        cluster_size=1,
        cluster_sources=["Reuters"],
        cluster_headlines=[{"title": title, "source": "Reuters"}],
        age_hours=age_hours,
        is_breaking=age_hours < 1.0,
        score=score,
        score_breakdown={
            "feed_position": 0.9,
            "repetition": 0.5,
            "recency": 0.7,
            "prominence": 0.8,
            "style_penalty": 1.0,
            "momentum": momentum,
        },
    )


class TestMomentumAwarenessFreshness:
    def test_high_momentum_older_story_leads_over_fresh_one_off(self):
        """
        A story with age 2.5h and momentum 0.4 (>= floor 0.35) should stay #1
        even though a 0.5h story sits below it (existing behavior broken).
        """
        trending_story = make_story(
            "Tariff negotiations continue to develop",
            age_hours=2.5,
            momentum=0.4,
            score=0.90,
        )
        fresh_one_off = make_story(
            "Forest fire in Indonesia",
            age_hours=0.5,
            momentum=0.05,
            score=0.85,
        )

        scored = [trending_story, fresh_one_off]
        result = enforce_top_story_freshness(scored)

        # Trending story should remain at #1 due to momentum override
        assert result[0] is trending_story
        assert result[0].title.startswith("Tariff")

    def test_low_momentum_older_story_is_demoted(self):
        """
        A story with age 2.5h and momentum 0.1 (< floor 0.35) should be demoted
        to make room for a fresh (<1h) story — existing behavior preserved.
        """
        old_low_momentum = make_story(
            "Old news from yesterday",
            age_hours=2.5,
            momentum=0.1,
            score=0.90,
        )
        fresh_story = make_story(
            "Breaking news now",
            age_hours=0.3,
            momentum=0.05,
            score=0.85,
        )

        scored = [old_low_momentum, fresh_story]
        result = enforce_top_story_freshness(scored)

        # Fresh story should be promoted to #1
        assert result[0] is fresh_story
        assert "Breaking" in result[0].title

    def test_very_old_high_momentum_story_is_demoted_by_staleness_gate(self):
        """
        A story with age 7.0h and momentum 0.9 does NOT lead (exceeds
        TREND_MAX_LEAD_AGE_HOURS of 6.0h). Widening fallback runs instead.
        """
        very_old_trending = make_story(
            "Ancient trending story",
            age_hours=7.0,
            momentum=0.9,
            score=0.95,
        )
        fairly_old_story = make_story(
            "Fairly old but reasonable",
            age_hours=3.5,
            momentum=0.3,
            score=0.80,
        )

        scored = [very_old_trending, fairly_old_story]
        result = enforce_top_story_freshness(scored)

        # Old trending should be demoted because it exceeds TREND_MAX_LEAD_AGE_HOURS
        # Widening fallback should find the fairly_old_story (< 6h window)
        assert result[0] is fairly_old_story

    def test_empty_scored_list_returns_empty(self):
        """Empty input should return empty output (unchanged behavior)."""
        result = enforce_top_story_freshness([])
        assert result == []

    def test_fresh_story_unaffected_by_momentum_override(self):
        """
        A story < 1h old should return early, unaffected by the momentum
        override logic (existing behavior preserved).
        """
        fresh_any_momentum = make_story(
            "Fresh news with any momentum",
            age_hours=0.3,
            momentum=0.5,
            score=0.80,
        )
        older_high_momentum = make_story(
            "Older but trending",
            age_hours=2.0,
            momentum=0.9,
            score=0.85,
        )

        scored = [fresh_any_momentum, older_high_momentum]
        result = enforce_top_story_freshness(scored)

        # Fresh story should stay at #1 (normal freshness gate)
        assert result[0] is fresh_any_momentum

    def test_momentum_boundary_just_below_floor(self):
        """
        A story with momentum = 0.34 (just below TREND_LEAD_MOMENTUM_FLOOR)
        should NOT get the override, even if in valid age range.
        """
        just_below_floor = make_story(
            "Just below threshold",
            age_hours=2.5,
            momentum=0.34,  # Just below 0.35
            score=0.90,
        )
        fresh_story = make_story(
            "Fresh alternative",
            age_hours=0.7,
            momentum=0.05,
            score=0.80,
        )

        scored = [just_below_floor, fresh_story]
        result = enforce_top_story_freshness(scored)

        # Just-below-floor should be demoted (widening fallback applies)
        assert result[0] is fresh_story

    def test_momentum_boundary_at_floor(self):
        """
        A story with momentum = 0.35 (exactly at TREND_LEAD_MOMENTUM_FLOOR)
        should get the override if age is valid.
        """
        at_floor = make_story(
            "Exactly at threshold",
            age_hours=2.5,
            momentum=0.35,  # Exactly 0.35
            score=0.90,
        )
        fresh_story = make_story(
            "Fresh alternative",
            age_hours=0.7,
            momentum=0.05,
            score=0.85,
        )

        scored = [at_floor, fresh_story]
        result = enforce_top_story_freshness(scored)

        # At-floor should stay at #1 (momentum override applies)
        assert result[0] is at_floor

    def test_age_boundary_just_below_6h_limit(self):
        """
        A story with age = 5.9h and momentum >= floor should get the override.
        """
        just_below_limit = make_story(
            "Just below age limit",
            age_hours=5.9,
            momentum=0.4,
            score=0.90,
        )
        fresh_story = make_story(
            "Fresh alternative",
            age_hours=0.5,
            momentum=0.05,
            score=0.85,
        )

        scored = [just_below_limit, fresh_story]
        result = enforce_top_story_freshness(scored)

        # Just-below-limit should stay at #1
        assert result[0] is just_below_limit

    def test_age_boundary_at_6h_limit(self):
        """
        A story with age = 6.0h (at TREND_MAX_LEAD_AGE_HOURS) should NOT
        get the override because the check is `< 6.0h`, not `<= 6.0h`.
        """
        at_limit = make_story(
            "At age limit",
            age_hours=6.0,
            momentum=0.4,
            score=0.90,
        )
        slightly_fresher = make_story(
            "Slightly fresher",
            age_hours=3.5,
            momentum=0.3,
            score=0.80,
        )

        scored = [at_limit, slightly_fresher]
        result = enforce_top_story_freshness(scored)

        # At-limit should be demoted (widening fallback applies)
        # The < 6h window should find the 3.5h story
        assert result[0] is slightly_fresher

    def test_missing_momentum_defaults_to_zero(self):
        """
        If momentum is missing from score_breakdown, it defaults to 0.0
        and the override is not applied.
        """
        no_momentum = make_story(
            "No momentum field",
            age_hours=2.5,
            score=0.90,
        )
        # Manually remove momentum from breakdown
        no_momentum.score_breakdown.pop("momentum", None)

        fresh_story = make_story(
            "Fresh story",
            age_hours=0.5,
            momentum=0.05,
            score=0.85,
        )

        scored = [no_momentum, fresh_story]
        result = enforce_top_story_freshness(scored)

        # Missing momentum should default to 0.0, so no override
        assert result[0] is fresh_story
