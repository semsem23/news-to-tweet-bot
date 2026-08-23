"""One full cycle: fetch -> rank -> dedupe -> compose -> post -> record."""

from __future__ import annotations

from datetime import datetime, timezone

import tweepy

from . import fetcher, history, ranker, rephraser
from .config import (
    DEDUP_LOOKBACK_HOURS,
    INCLUDE_LINK,
    POST_MIN_INTERVAL_MINUTES,
    RESOLVE_REAL_ARTICLE_URL,
    TOP_N,
    TWEET_MAX_CHARS,
    log,
)
from .models import PostedEntry
from datetime import timedelta


def run_cycle(client: tweepy.Client, dry_run: bool = False) -> None:
    log.info("=== Cycle start ===")

    now = datetime.now(timezone.utc)

    # Check 90-minute posting interval gate (skip if less than POST_MIN_INTERVAL_MINUTES since last post)
    if not dry_run:
        posted = history.load_history()
        posted = history.prune_history(posted, now)
        if posted:
            last_post = max(posted, key=lambda e: e.posted_at)
            last_posted_time = datetime.fromisoformat(last_post.posted_at)
            time_since_last = now - last_posted_time
            min_interval = timedelta(minutes=POST_MIN_INTERVAL_MINUTES)
            if time_since_last < min_interval:
                remaining = min_interval - time_since_last
                log.info(
                    "Interval gate: last post was %.1f min ago; "
                    "need %.1f more min before next post. Skipping this cycle.",
                    time_since_last.total_seconds() / 60,
                    remaining.total_seconds() / 60,
                )
                return

    # Fetch all feeds
    try:
        feeds_dict = fetcher.fetch_all_feeds()
    except RuntimeError as exc:
        log.error("All feeds failed to fetch: %s. Skipping this cycle.", exc)
        return

    # Parse entries from each feed, tagged with feed name
    articles_by_feed: dict[str, list] = {}
    for feed_name, parsed_feed in feeds_dict.items():
        articles = fetcher.parse_entries(parsed_feed, feed_name=feed_name)
        articles_by_feed[feed_name] = articles

    # Merge and deduplicate by link
    all_articles = fetcher.merge_and_dedup_articles({
        feed_name: articles for feed_name, articles in articles_by_feed.items()
    })

    if not all_articles:
        log.warning("No articles returned from feeds this cycle; skipping.")
        return

    # Apply headline filters
    filtered_articles = []
    filtered_count = 0
    for article in all_articles:
        is_disq, reason = fetcher.is_disqualified(article.title, article.source)
        if is_disq:
            log.debug("Filtered %r: %s", article.title, reason)
            filtered_count += 1
        else:
            filtered_articles.append(article)

    log.info(
        "Filtered %d articles (disqualified). %d articles remain for ranking.",
        filtered_count, len(filtered_articles),
    )

    if not filtered_articles:
        log.warning("All articles were filtered out; nothing to rank.")
        return

    articles = [a.to_dict() for a in filtered_articles]

    ranked = ranker.rank_articles(articles, top_n=TOP_N)
    if not ranked:
        log.warning("Ranking produced no candidates this cycle; skipping.")
        return

    # Log top 5 candidates with momentum breakdown for verification
    for s in ranked[:5]:
        log.info("cand score=%.4f age=%.2fh mom=%.3f | %s",
                 s.score, s.age_hours,
                 s.score_breakdown.get("momentum", 0.0), s.title[:70])

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
