"""Fetching and parsing the Google News RSS feed.

Timezone handling: Google News RSS timestamps are always RFC 822 / GMT
(=UTC). We parse them as timezone-aware UTC datetimes, then convert to
Europe/Paris using the IANA tz database via `zoneinfo`, so CET/CEST and
the exact DST transition dates are handled automatically.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import feedparser
import requests

from .config import (
    FEED_URL,
    PARIS_TZ,
    REQUEST_TIMEOUT,
    RESOLVE_REAL_ARTICLE_URL,
    URL_RESOLVE_TIMEOUT,
    USER_AGENT,
    get_active_feed_urls,
    log,
)
from .models import Article

UTC = timezone.utc


def fetch_raw_feed(url: str = FEED_URL) -> feedparser.FeedParserDict:
    """Download and parse the RSS feed. Raises RuntimeError on failure."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to fetch RSS feed from {url}: {exc}") from exc

    parsed = feedparser.parse(resp.content)

    if parsed.bozo and not parsed.entries:
        # bozo=True just means "not strictly well-formed XML"; Google News
        # feeds sometimes trip this flag even when entries parse fine, so
        # only treat it as fatal if we got zero usable entries.
        raise RuntimeError(f"Could not parse RSS feed: {parsed.bozo_exception}")

    return parsed


def fetch_active_feeds(dt_paris: datetime | None = None) -> feedparser.FeedParserDict:
    """
    Fetch feed(s) based on Paris time and env override.

    If FEED_URL env var is set, fetch only that (for testing/CI).
    Otherwise, use get_active_feed_urls() to determine which feeds to fetch
    (normally 1, sometimes 2 during overlap windows).

    Args:
        dt_paris: datetime in Paris timezone. If None, uses current time.

    Returns a merged feedparser dict with entries from all fetched feeds.
    Raises RuntimeError if all feeds fail.
    """
    # Check for env override first
    if FEED_URL:
        return fetch_raw_feed(FEED_URL)

    urls = get_active_feed_urls(dt_paris)
    log.info("Active feed URLs for this cycle: %s", urls)

    if not urls:
        raise RuntimeError("No feed URLs configured")

    # Fetch all active feeds and merge entries
    all_entries = []
    last_error = None

    for url in urls:
        try:
            parsed = fetch_raw_feed(url)
            all_entries.extend(parsed.entries)
            log.info("Fetched %d entries from %s", len(parsed.entries), url)
        except RuntimeError as exc:
            log.warning("Failed to fetch %s: %s", url, exc)
            last_error = exc

    if not all_entries:
        if last_error:
            raise RuntimeError(
                f"All {len(urls)} feed(s) failed. Last error: {last_error}"
            ) from last_error
        raise RuntimeError("No entries fetched from any feed")

    # Return a fake feedparser dict with merged entries
    merged = feedparser.FeedParserDict()
    merged.entries = all_entries
    return merged


def split_title_and_source(raw_title: str, fallback_source: str = "") -> tuple[str, str]:
    """
    Google News titles are usually formatted "Headline text - Source Name".
    If we already know the source (from the <source> tag), strip that exact
    suffix. Otherwise fall back to splitting on the last " - " occurrence,
    guarded so headlines that legitimately contain a dash aren't mangled.
    """
    if fallback_source and raw_title.endswith(f" - {fallback_source}"):
        return raw_title[: -(len(fallback_source) + 3)].strip(), fallback_source

    if " - " in raw_title:
        head, _, tail = raw_title.rpartition(" - ")
        if 0 < len(tail) <= 40 and not re.search(r"[.?!]", tail):
            return head.strip(), tail.strip()

    return raw_title.strip(), fallback_source or "Unknown"


def to_paris_iso(struct_time_utc: time.struct_time) -> tuple[str, str]:
    """Convert a feedparser UTC struct_time into (utc_iso, paris_iso) strings."""
    dt_utc = datetime(*struct_time_utc[:6], tzinfo=UTC)
    dt_paris = dt_utc.astimezone(PARIS_TZ)
    return dt_utc.isoformat(), dt_paris.isoformat()


def parse_entries(parsed: feedparser.FeedParserDict) -> list[Article]:
    articles: list[Article] = []

    for entry in parsed.entries:
        raw_title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()

        source_tag = ""
        if hasattr(entry, "source") and getattr(entry.source, "title", None):
            source_tag = entry.source.title.strip()

        clean_title, source = split_title_and_source(raw_title, source_tag)

        if getattr(entry, "published_parsed", None):
            published_utc, published_paris = to_paris_iso(entry.published_parsed)
        else:
            now_utc = datetime.now(tz=UTC)
            published_utc = now_utc.isoformat()
            published_paris = now_utc.astimezone(PARIS_TZ).isoformat()

        articles.append(
            Article(
                title=clean_title,
                raw_title=raw_title,
                source=source,
                link=link,
                published_utc=published_utc,
                published_paris=published_paris,
            )
        )

    return articles


def resolve_article_url(link: str, timeout: float = URL_RESOLVE_TIMEOUT) -> str:
    """
    Google News RSS links are redirect wrappers, not the real article URL.
    Only used when INCLUDE_LINK is enabled. Tries the maintained
    `googlenewsdecoder` library first (handles Google's current
    opaque-token format), then a plain redirect-follow (with a Google
    CONSENT cookie to dodge the EU cookie interstitial). Never raises —
    falls back to the original link if both methods fail.
    """
    try:
        from googlenewsdecoder import gnewsdecoder

        result = gnewsdecoder(link, interval=1)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
        log.warning(
            "googlenewsdecoder could not resolve %s (%s); trying redirect-follow instead.",
            link, result.get("message"),
        )
    except ImportError:
        log.warning(
            "googlenewsdecoder not installed (pip install googlenewsdecoder for "
            "better link-resolution odds); trying redirect-follow instead."
        )
    except Exception as exc:  # noqa: BLE001 — third-party lib, contain everything
        log.warning("googlenewsdecoder raised an unexpected error (%s); trying redirect-follow instead.", exc)

    try:
        resp = requests.get(
            link,
            allow_redirects=True,
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Cookie": "CONSENT=YES+cb.20240101-17-p0.en+FX+000",
            },
            stream=True,
        )
        resolved = resp.url
        resp.close()
        if resolved and "google.com" not in resolved:
            return resolved
        log.warning(
            "Could not resolve real article URL for %s (still on a google.com "
            "page after following redirects); using Google News link as-is.",
            link,
        )
    except requests.RequestException as exc:
        log.warning("Could not resolve real article URL for %s (%s); using Google News link as-is.", link, exc)
    return link


__all__ = [
    "fetch_raw_feed",
    "fetch_active_feeds",
    "parse_entries",
    "split_title_and_source",
    "to_paris_iso",
    "resolve_article_url",
    "RESOLVE_REAL_ARTICLE_URL",
]
