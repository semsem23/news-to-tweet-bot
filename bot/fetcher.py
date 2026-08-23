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
    EXCLUDE_HOROSCOPE,
    EXCLUDE_QUESTION_HEADLINES,
    FEEDS,
    MIN_TWEET_CHARS,
    PARIS_TZ,
    QUESTION_START_WORDS,
    REQUEST_TIMEOUT,
    RESOLVE_REAL_ARTICLE_URL,
    URL_RESOLVE_TIMEOUT,
    USER_AGENT,
    log,
)
from .models import Article

UTC = timezone.utc


def fetch_raw_feed(url: str) -> feedparser.FeedParserDict:
    """Download and parse a single RSS feed. Raises RuntimeError on failure."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to fetch RSS feed from {url}: {exc}") from exc

    parsed = feedparser.parse(resp.content)

    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"Could not parse RSS feed: {parsed.bozo_exception}")

    return parsed


def fetch_all_feeds(feeds: dict[str, str] = FEEDS) -> dict[str, feedparser.FeedParserDict]:
    """
    Fetch all feeds. On per-feed failure, log and continue with remaining feeds.
    Returns a dict mapping feed name to parsed feed. If all feeds fail, raises RuntimeError.
    """
    results = {}
    for feed_name, feed_url in feeds.items():
        try:
            results[feed_name] = fetch_raw_feed(feed_url)
            log.info("Fetched %s feed (%d entries)", feed_name, len(results[feed_name].entries))
        except RuntimeError as exc:
            log.warning("Failed to fetch %s feed: %s. Continuing with other feeds.", feed_name, exc)

    if not results:
        raise RuntimeError("All feeds failed to fetch")

    return results


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


def parse_entries(parsed: feedparser.FeedParserDict, feed_name: str = "") -> list[Article]:
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
                feed=feed_name,
                feeds=[feed_name] if feed_name else [],
            )
        )

    return articles


def merge_and_dedup_articles(articles_by_feed: dict[str, list[Article]]) -> list[Article]:
    """
    Merge articles from all feeds, deduplicating by link.
    Preserves the set of feeds each article appeared in.
    """
    by_link: dict[str, Article] = {}

    for feed_name, articles in articles_by_feed.items():
        for article in articles:
            if article.link in by_link:
                existing = by_link[article.link]
                if feed_name not in existing.feeds:
                    existing.feeds.append(feed_name)
            else:
                article.feeds = [feed_name] if feed_name else []
                by_link[article.link] = article

    result = list(by_link.values())
    log.info("Merged %d articles from all feeds (after dedup by link)", len(result))
    return result


def compose_tweet_text(title: str, source: str) -> str:
    """Build the composed tweet text (headline + source attribution)."""
    return f"{title} ({source})"


def is_disqualified(title: str, source: str) -> tuple[bool, str]:
    """
    Check if an article should be excluded from ranking.
    Returns (is_disqualified, reason).
    """
    composed = compose_tweet_text(title, source)

    # Horoscope filter
    if EXCLUDE_HOROSCOPE:
        clean_title = title.strip().lstrip('"\'').lower()
        if clean_title.startswith("horoscope"):
            return True, "horoscope"

    # Question / interrogative filter
    if EXCLUDE_QUESTION_HEADLINES:
        clean_title = title.strip().lstrip('"\'').lower()
        if clean_title.endswith("?"):
            return True, "ends with question mark"
        first_word = re.split(r"\s+", clean_title)[0].rstrip(".,!?;:") if clean_title else ""
        if first_word in QUESTION_START_WORDS:
            return True, f"starts with question word: {first_word}"

    # Minimum length filter
    if len(composed) < MIN_TWEET_CHARS:
        return True, f"too short ({len(composed)} chars < {MIN_TWEET_CHARS} minimum)"

    return False, ""


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
    "fetch_all_feeds",
    "parse_entries",
    "merge_and_dedup_articles",
    "split_title_and_source",
    "to_paris_iso",
    "resolve_article_url",
    "compose_tweet_text",
    "is_disqualified",
    "RESOLVE_REAL_ARTICLE_URL",
]
