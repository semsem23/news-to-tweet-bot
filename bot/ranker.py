"""Ranking: clustering near-duplicate headlines and scoring "trendiness".

A single RSS pull has no engagement metrics, so trendiness is approximated
from three signals: cross-source repetition (40%), recency (35%, 3h
half-life exponential decay), and source prominence (25%) — multiplied by
a style penalty that down-ranks question/explainer/opinion headlines in
favor of hard news. A hard freshness constraint then guarantees the #1
slot goes to a recent story (<1h, progressively widened to 6h if needed).
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Optional

from .config import (
    CLUSTER_SIMILARITY_THRESHOLD,
    DEFAULT_PROMINENCE,
    RECENCY_HALF_LIFE_HOURS,
    SOURCE_PROMINENCE,
    STOPWORDS,
    TOP_N,
    TOP_STORY_AGE_WINDOWS,
    TOP_STORY_MAX_AGE_HOURS,
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


def score_cluster(cluster: list[dict], max_cluster_size: int, now: datetime) -> RankedStory:
    # Representative = article from the most prominent source in the cluster.
    rep = max(cluster, key=lambda a: prominence_of(a["source"]))

    repetition = len(cluster) / max_cluster_size if max_cluster_size else 0.0
    recency = max(recency_score(a["published_paris"], now) for a in cluster)
    prominence = max(prominence_of(a["source"]) for a in cluster)
    freshest_age = min(age_hours_of(a["published_paris"], now) for a in cluster)
    style_penalty = headline_style_penalty(rep["title"])

    composite = (
        WEIGHT_REPETITION * repetition
        + WEIGHT_RECENCY * recency
        + WEIGHT_PROMINENCE * prominence
    ) * style_penalty

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
        is_breaking=freshest_age < TOP_STORY_MAX_AGE_HOURS,
        score=round(composite, 4),
        score_breakdown={
            "repetition": round(repetition, 3),
            "recency": round(recency, 3),
            "prominence": round(prominence, 3),
            "style_penalty": round(style_penalty, 2),
        },
    )


def enforce_top_story_freshness(
    scored: list[RankedStory], windows: list[float] = TOP_STORY_AGE_WINDOWS
) -> list[RankedStory]:
    """
    Guarantees position #1 is the freshest reasonably-available story,
    without ever leaving the slot empty: try the strict window first; if
    nothing qualifies, progressively widen (1h -> 2h -> 3h -> 6h) and take
    the highest-scoring story clearing the first non-empty window. Only if
    every window is empty fall back to pure highest-score, with a warning.
    """
    if not scored:
        return scored

    if scored[0].age_hours < windows[0]:
        return scored

    for w in windows:
        candidates = [s for s in scored if s.age_hours < w]
        if candidates:
            best = candidates[0]  # `scored` is already score-sorted
            rest = [s for s in scored if s is not best]
            if w > windows[0]:
                print(
                    f"NOTE: nothing under {windows[0]}h available; widened to "
                    f"{w}h and promoted '{best.title}' ({best.age_hours}h old).",
                    file=sys.stderr,
                )
            return [best] + rest

    print(
        f"WARNING: no story younger than {windows[-1]}h was found in this "
        f"pull; cannot satisfy any freshness window. Falling back to the "
        f"highest-scoring story overall.",
        file=sys.stderr,
    )
    return scored


def rank_articles(articles: list[dict], top_n: int = TOP_N) -> list[RankedStory]:
    clusters = cluster_articles(articles)
    max_size = max(len(c) for c in clusters)
    now = datetime.now(timezone.utc)

    scored = [score_cluster(c, max_size, now) for c in clusters]
    scored.sort(key=lambda s: s.score, reverse=True)
    scored = enforce_top_story_freshness(scored)
    return scored[:top_n]
