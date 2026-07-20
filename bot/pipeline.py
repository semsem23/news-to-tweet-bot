"""One full cycle: fetch -> rank -> dedupe -> compose -> post -> record."""

from __future__ import annotations

from datetime import datetime, timezone

import tweepy

from . import fetcher, history, ranker, rephraser, trends
from .config import (
    DEDUP_LOOKBACK_HOURS,
    INCLUDE_LINK,
    PARIS_TZ,
    RESOLVE_REAL_ARTICLE_URL,
    TOP_N,
    TRENDING_ENABLED,
    TRENDING_GEOS,
    TRENDING_MATCH_THRESHOLD,
    TWEET_MAX_CHARS,
    get_active_feed_urls,
    log,
)
from .models import PostedEntry


def _get_trending_geos_for_current_time(dt_paris: datetime | None = None) -> list[str]:
    """
    Determine which geo list to use for trending queries based on current Paris time.

    Uses the same feed-switching logic as get_active_feed_urls():
    - 07:00-18:00: WORLD edition → use TRENDING_GEOS["world"]
    - 18:00-07:00: USA edition → use TRENDING_GEOS["usa"]

    Args:
        dt_paris: Datetime in Paris timezone for testing. If None, uses current time.
    """
    urls = get_active_feed_urls(dt_paris)
    from .config import FEED_URL_WORLD

    if FEED_URL_WORLD in urls:
        return TRENDING_GEOS.get("world", [])
    else:
        return TRENDING_GEOS.get("usa", [])


def run_cycle(client: tweepy.Client, dry_run: bool = False, now_override: datetime | None = None) -> None:
    log.info("=== Cycle start ===")

    try:
        parsed_feed = fetcher.fetch_active_feeds()
    except RuntimeError as exc:
        log.error("Fetch failed: %s. Skipping this cycle.", exc)
        return

    articles = [a.to_dict() for a in fetcher.parse_entries(parsed_feed)]
    if not articles:
        log.warning("No articles returned from feed(s) this cycle; skipping.")
        return
    log.info("Fetched %d articles from active feed(s).", len(articles))

    ranked = ranker.rank_articles(articles, top_n=TOP_N)
    if not ranked:
        log.warning("Ranking produced no candidates this cycle; skipping.")
        return

    now = now_override or datetime.now(timezone.utc)
    dt_paris = now.astimezone(PARIS_TZ) if now_override else None
    posted = history.prune_history(history.load_history(), now)

    # Filter to non-duplicate candidates
    non_duplicate_candidates = []
    for cand in ranked:
        if not history.is_duplicate(cand, posted):
            non_duplicate_candidates.append(cand)

    if not non_duplicate_candidates:
        log.info(
            "All %d top-ranked candidates are duplicates of stories posted in "
            "the last %dh; nothing new to post this cycle.",
            len(ranked), DEDUP_LOOKBACK_HOURS,
        )
        return

    # Trending topic gating: cycle through candidates to find one matching actual trends
    candidate = None
    if TRENDING_ENABLED:
        geos = _get_trending_geos_for_current_time(dt_paris)
        if geos:
            try:
                trending_tokens = trends.fetch_trending_tokens(geos)
                log.info(
                    "Fetched %d trending topics from %s for trending filter.",
                    len(trending_tokens), geos,
                )
                if trending_tokens:
                    log.debug("Trending queries: %s",
                             [' '.join(ts) for ts in trending_tokens[:3]])  # Show first 3

                    # Try each non-duplicate candidate in rank order
                    for cand in non_duplicate_candidates:
                        log.debug("Testing candidate for trending match: '%s'", cand.title)
                        if trends.matches_trending(
                            cand.title, trending_tokens, TRENDING_MATCH_THRESHOLD
                        ):
                            candidate = cand
                            log.info("Candidate matches trending topic: '%s'", cand.title)
                            break

                    if candidate is None:
                        log.info(
                            "None of %d non-duplicate candidates match trending topics "
                            "(threshold=%.2f); skipping this cycle.",
                            len(non_duplicate_candidates), TRENDING_MATCH_THRESHOLD,
                        )
                        return
                else:
                    log.warning(
                        "No trending data available; skipping trending filter for this cycle."
                    )
                    candidate = non_duplicate_candidates[0]
            except Exception as exc:  # noqa: BLE001 — network errors, etc.
                log.warning(
                    "Trending fetch failed (%s); proceeding without trending filter. "
                    "Using first non-duplicate candidate.",
                    exc,
                )
                candidate = non_duplicate_candidates[0]
        else:
            log.warning("No geos configured for trending filter; using first non-duplicate.")
            candidate = non_duplicate_candidates[0]
    else:
        # Trending disabled: use first non-duplicate
        candidate = non_duplicate_candidates[0]

    if INCLUDE_LINK and RESOLVE_REAL_ARTICLE_URL:
        resolved_link = fetcher.resolve_article_url(candidate.link)
        if resolved_link != candidate.link:
            log.info("Resolved real article URL: %s", resolved_link)
            candidate.link = resolved_link

    candidate.tweet = rephraser.build_tweet(candidate)

    tweet_length = rephraser.twitter_weighted_length(candidate.tweet)
    if tweet_length > TWEET_MAX_CHARS:
        # Defensive check — build_tweet() already enforces the budget.
        # Uses X's actual counting rule (URLs weighted as flat 23 chars).
        log.error(
            "Generated tweet exceeds X's char budget as X counts it (%d > %d); "
            "skipping rather than posting malformed content: %r",
            tweet_length, TWEET_MAX_CHARS, candidate.tweet,
        )
        return

    log.info("Selected: %r (score=%s, age=%.2fh)", candidate.title, candidate.score, candidate.age_hours)
    log.info("Tweet (%d chars as X counts it): %s", tweet_length, candidate.tweet)

    if dry_run:
        log.info("[dry-run] Skipping actual post to X.")
        return

    from .poster import post_story  # local import keeps module deps one-way

    tweet_id = post_story(client, candidate.tweet)
    if tweet_id is None:
        log.warning("Post failed; not recording in history so it can be retried.")
        return

    posted.append(PostedEntry(link=candidate.link, title=candidate.title, posted_at=now.isoformat()))
    history.save_history(posted)
    log.info("=== Cycle complete ===")
