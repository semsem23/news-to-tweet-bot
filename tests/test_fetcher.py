"""Tests for RSS feed fetching, including retry behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from bot.fetcher import fetch_raw_feed


def make_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b"<rss></rss>"
    if status_code >= 400:
        error = requests.HTTPError(f"{status_code} Error")
        error.response = resp
        resp.raise_for_status.side_effect = error
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestFetchRawFeedRetry:
    @patch("bot.fetcher.time.sleep")
    @patch("bot.fetcher.requests.get")
    def test_succeeds_on_second_attempt_after_503(self, mock_get, mock_sleep):
        """A transient 503 followed by a 200 should succeed without raising."""
        mock_get.side_effect = [make_response(503), make_response(200)]

        result = fetch_raw_feed("https://example.com/feed")

        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(5)
        assert result is not None

    @patch("bot.fetcher.time.sleep")
    @patch("bot.fetcher.requests.get")
    def test_gives_up_after_exhausting_retries_on_persistent_503(self, mock_get, mock_sleep):
        """A 503 on every attempt should exhaust retries and raise RuntimeError."""
        mock_get.side_effect = [make_response(503), make_response(503), make_response(503)]

        with pytest.raises(RuntimeError, match="Failed to fetch RSS feed"):
            fetch_raw_feed("https://example.com/feed")

        # Initial attempt + 2 retries = 3 total calls, 2 sleeps between them.
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("bot.fetcher.time.sleep")
    @patch("bot.fetcher.requests.get")
    def test_does_not_retry_on_4xx(self, mock_get, mock_sleep):
        """A 404 is a client error and should fail fast without retrying."""
        mock_get.return_value = make_response(404)

        with pytest.raises(RuntimeError, match="Failed to fetch RSS feed"):
            fetch_raw_feed("https://example.com/feed")

        assert mock_get.call_count == 1
        mock_sleep.assert_not_called()

    @patch("bot.fetcher.time.sleep")
    @patch("bot.fetcher.requests.get")
    def test_retries_on_network_error_then_succeeds(self, mock_get, mock_sleep):
        """A connection error followed by a 200 should succeed after one retry."""
        mock_get.side_effect = [requests.ConnectionError("boom"), make_response(200)]

        result = fetch_raw_feed("https://example.com/feed")

        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(5)
        assert result is not None
