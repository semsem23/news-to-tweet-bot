"""Local, per-article "trend score" — a heuristic estimate of viral potential.

Adapted from a standalone batch script (analyze_article() over a pandas
DataFrame) to score one RankedStory at a time inside the live pipeline.
Runs entirely offline (no Google Trends API): sentiment (VADER), death/
violence keyword boosts, a hot-geopolitical-topic proxy, urgency markers,
and named-entity density.

Both `vaderSentiment` and `spacy` are optional. If either is missing, the
corresponding sub-score degrades to a neutral/zero contribution rather than
raising — this module must never be the reason a cycle fails.
"""

from __future__ import annotations

from .config import log
from .models import RankedStory

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    _sia = SentimentIntensityAnalyzer()
except ImportError:
    log.warning(
        "vaderSentiment not installed; trend scoring will treat sentiment as neutral. "
        "Install vaderSentiment to enable this signal."
    )
    _sia = None

try:
    import spacy

    _nlp = spacy.load("en_core_web_sm")
except Exception:  # noqa: BLE001 — not installed, no model, or load error: degrade gracefully
    _nlp = None

DEATH_KEYWORDS = [
    "kill", "killed", "dead", "died", "death", "murder", "massacre",
    "bomb", "blast", "explosion", "attack", "terror", "terrorism",
    "hostage", "crash", "disaster", "flood", "quake", "missile", "strike",
]

HOT_GEOPOLITICAL_KEYWORDS = [
    "israel", "gaza", "hamas", "netanyahu", "palestin", "west bank",
    "iran", "trump", "putin", "ukraine", "zelensky", "russia",
    "taiwan", "china", "india", "pakistan", "houthi", "yemen", "syria",
    "lebanon", "hezbollah", "north korea", "kim",
]

URGENCY_WORDS = [
    "breaking", "urgent", "alert", "just in", "now", "today",
    "live", "developing", "fast", "rapid", "moment", "unfolding",
]


def analyze_article(title: str, content: str = "") -> dict:
    """Score a single article's viral potential. Never raises."""
    text = f"{title or ''} {content or ''}"
    text_lower = text.lower()

    if _sia is not None:
        sentiment = _sia.polarity_scores(text)
    else:
        sentiment = {"compound": 0.0}
    compound_abs = abs(sentiment["compound"])
    very_negative = sentiment["compound"] <= -0.4

    death_boost = 0.35 if any(w in text_lower for w in DEATH_KEYWORDS) else 0.0

    geo_count = sum(text_lower.count(word) for word in HOT_GEOPOLITICAL_KEYWORDS)
    geo_factor = min(geo_count / 5.0, 1.5)

    urgency_boost = 0.25 if any(w in text_lower for w in URGENCY_WORDS) else 0.0

    entity_density = 0.0
    entity_boost = 0.0
    if _nlp is not None:
        try:
            doc = _nlp(text[:1_000_000])
            entity_density = len(doc.ents) / max(len(text.split()), 1)
            entity_boost = min(entity_density * 4, 0.45)
        except Exception:  # noqa: BLE001 — third-party model call, contain everything
            entity_density = 0.0
            entity_boost = 0.0

    trend_score = (
        0.32 * compound_abs
        + 0.16 * (1.0 if very_negative else 0.35)
        + 0.18 * death_boost
        + 0.20 * geo_factor
        + 0.08 * urgency_boost
        + 0.06 * entity_boost
    )
    trend_score = round(max(0.0, min(1.25, trend_score)), 3)

    return {
        "trend_score": trend_score,
        "sentiment_compound": round(sentiment["compound"], 3),
        "sentiment_abs": round(compound_abs, 3),
        "very_negative": very_negative,
        "geo_hot_count": geo_count,
        "urgency_detected": bool(urgency_boost > 0),
        "entity_density": round(entity_density, 3),
    }


def _pseudo_content(story: RankedStory) -> str:
    """Alternate headlines from the same cluster, used as a stand-in for
    article body text (the RSS-only pipeline never fetches article content)."""
    return " ".join(
        h["title"] for h in story.cluster_headlines if h["title"] != story.title
    )


def apply_trend_scoring(ranked: list[RankedStory]) -> list[RankedStory]:
    """Re-rank candidates by local trend_score, leaving position #1 untouched.

    Position #1 is already locked in by ranker.enforce_top_story_freshness's
    freshness/momentum guarantee; trend scoring only reorders the rest so it
    can't undermine that guarantee.
    """
    if len(ranked) <= 1:
        return ranked

    head, tail = ranked[0], ranked[1:]

    for story in tail:
        metrics = analyze_article(story.title, _pseudo_content(story))
        story.score_breakdown["trend_score"] = metrics["trend_score"]

    tail.sort(key=lambda s: s.score_breakdown.get("trend_score", 0.0), reverse=True)

    return [head] + tail
