"""Dedup store: tracks recently posted stories so they aren't re-posted.

Backed by a JSON file (data/posted_history.json). In GitHub Actions the
workflow commits this file back to the repo after each successful post, so
state persists across ephemeral runners.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from .config import (
    DEDUP_LOOKBACK_HOURS,
    DUPLICATE_SIMILARITY_THRESHOLD,
    POST_HISTORY_PATH,
    log,
)
from .models import PostedEntry, RankedStory
from .ranker import jaccard, tokenize


def load_history() -> list[PostedEntry]:
    if not POST_HISTORY_PATH.exists():
        return []
    try:
        with POST_HISTORY_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return [PostedEntry(**entry) for entry in raw]
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        log.warning("posted_history.json is corrupt (%s); starting with empty history.", exc)
        return []


def save_history(history: list[PostedEntry]) -> None:
    POST_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with POST_HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in history], f, ensure_ascii=False, indent=2)


def prune_history(history: list[PostedEntry], now: datetime) -> list[PostedEntry]:
    cutoff = now - timedelta(hours=DEDUP_LOOKBACK_HOURS)
    return [e for e in history if datetime.fromisoformat(e.posted_at) >= cutoff]


def is_duplicate(story: RankedStory, history: list[PostedEntry]) -> bool:
    if any(entry.link == story.link for entry in history):
        return True

    story_tokens = tokenize(story.title)
    for entry in history:
        if jaccard(story_tokens, tokenize(entry.title)) >= DUPLICATE_SIMILARITY_THRESHOLD:
            return True

    return False


def pick_non_duplicate(
    ranked_stories: list[RankedStory], history: list[PostedEntry]
) -> Optional[RankedStory]:
    for story in ranked_stories:
        if not is_duplicate(story, history):
            return story
    return None
