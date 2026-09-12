"""Local, per-article "trend score" — a heuristic estimate of viral potential.

Adapted from a standalone batch script (analyze_article() over a pandas
DataFrame) to score one RankedStory at a time inside the live pipeline.
Runs entirely offline (no Google Trends API): live-coverage format, death/
violence keywords, celebrity/sports pull, sentiment (VADER), urgency markers,
and named-entity density.

Weights and keyword sets live in bot/config.py (TREND_WEIGHTS and the
*_KEYWORDS / *_MARKERS lists) and were calibrated on 2026-09-12 against 706
posted tweets joined to their real impressions — see the notes there for the
measured per-feature correlations and why the geopolitical term is disabled.

Two families of signal are matched differently:
  * FORMAT signals (watch-live, liveblog, question, analytical framing) are
    matched against the headline only. They are properties of how the
    headline is written, and matching them against `content` — which is
    other outlets' headlines for the same story — produced false fires.
  * LEXICAL signals (death, celebrity, geopolitics, sentiment, entities) are
    matched against headline + content, where the extra text adds evidence.

Both `vaderSentiment` and `spacy` are optional. If either is missing, the
corresponding sub-score degrades to a neutral/zero contribution rather than
raising — this module must never be the reason a cycle fails.
"""

from __future__ import annotations

from . import config
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

# Re-exported for backwards compatibility: these used to be defined here.
DEATH_KEYWORDS = config.DEATH_KEYWORDS
HOT_GEOPOLITICAL_KEYWORDS = config.HOT_GEOPOLITICAL_KEYWORDS
URGENCY_WORDS = config.URGENCY_WORDS


def _is_question_headline(title: str) -> bool:
    """Question framing — measured at 0.62x baseline reach on the Jun-Aug sample."""
    stripped = (title or "").strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    first = stripped.split()[0].lower().strip(",:")
    return first in config.QUESTION_START_WORDS


def analyze_article(title: str, content: str = "") -> dict:
    """Score a single article's viral potential. Never raises."""
    w = config.TREND_WEIGHTS

    title = title or ""
    content = content or ""
    full_text = f"{title} {content}"
    full_lower = full_text.lower()
    # Pad so " live " style markers can match at the headline edges.
    title_lower = f" {title.lower()} "

    # --- format signals: headline only -------------------------------------
    watch_live = any(m in title_lower for m in config.WATCH_LIVE_MARKERS)
    # A "Watch Live:" headline is also a liveblog by wording; score it in the
    # stronger bucket only, so the two weights never stack.
    live_blog = (not watch_live) and any(
        m in title_lower for m in config.LIVE_BLOG_MARKERS
    )
    is_question = _is_question_headline(title)
    is_analytical = any(m in title_lower for m in config.ANALYTICAL_MARKERS)

    # --- lexical signals: headline + content -------------------------------
    if _sia is not None:
        sentiment = _sia.polarity_scores(full_text)
    else:
        sentiment = {"compound": 0.0}
    compound_abs = abs(sentiment["compound"])
    very_negative = sentiment["compound"] <= -0.4

    has_death = any(k in full_lower for k in config.DEATH_KEYWORDS)
    has_celebrity = any(k in full_lower for k in config.CELEBRITY_KEYWORDS)
    has_urgency = any(k in full_lower for k in config.URGENCY_WORDS)

    geo_count = sum(full_lower.count(k) for k in config.HOT_GEOPOLITICAL_KEYWORDS)
    geo_factor = min(geo_count / 5.0, 1.0)

    entity_density = 0.0
    entity_boost = 0.0
    if _nlp is not None:
        try:
            doc = _nlp(full_text[:1_000_000])
            entity_density = len(doc.ents) / max(len(full_text.split()), 1)
            entity_boost = min(entity_density * 4, 0.45)
        except Exception:  # noqa: BLE001 — third-party model call, contain everything
            entity_density = 0.0
            entity_boost = 0.0

    trend_score = (
        config.TREND_SCORE_BASELINE
        + w["watch_live"] * (1.0 if watch_live else 0.0)
        + w["live_blog"] * (1.0 if live_blog else 0.0)
        + w["death"] * (1.0 if has_death else 0.0)
        + w["celebrity"] * (1.0 if has_celebrity else 0.0)
        + w["urgency"] * (1.0 if has_urgency else 0.0)
        + w["geo"] * geo_factor
        + w["sentiment_abs"] * compound_abs
        + w["very_negative"] * (1.0 if very_negative else 0.0)
        + w["entity"] * entity_boost
        - w["question_penalty"] * (1.0 if is_question else 0.0)
        - w["analytical_penalty"] * (1.0 if is_analytical else 0.0)
    )
    trend_score = round(max(0.0, min(config.TREND_SCORE_MAX, trend_score)), 3)

    return {
        "trend_score": trend_score,
        "sentiment_compound": round(sentiment["compound"], 3),
        "sentiment_abs": round(compound_abs, 3),
        "very_negative": very_negative,
        "geo_hot_count": geo_count,
        "urgency_detected": has_urgency,
        "entity_density": round(entity_density, 3),
        "watch_live": watch_live,
        "live_blog": live_blog,
        "death_detected": has_death,
        "celebrity_detected": has_celebrity,
        "question_headline": is_question,
        "analytical_headline": is_analytical,
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
