"""Tests for time-based feed switching configuration."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.config import (
    FEED_URL_USA,
    FEED_URL_WORLD,
    FEED_SWITCH_HOUR_USA_START,
    FEED_SWITCH_HOUR_WORLD_START,
    FEED_WINDOW_OVERLAP_MINUTES,
    PARIS_TZ,
    get_active_feed_urls,
)


class TestFeedSelection:
    """Test time-based feed URL selection logic."""

    def test_daytime_world_edition(self):
        """During WORLD hours (07:00-18:00), return only WORLD feed."""
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=PARIS_TZ)  # noon
        urls = get_active_feed_urls(dt)
        assert urls == [FEED_URL_WORLD]

    def test_nighttime_usa_edition(self):
        """During USA hours (18:00-07:00), return only USA feed."""
        dt = datetime(2024, 1, 15, 22, 0, 0, tzinfo=PARIS_TZ)  # 10 PM
        urls = get_active_feed_urls(dt)
        assert urls == [FEED_URL_USA]

    def test_late_night_usa_edition(self):
        """In the early morning (before 07:00), return USA feed."""
        dt = datetime(2024, 1, 15, 3, 0, 0, tzinfo=PARIS_TZ)  # 3 AM
        urls = get_active_feed_urls(dt)
        assert urls == [FEED_URL_USA]

    def test_world_boundary_start_exact(self):
        """At exactly 07:00 (WORLD start), return both feeds (overlap window)."""
        dt = datetime(2024, 1, 15, 7, 0, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert len(urls) == 2
        assert FEED_URL_WORLD in urls
        assert FEED_URL_USA in urls

    def test_usa_boundary_start_exact(self):
        """At exactly 18:00 (USA start), return both feeds (overlap window)."""
        dt = datetime(2024, 1, 15, 18, 0, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert len(urls) == 2
        assert FEED_URL_WORLD in urls
        assert FEED_URL_USA in urls

    def test_world_boundary_early_overlap(self):
        """Before 07:00 within overlap window, return both feeds."""
        # 30 minutes before 07:00
        dt = datetime(2024, 1, 15, 6, 30, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert len(urls) == 2
        assert FEED_URL_WORLD in urls
        assert FEED_URL_USA in urls

    def test_world_boundary_late_overlap(self):
        """After 07:00 within overlap window, return both feeds."""
        # 30 minutes after 07:00
        dt = datetime(2024, 1, 15, 7, 30, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert len(urls) == 2
        assert FEED_URL_WORLD in urls
        assert FEED_URL_USA in urls

    def test_usa_boundary_early_overlap(self):
        """Before 18:00 within overlap window, return both feeds."""
        # 30 minutes before 18:00
        dt = datetime(2024, 1, 15, 17, 30, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert len(urls) == 2
        assert FEED_URL_WORLD in urls
        assert FEED_URL_USA in urls

    def test_usa_boundary_late_overlap(self):
        """After 18:00 within overlap window, return both feeds."""
        # 30 minutes after 18:00
        dt = datetime(2024, 1, 15, 18, 30, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert len(urls) == 2
        assert FEED_URL_WORLD in urls
        assert FEED_URL_USA in urls

    def test_world_boundary_before_overlap_window(self):
        """Just before 07:00 overlap window (outside), return USA feed."""
        # 61 minutes before 07:00 (outside overlap window which is 60 minutes)
        dt = datetime(2024, 1, 15, 5, 59, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert urls == [FEED_URL_USA]

    def test_world_boundary_after_overlap_window(self):
        """Just after 07:00 overlap window (outside), return WORLD feed."""
        # 61 minutes after 07:00
        dt = datetime(2024, 1, 15, 8, 1, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert urls == [FEED_URL_WORLD]

    def test_usa_boundary_before_overlap_window(self):
        """Just before 18:00 overlap window (outside), return WORLD feed."""
        # 61 minutes before 18:00
        dt = datetime(2024, 1, 15, 16, 59, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert urls == [FEED_URL_WORLD]

    def test_usa_boundary_after_overlap_window(self):
        """Just after 18:00 overlap window (outside), return USA feed."""
        # 61 minutes after 18:00
        dt = datetime(2024, 1, 15, 19, 1, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert urls == [FEED_URL_USA]

    def test_overlap_window_edge_case_minus_60(self):
        """Exactly 60 minutes before boundary, should be in overlap."""
        # Exactly 60 minutes before 07:00
        dt = datetime(2024, 1, 15, 6, 0, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert len(urls) == 2

    def test_overlap_window_edge_case_plus_60(self):
        """Exactly 60 minutes after boundary, should be in overlap."""
        # Exactly 60 minutes after 18:00
        dt = datetime(2024, 1, 15, 19, 0, 0, tzinfo=PARIS_TZ)
        urls = get_active_feed_urls(dt)
        assert len(urls) == 2

    def test_config_values_sensible(self):
        """Verify config constants are set as expected."""
        assert FEED_SWITCH_HOUR_WORLD_START == 7
        assert FEED_SWITCH_HOUR_USA_START == 18
        assert FEED_WINDOW_OVERLAP_MINUTES == 60
        assert FEED_URL_WORLD is not None
        assert FEED_URL_USA is not None
        assert FEED_URL_WORLD != FEED_URL_USA

    def test_none_uses_current_time(self):
        """When dt_paris is None, use current time (just verify it doesn't crash)."""
        urls = get_active_feed_urls(None)
        assert len(urls) >= 1
        assert all(isinstance(url, str) for url in urls)
