"""Tweet composition.

Free path (default): AP-style tightening of the headline + expansion using
real, attributed headlines from other outlets in the same cluster ("Also
reported" lines). Nothing is ever invented — every character comes from an
actual published headline.

Optional paid path: if ANTHROPIC_API_KEY is set, the headline is rewritten
into punchy journalistic copy by the Anthropic API instead.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Optional

from .config import (
    ALT_HEADLINE_MAX_SIMILARITY,
    ANTHROPIC_MODEL,
    INCLUDE_LINK,
    T_CO_LINK_LENGTH,
    TWEET_MAX_CHARS,
)
from .models import RankedStory
from .ranker import jaccard, tokenize

# --------------------------------------------------------------------------
# Length accounting
# --------------------------------------------------------------------------

# X auto-shortens every URL to a fixed 23-char t.co wrapper and counts it
# as such against the character limit — regardless of the URL's real
# length. Length checks on final tweet text must mirror that rule, or long
# Google News redirect URLs (150-300+ raw chars) cause false rejections.
URL_PATTERN = re.compile(r"https?://\S+")
TCO_SHORTENED_LENGTH = 23


def twitter_weighted_length(text: str) -> int:
    """Length of `text` as X would count it: any URL counts as a flat 23
    characters instead of its real length, everything else counts as-is."""
    length = len(text)
    for url in URL_PATTERN.findall(text):
        length += TCO_SHORTENED_LENGTH - len(url)
    return length


def fit_to_budget(tweet: str, char_budget: int) -> str:
    if len(tweet) <= char_budget:
        return tweet
    return tweet[: char_budget - 1].rsplit(" ", 1)[0] + "…"


# --------------------------------------------------------------------------
# Free path: tightening + cluster expansion
# --------------------------------------------------------------------------


def tighten_text(title: str, is_breaking: bool = True) -> str:
    """
    AP-style tightening with no truncation — strips throat-clearing
    lead-ins and contracts wordy constructions into shorter, same-meaning
    synonyms. Deliberately conservative: every substitution is a
    contraction, never a semantic rewrite, so there's no risk of
    distorting facts.
    """
    text = title.strip()

    lead_in_patterns = [
        r"^(Report|Watch|Opinion):\s*",
        r"^According to (reports|officials|sources)[,:]?\s*",
        r"^It (has been reported|is reported) that\s*",
        r"^In a (statement|report)[,:]?\s*",
        r"^Sources say(s)? that\s*",
        r"^Officials (say|said)( that)?\s*",
    ]
    for pattern in lead_in_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    if not is_breaking:
        text = re.sub(r"^(Breaking:|Just In:)\s*", "", text, flags=re.IGNORECASE)

    contractions = [
        (r"\bin order to\b", "to"),
        (r"\bdue to the fact that\b", "because"),
        (r"\ba number of\b", "several"),
        (r"\bis set to\b", "will"),
        (r"\bare set to\b", "will"),
        (r"\bin the wake of\b", "after"),
        (r"\bwith regard to\b", "on"),
        (r"\bat this point in time\b", "now"),
        (r"\bin light of\b", "given"),
        (r"\bon the grounds that\b", "because"),
        (r"\bfor the purpose of\b", "to"),
        (r"\bin the process of\b", ""),
        (r"\ba total of\b", ""),
    ]
    for pattern, replacement in contractions:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    text = re.sub(r"\s{2,}", " ", text).strip()

    # Re-capitalize if stripping/contracting left a lowercase first letter —
    # unless that first word is a stylized-lowercase brand name.
    lowercase_stylized_brands = {"iphone", "ipad", "ipod", "imac", "ios", "ebay", "esports", "eharmony"}
    if text and text[0].islower():
        first_word = text.split(" ", 1)[0].strip(".,!?:;\"'")
        if first_word.lower() not in lowercase_stylized_brands:
            text = text[0].upper() + text[1:]

    return text


def rephrase_rule_based(title: str, char_budget: int, is_breaking: bool = True) -> str:
    """Zero-cost fallback: tighten, then truncate to fit the char budget."""
    text = tighten_text(title, is_breaking)
    if len(text) > char_budget:
        text = text[: char_budget - 1].rsplit(" ", 1)[0] + "…"
    return text


def compose_expanded_tweet(story: RankedStory, char_budget: int) -> str:
    """
    Composes a longer tweet using only real, attributable material: the
    tightened main headline with source attribution, plus how OTHER
    outlets in the same cluster worded the same story ("Also reported"
    lines). Alt headlines are only included when their wording differs
    enough from the main one to actually add information.

    A single-source story can't be expanded this way — it posts as the
    tightened headline plus attribution, however short. Padding it
    further would mean inventing content.
    """
    main = tighten_text(story.title, story.is_breaking)
    body = f"{main} ({story.source})" if story.source and story.source != "Unknown" else main
    if len(body) > char_budget:
        return fit_to_budget(main, char_budget)

    main_tokens = tokenize(story.title)
    seen_sources = {story.source}

    for alt in story.cluster_headlines[1:]:
        alt_title, alt_source = alt["title"], alt["source"]
        if alt_source in seen_sources:
            continue  # one line per outlet is enough
        # Skip near-identical wordings — they'd add length but no info.
        if jaccard(tokenize(alt_title), main_tokens) >= ALT_HEADLINE_MAX_SIMILARITY:
            continue

        line = f"\n\nAlso reported: “{tighten_text(alt_title, story.is_breaking)}” ({alt_source})"
        if len(body) + len(line) > char_budget:
            break
        body += line
        seen_sources.add(alt_source)

    return body


# --------------------------------------------------------------------------
# Optional paid path: Anthropic API
# --------------------------------------------------------------------------

REPHRASE_SYSTEM_PROMPT = """You are a news-desk social media editor. Rewrite the given headline \
into a single punchy, journalistic tweet.

Rules:
- Active voice, present tense where natural, no filler words.
- Convey urgency/trending tone without sensationalizing or adding facts not in the headline.
- Do NOT invent details, numbers, or quotes that aren't in the source headline.
- No hashtags unless one obvious topical tag adds real value (max one).
- No more than one emoji, and only if it fits naturally — often none is best.
- Output ONLY the tweet text, nothing else (no quotes, no preamble, no explanation).
- Hard limit: {char_budget} characters, including any hashtag/emoji."""


def rephrase_with_claude(title: str, char_budget: int, is_breaking: bool = True) -> Optional[str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    system_prompt = REPHRASE_SYSTEM_PROMPT.format(char_budget=char_budget)
    if not is_breaking:
        system_prompt += (
            "\n- This report is more than 1 hour old — do NOT use urgency/"
            "immediacy language such as 'breaking', 'just in', 'happening "
            "now', or 'moments ago'."
        )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=120,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Headline: {title}"}],
        )
        text_blocks = [b.text for b in response.content if getattr(b, "type", "") == "text"]
        tweet = "".join(text_blocks).strip().strip('"')
        return tweet or None
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the cycle
        print(f"WARNING: Claude rephrase failed ({exc}); falling back to rule-based.", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_tweet(story: RankedStory) -> str:
    char_budget = TWEET_MAX_CHARS - (T_CO_LINK_LENGTH if INCLUDE_LINK else 0)

    tweet_body = rephrase_with_claude(story.title, char_budget, is_breaking=story.is_breaking)
    if tweet_body is None:
        tweet_body = compose_expanded_tweet(story, char_budget)

    tweet_body = fit_to_budget(tweet_body, char_budget)

    if INCLUDE_LINK:
        return f"{tweet_body} {story.link}"
    return tweet_body
