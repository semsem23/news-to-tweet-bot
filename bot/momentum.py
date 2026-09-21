"""Cross-source count and spike-detection momentum, persisted across cycles.

Feeds `RankedStory.score_breakdown["momentum"]` — the signal that
`ranker.enforce_top_story_freshness`'s momentum override reads to let a
sustained, multi-source trend lead the #1 slot even when older than the
plain freshness window. That override (and its config knobs, and its test
suite) existed before this module did, but nothing ever wrote "momentum"
into score_breakdown, so it always read 0.0 in production. This module is
the producer.

Backed by a JSON file (data/trend_history.json), mirroring history.py's
posted_history.json: committed back to the repo by the GitHub Actions
workflow so the time series survives across ephemeral runners.

Each tracked "signature" is the set of tokens across a story's cluster
headlines, matched cycle-to-cycle with the same stem+jaccard logic
ranker.cluster_articles uses — clusters are rebuilt from scratch every
cycle and have no stable ID, so this is how a persisting story is
recognized across runs. For each signature we keep one bucket per hour:
the set of distinct sources that mentioned it during that hour. From those
buckets each cycle derives:
  * cross-source count: distinct sources across the last N hours (union).
  * a z-score of the current hour's distinct-source count against the
    signature's own rolling average over a longer window, as a simple
    stand-in for spike detection.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import (
    MOMENTUM_CROSS_SOURCE_WINDOW_HOURS,
    MOMENTUM_HISTORY_RETENTION_HOURS,
    MOMENTUM_MIN_DISTINCT_SOURCES_FOR_SPIKE,
    MOMENTUM_SIGNATURE_SIMILARITY_THRESHOLD,
    MOMENTUM_SOURCE_WEIGHT,
    MOMENTUM_SOURCES_FOR_FULL_BREADTH,
    MOMENTUM_SPIKE_WEIGHT,
    MOMENTUM_ZSCORE_CUTOFF,
    MOMENTUM_ZSCORE_WINDOW_HOURS,
    TREND_HISTORY_PATH,
    log,
)
from .models import RankedStory

_HOUR_FORMAT = "%Y-%m-%dT%H:00"


def _hour_bucket(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_HOUR_FORMAT)


def _bucket_time(hour_iso: str) -> datetime:
    return datetime.strptime(hour_iso, _HOUR_FORMAT).replace(tzinfo=timezone.utc)


def load_trend_history() -> list[dict]:
    if not TREND_HISTORY_PATH.exists():
        return []
    try:
        with TREND_HISTORY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("trend_history.json is corrupt (%s); starting with an empty store.", exc)
        return []


def save_trend_history(entries: list[dict]) -> None:
    TREND_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TREND_HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _prune_buckets(buckets: dict[str, list[str]], now: datetime) -> dict[str, list[str]]:
    cutoff = now - timedelta(hours=MOMENTUM_HISTORY_RETENTION_HOURS)
    return {h: sources for h, sources in buckets.items() if _bucket_time(h) >= cutoff}


def _signature_tokens(story: RankedStory) -> set[str]:
    # Local import breaks the ranker <-> momentum cycle: ranker imports this
    # module at top level, so this module can't also import ranker at its
    # own top level.
    from .ranker import tokenize

    tokens: set[str] = set()
    for headline in story.cluster_headlines:
        tokens |= tokenize(headline["title"])
    return tokens


def _find_matching_entry(entries: list[dict], tokens: set[str]) -> Optional[dict]:
    from .ranker import jaccard

    best_entry = None
    best_sim = 0.0
    for entry in entries:
        sim = jaccard(tokens, set(entry["signature"]))
        if sim > best_sim:
            best_sim = sim
            best_entry = entry

    if best_entry is not None and best_sim >= MOMENTUM_SIGNATURE_SIMILARITY_THRESHOLD:
        return best_entry
    return None


def _cross_source_count(buckets: dict[str, list[str]], now: datetime) -> int:
    cutoff = now - timedelta(hours=MOMENTUM_CROSS_SOURCE_WINDOW_HOURS)
    sources: set[str] = set()
    for hour_iso, bucket_sources in buckets.items():
        if _bucket_time(hour_iso) >= cutoff:
            sources.update(bucket_sources)
    return len(sources)


def _history_counts(buckets: dict[str, list[str]], now: datetime, exclude_hour: str) -> list[int]:
    cutoff = now - timedelta(hours=MOMENTUM_ZSCORE_WINDOW_HOURS)
    counts = []
    for hour_iso, bucket_sources in buckets.items():
        if hour_iso == exclude_hour:
            continue
        if _bucket_time(hour_iso) >= cutoff:
            counts.append(len(bucket_sources))
    return counts


def _score_momentum(
    cross_source_count: int, current_hour_count: int, history_counts: list[int]
) -> tuple[float, float, bool]:
    source_breadth = min(cross_source_count / MOMENTUM_SOURCES_FOR_FULL_BREADTH, 1.0)

    zscore = 0.0
    is_spike = False
    if len(history_counts) >= 2:
        mean = statistics.mean(history_counts)
        stdev = statistics.pstdev(history_counts)
        if stdev > 0:
            zscore = (current_hour_count - mean) / stdev
        elif current_hour_count > mean:
            # No variance in the prior hours, but this hour broke the pattern.
            zscore = MOMENTUM_ZSCORE_CUTOFF
        is_spike = (
            zscore >= MOMENTUM_ZSCORE_CUTOFF
            and current_hour_count >= MOMENTUM_MIN_DISTINCT_SOURCES_FOR_SPIKE
        )

    momentum = (
        MOMENTUM_SOURCE_WEIGHT * source_breadth
        + MOMENTUM_SPIKE_WEIGHT * (1.0 if is_spike else 0.0)
    )
    return round(min(momentum, 1.0), 3), round(zscore, 3), is_spike


def annotate_momentum(scored: list[RankedStory], now: Optional[datetime] = None) -> list[RankedStory]:
    """Compute and record cross-source/spike momentum for every candidate.

    Must run before ranker.enforce_top_story_freshness, which reads
    score_breakdown["momentum"] to decide whether a story older than the
    plain freshness window may still lead. Runs on every candidate (not
    just the eventual #1), matching the shadow-mode pattern trend_scoring
    uses for trend_score, so the persisted time series stays complete
    regardless of which candidate is ultimately picked.

    Never raises: a momentum-tracking failure must not be the reason a
    cycle fails to post.
    """
    now = now or datetime.now(timezone.utc)
    hour = _hour_bucket(now)

    try:
        entries = load_trend_history()
        for entry in entries:
            entry["buckets"] = _prune_buckets(entry.get("buckets", {}), now)
        entries = [e for e in entries if e["buckets"]]

        for story in scored:
            tokens = _signature_tokens(story)
            entry = _find_matching_entry(entries, tokens)

            if entry is None:
                entry = {"signature": sorted(tokens), "buckets": {}}
                entries.append(entry)
            else:
                # Widen the signature toward the union so slow wording drift
                # across cycles doesn't lose the match over time.
                entry["signature"] = sorted(set(entry["signature"]) | tokens)

            buckets = entry["buckets"]
            buckets[hour] = sorted(set(buckets.get(hour, [])) | set(story.cluster_sources))

            cross_source_count = _cross_source_count(buckets, now)
            current_hour_count = len(buckets[hour])
            history_counts = _history_counts(buckets, now, exclude_hour=hour)

            momentum, zscore, is_spike = _score_momentum(
                cross_source_count, current_hour_count, history_counts
            )

            story.score_breakdown["momentum"] = momentum
            story.score_breakdown["cross_source_count"] = cross_source_count
            story.score_breakdown["momentum_zscore"] = zscore
            story.score_breakdown["momentum_spike"] = is_spike

        save_trend_history(entries)
    except Exception as exc:  # noqa: BLE001 — momentum must never break a cycle
        log.warning("Momentum tracking failed (%s); continuing with momentum=0.0.", exc)
        for story in scored:
            story.score_breakdown.setdefault("momentum", 0.0)

    return scored
