"""Tests for cross-source count + spike-detection momentum tracking."""

from datetime import datetime, timedelta, timezone

from bot import momentum
from bot.config import (
    MOMENTUM_MIN_DISTINCT_SOURCES_FOR_SPIKE,
    MOMENTUM_SOURCES_FOR_FULL_BREADTH,
    MOMENTUM_ZSCORE_CUTOFF,
    TREND_LEAD_MOMENTUM_FLOOR,
)
from bot.models import RankedStory

BASE = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def story(title: str, sources: list[str]) -> RankedStory:
    return RankedStory(
        title=title,
        source=sources[0],
        link="https://x",
        published_paris="2026-09-21T14:00:00+02:00",
        cluster_size=len(sources),
        cluster_sources=sources,
        cluster_headlines=[{"title": title, "source": s} for s in sources],
    )


def run_cycle(title: str, sources: list[str], at: datetime) -> RankedStory:
    """Simulate one pipeline cycle's worth of momentum tracking for one story."""
    s = story(title, sources)
    momentum.annotate_momentum([s], now=at)
    return s


class TestFirstSighting:
    def test_new_signature_has_no_spike_and_partial_breadth(self):
        s = run_cycle("Central bank raises interest rates", ["Reuters", "BBC"], BASE)
        assert s.score_breakdown["cross_source_count"] == 2
        assert s.score_breakdown["momentum_spike"] is False
        expected_breadth = min(2 / MOMENTUM_SOURCES_FOR_FULL_BREADTH, 1.0)
        assert s.score_breakdown["momentum"] == round(0.5 * expected_breadth, 3)

    def test_full_breadth_alone_clears_the_lead_floor(self):
        sources = [f"Source{i}" for i in range(MOMENTUM_SOURCES_FOR_FULL_BREADTH)]
        s = run_cycle("Major earthquake strikes off the coast", sources, BASE)
        assert s.score_breakdown["momentum"] >= TREND_LEAD_MOMENTUM_FLOOR


class TestCrossSourceAccumulation:
    def test_distinct_sources_accumulate_across_hourly_cycles(self):
        title = "Tariff negotiations continue between the two countries"
        run_cycle(title, ["Reuters"], BASE)
        run_cycle(title, ["BBC"], BASE + timedelta(hours=1))
        s = run_cycle(title, ["AP"], BASE + timedelta(hours=2))

        assert s.score_breakdown["cross_source_count"] == 3

    def test_sources_outside_the_window_are_not_counted(self):
        from bot.config import MOMENTUM_CROSS_SOURCE_WINDOW_HOURS

        title = "Regional election results confirm ruling party majority"
        run_cycle(title, ["Reuters"], BASE)
        later = BASE + timedelta(hours=MOMENTUM_CROSS_SOURCE_WINDOW_HOURS + 1)
        s = run_cycle(title, ["BBC"], later)

        # The Reuters mention is now outside the cross-source window.
        assert s.score_breakdown["cross_source_count"] == 1

    def test_unrelated_stories_are_tracked_separately(self):
        run_cycle("Central bank holds interest rates steady", ["Reuters"], BASE)
        s = run_cycle(
            "Major earthquake strikes off the coast of Japan", ["BBC"], BASE + timedelta(minutes=30)
        )
        assert s.score_breakdown["cross_source_count"] == 1


class TestSpikeDetection:
    def test_sudden_jump_in_distinct_sources_is_flagged_as_a_spike(self):
        title = "Wildfire spreads rapidly through the region"
        # A quiet, steady history: one source per hour for several hours.
        for i in range(6):
            run_cycle(title, [f"SteadySource{i}"], BASE + timedelta(hours=i))

        # A sudden burst of many distinct sources in the next hour.
        burst_sources = [f"BurstSource{i}" for i in range(MOMENTUM_MIN_DISTINCT_SOURCES_FOR_SPIKE + 2)]
        s = run_cycle(title, burst_sources, BASE + timedelta(hours=6))

        assert s.score_breakdown["momentum_spike"] is True
        assert s.score_breakdown["momentum_zscore"] >= MOMENTUM_ZSCORE_CUTOFF
        assert s.score_breakdown["momentum"] >= TREND_LEAD_MOMENTUM_FLOOR

    def test_flat_history_never_spikes(self):
        title = "Weekly jobs report released"
        s = None
        for i in range(8):
            s = run_cycle(title, ["Reuters", "BBC"], BASE + timedelta(hours=i))

        assert s.score_breakdown["momentum_spike"] is False


class TestRetention:
    def test_stale_buckets_are_pruned_from_the_store(self):
        from bot.config import MOMENTUM_HISTORY_RETENTION_HOURS

        title = "Old story nobody is covering anymore"
        run_cycle(title, ["Reuters"], BASE)

        much_later = BASE + timedelta(hours=MOMENTUM_HISTORY_RETENTION_HOURS + 5)
        # A cycle at a much later time with an unrelated story should prune
        # the stale entry out of the store entirely.
        run_cycle("Completely different unrelated headline today", ["AP"], much_later)

        entries = momentum.load_trend_history()
        signatures = [set(e["signature"]) for e in entries]
        from bot.ranker import tokenize

        assert tokenize(title) not in signatures


class TestNeverRaises:
    def test_corrupt_json_degrades_to_an_empty_store_rather_than_raising(self):
        momentum.TREND_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        momentum.TREND_HISTORY_PATH.write_text("not valid json", encoding="utf-8")

        s = story("Some headline", ["Reuters"])
        result = momentum.annotate_momentum([s], now=BASE)

        assert "momentum" in result[0].score_breakdown

    def test_malformed_entry_falls_back_to_zero_momentum(self):
        import json

        momentum.TREND_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # An entry missing "signature" trips an exception mid-cycle; the
        # whole cycle must still degrade gracefully rather than fail to post.
        momentum.TREND_HISTORY_PATH.write_text(
            json.dumps([{"buckets": {"2026-09-21T12:00": ["Reuters"]}}]),
            encoding="utf-8",
        )

        s = story("Some headline", ["Reuters"])
        result = momentum.annotate_momentum([s], now=BASE)

        assert result[0].score_breakdown["momentum"] == 0.0
