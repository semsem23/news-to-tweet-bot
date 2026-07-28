"""Ranking: clustering near-duplicate headlines and scoring "trendiness".

A single RSS pull has no engagement metrics, so trendiness is approximated
from three signals: cross-source repetition (40%), recency (35%, 3h
half-life exponential decay), and source prominence (25%) — multiplied by
a style penalty that down-ranks question/explainer/opinion headlines in
favor of hard news. Stories older than MAX_STORY_AGE_HOURS are excluded
outright as a sanity floor; within that ceiling, importance (repetition +
prominence) drives rank and recency only acts as a tiebreaker, so a
well-corroborated story from a couple of hours ago can, and should, outrank
a thinly-sourced one that broke twenty minutes ago.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Optional

from .config import (
    BREAKING_NEWS_MAX_AGE_HOURS,
    CLUSTER_SIMILARITY_THRESHOLD,
    DEFAULT_PROMINENCE,
    MAX_STORY_AGE_HOURS,
    RECENCY_HALF_LIFE_HOURS,
    SOURCE_PROMINENCE,
    STOPWORDS,
    TOPIC_PENALTIES,
    TOP_N,
    WEIGHT_PROMINENCE,
    WEIGHT_RECENCY,
    WEIGHT_REPETITION,
)
from .models import RankedStory

# --------------------------------------------------------------------------
# Tokenization / similarity
# --------------------------------------------------------------------------


def stem(word: str) -> str:
    """Crude prefix-truncation stemmer — just enough to match simple
    morphological variants (issued/issues, agree/agrees) without pulling in
    a full NLP dependency. Suffix-stripping proved inconsistent for
    irregular pairs; fixed-prefix truncation maps both forms to the same
    stem regardless of which one carried the suffix."""
    return word if len(word) <= 5 else word[:5]


def tokenize(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", title.lower())
    return {stem(w) for w in words if w not in STOPWORDS and len(w) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def cluster_articles(articles: list[dict]) -> list[list[dict]]:
    """
    Greedy nearest-cluster assignment: each article is compared against
    every member of every existing cluster and joins whichever it matches
    best — provided the best match clears the similarity threshold.
    O(n^2) token-set comparisons; fine at RSS-feed scale.
    """
    clusters: list[list[dict]] = []
    cluster_tokens: list[list[set[str]]] = []

    for art in articles:
        tokens = tokenize(art["title"])
        best_idx = None
        best_sim = 0.0

        for i, member_token_sets in enumerate(cluster_tokens):
            sim = max(jaccard(tokens, t) for t in member_token_sets)
            if sim > best_sim:
                best_sim = sim
                best_idx = i

        if best_idx is not None and best_sim >= CLUSTER_SIMILARITY_THRESHOLD:
            clusters[best_idx].append(art)
            cluster_tokens[best_idx].append(tokens)
        else:
            clusters.append([art])
            cluster_tokens.append([tokens])

    return clusters


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def prominence_of(source: str) -> float:
    return SOURCE_PROMINENCE.get(source.strip().lower(), DEFAULT_PROMINENCE)


def age_hours_of(published_paris_iso: str, now: datetime) -> float:
    published = datetime.fromisoformat(published_paris_iso)
    return max(0.0, (now - published).total_seconds() / 3600.0)


def recency_score(published_paris_iso: str, now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    age_hours = age_hours_of(published_paris_iso, now)
    return 0.5 ** (age_hours / RECENCY_HALF_LIFE_HOURS)


def headline_style_penalty(title: str) -> float:
    """
    Multiplier in (0, 1] applied to a story's composite score.
    Question-style, explainer, opinion, live-blog, and listicle headlines
    are inherently soft/analytical rather than punchy breaking news — a
    trending-news bot should prefer hard-news headlines from the same pull
    when they exist. Penalized (not excluded): if a cycle's pull contains
    ONLY soft headlines, one can still be posted rather than going silent.
    """
    t = title.strip().lower()

    if t.endswith("?"):
        return 0.55
    if re.match(r"^(what|why|how|who|when|where|is|are|can|could|should|will|does|do)\b", t):
        return 0.65

    soft_markers = (
        "explained", "explainer", "analysis:", "opinion:", "editorial:",
        "live updates", "live blog", "in pictures", "in photos", "watch:",
        "what we know", "what to know", "everything you need to know",
        "takeaways", "recap", "timeline:", "factbox", "q&a", "faq",
    )
    if any(marker in t for marker in soft_markers):
        return 0.6

    if re.match(r"^\d+\s+(things|ways|reasons|takeaways|questions|photos|charts|maps)\b", t):
        return 0.6

    return 1.0


def topic_penalty(title: str) -> float:
    """
    Multiplier in (0, 1] applied to a story's composite score based on its topic.

    Certain topics (e.g., politics) can be interesting but tend to dominate feeds.
    This penalty allows routine stories in those categories to be deprioritized
    while still letting genuinely huge stories (high repetition/recency) break through.

    Returns the penalty multiplier if title matches a topic category's keywords,
    otherwise 1.0 (no penalty).
    """
    if not TOPIC_PENALTIES:
        return 1.0

    title_tokens = tokenize(title)
    if not title_tokens:
        return 1.0

    for topic_name, (penalty_multiplier, keywords) in TOPIC_PENALTIES.items():
        if title_tokens & keywords:
            return penalty_multiplier

    return 1.0


def score_cluster(cluster: list[dict], max_cluster_size: int, now: datetime) -> RankedStory:
    # Representative = article from the most prominent source in the cluster.
    rep = max(cluster, key=lambda a: prominence_of(a["source"]))

    repetition = len(cluster) / max_cluster_size if max_cluster_size else 0.0
    recency = max(recency_score(a["published_paris"], now) for a in cluster)
    prominence = max(prominence_of(a["source"]) for a in cluster)
    freshest_age = min(age_hours_of(a["published_paris"], now) for a in cluster)
    style_penalty = headline_style_penalty(rep["title"])
    topic_penalty_val = topic_penalty(rep["title"])

    composite = (
        WEIGHT_REPETITION * repetition
        + WEIGHT_RECENCY * recency
        + WEIGHT_PROMINENCE * prominence
    ) * style_penalty * topic_penalty_val

    return RankedStory(
        title=rep["title"],
        source=rep["source"],
        link=rep["link"],
        published_paris=rep["published_paris"],
        cluster_size=len(cluster),
        cluster_sources=sorted({a["source"] for a in cluster}),
        cluster_headlines=(
            [{"title": rep["title"], "source": rep["source"]}]
            + [{"title": a["title"], "source": a["source"]} for a in cluster if a is not rep]
        ),
        age_hours=round(freshest_age, 3),
        is_breaking=freshest_age < BREAKING_NEWS_MAX_AGE_HOURS,
        score=round(composite, 4),
        score_breakdown={
            "repetition": round(repetition, 3),
            "recency": round(recency, 3),
            "prominence": round(prominence, 3),
            "style_penalty": round(style_penalty, 2),
            "topic_penalty": round(topic_penalty_val, 2),
        },
    )


def exclude_stale_stories(
    scored: list[RankedStory], max_age_hours: float = MAX_STORY_AGE_HOURS
) -> list[RankedStory]:
    """
    Drops stories older than the absolute ceiling before ranking. This is a
    sanity floor, not a freshness gate on the #1 slot — importance already
    outweighs recency in the composite score, so ranking is left to
    score_cluster()/sort. Falls back to the full list (with a warning)
    if every story in the pull is stale, rather than going silent.
    """
    fresh = [s for s in scored if s.age_hours <= max_age_hours]
    if fresh:
        return fresh

    print(
        f"WARNING: every story in this pull is older than {max_age_hours}h; "
        f"excluding all of them would leave nothing to post. Ranking by "
        f"score alone instead.",
        file=sys.stderr,
    )
    return scored


def rank_articles(articles: list[dict], top_n: int = TOP_N) -> list[RankedStory]:
    clusters = cluster_articles(articles)
    max_size = max(len(c) for c in clusters)
    now = datetime.now(timezone.utc)

    scored = [score_cluster(c, max_size, now) for c in clusters]
    scored = exclude_stale_stories(scored)
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:top_n]
