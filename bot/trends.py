"""Fetch and match against Google Trends data to gate posts on actual trending topics."""

from __future__ import annotations

import re

import feedparser
import requests

from .config import REQUEST_TIMEOUT, USER_AGENT, log
from .ranker import jaccard, tokenize


def fetch_trending_rss(geo: str) -> feedparser.FeedParserDict:
    """
    Fetch Google Trends RSS feed for a given geography.

    Args:
        geo: ISO 3166-1 alpha-2 country code (e.g., "US", "GB", "FR")

    Returns:
        Parsed feedparser dict. Raises RuntimeError on network failure or parse error.
    """
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to fetch Google Trends RSS for {geo}: {exc}") from exc

    parsed = feedparser.parse(resp.content)

    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"Could not parse Google Trends RSS for {geo}: {parsed.bozo_exception}")

    return parsed


def fetch_trending_tokens(geos: list[str]) -> list[set[str]]:
    """
    Fetch trending queries from Google Trends for multiple geographies.

    Each trending query's <title> is tokenized (via tokenize()) and returned as a
    set of tokens. Failures per geography are logged as warnings but don't block
    the whole fetch — we collect whatever succeeds.

    Args:
        geos: List of ISO 3166-1 alpha-2 country codes (e.g., ["US", "GB"]).

    Returns:
        List of token sets, one per successfully-fetched trending query across all
        geographies. Empty list if all fetches fail.
    """
    trending_sets: list[set[str]] = []

    for geo in geos:
        try:
            parsed = fetch_trending_rss(geo)
            for entry in parsed.entries:
                query_title = getattr(entry, "title", "").strip()
                if query_title:
                    tokens = tokenize(query_title)
                    if tokens:  # Only add non-empty token sets
                        trending_sets.append(tokens)
                        log.debug("Trending query [%s]: %s (tokens: %s)", geo, query_title, tokens)
        except RuntimeError as exc:
            log.warning("Could not fetch trending queries for %s: %s", geo, exc)

    return trending_sets


def matches_trending(
    title: str, trending_token_sets: list[set[str]], threshold: float = 0.3
) -> bool:
    """
    Check if a headline matches any trending topic.

    Tokenizes the headline and checks Jaccard similarity against each trending
    token set. Returns True if ANY trending set exceeds the threshold.

    Args:
        title: Headline to check.
        trending_token_sets: List of token sets from fetch_trending_tokens().
        threshold: Similarity threshold (0.0-1.0). Default 0.3.

    Returns:
        True if the headline matches at least one trending topic.
    """
    if not trending_token_sets:
        # If no trending data is available, don't gate anything
        log.warning("No trending data available; skipping trending filter")
        return True

    headline_tokens = tokenize(title)
    if not headline_tokens:
        # If headline tokenizes to nothing, it can't match trending
        return False

    for trending_set in trending_token_sets:
        similarity = jaccard(headline_tokens, trending_set)
        if similarity >= threshold:
            log.debug("Headline matches trending topic (similarity=%.3f): %s", similarity, title)
            return True

    return False


__all__ = [
    "fetch_trending_rss",
    "fetch_trending_tokens",
    "matches_trending",
]
