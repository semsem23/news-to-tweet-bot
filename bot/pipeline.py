"""One full cycle: fetch -> rank -> dedupe -> compose -> post -> record."""

from __future__ import annotations

from datetime import datetime, timezone

import tweepy

from . import fetcher, history, ranker, rephraser
from .config import (
    DEDUP_LOOKBACK_HOURS,
    INCLUDE_LINK,
    RESOLVE_REAL_ARTICLE_URL,
    TOP_N,
    TWEET_MAX_CHARS,
    log,
)
from .models import PostedEntry


def run_cycle(client: tweepy.Client, dry_run: bool = False) -> None:
    log.info("=== Cycle start ===")

    try:
        parsed_feed = fetcher.fetch_raw_feed()
    except RuntimeError as exc:
        log.error("Fetch failed: %s. Skipping this cycle.", exc)
        return

    articles = [a.to_dict() for a in fetcher.parse_entries(parsed_feed)]
    if not articles:
        log.warning("No articles returned from feed this cycle; skipping.")
        return
    log.info("Fetched %d articles.", len(articles))

    ranked = ranker.rank_articles(articles, top_n=TOP_N)
    if not ranked:
        log.warning("Ranking produced no candidates this cycle; skipping.")
        return

    now = datetime.now(timezone.utc)
    posted = history.prune_history(history.load_history(), now)

    candidate = history.pick_non_duplicate(ranked, posted)
    if candidate is None:
        log.info(
            "All %d top-ranked candidates are duplicates of stories posted in "
            "the last %dh; nothing new to post this cycle.",
            len(ranked), DEDUP_LOOKBACK_HOURS,
        )
        return

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
