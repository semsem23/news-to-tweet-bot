"""Central configuration for the News-to-Tweet Bot.

Every tunable lives here. Values marked (env) can be overridden with an
environment variable of the same name; everything else is edited in place.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Dedup state — committed back to the repo by the GitHub Actions workflow
# so it persists across ephemeral runners.
POST_HISTORY_PATH = DATA_DIR / "posted_history.json"

# Optional: load a .env file at the repo root, if present, so credentials
# can be set once in a file for local runs. Real environment variables
# (GitHub Actions secrets, systemd, Docker) always take precedence.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:
    pass

# --------------------------------------------------------------------------
# Timezone
# --------------------------------------------------------------------------

PARIS_TZ = ZoneInfo("Europe/Paris")

# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

FEED_URL_WORLD = "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en"
FEED_URL_USA = "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-US&gl=US&ceid=US:en"

FEED_URLS = {
    "WORLD": FEED_URL_WORLD,
    "USA": FEED_URL_USA,
}

# Time-based feed switching (Paris timezone)
FEED_SWITCH_HOUR_WORLD_START = 7    # 07:00 Paris time: switch to WORLD edition
FEED_SWITCH_HOUR_USA_START = 18     # 18:00 Paris time: switch to USA edition
FEED_WINDOW_OVERLAP_MINUTES = 60    # Minutes before/after boundary to fetch both feeds

# Support env override for local testing / CI (takes precedence over time-based logic)
FEED_URL = os.environ.get("FEED_URL", "")

REQUEST_TIMEOUT = 15  # seconds
USER_AGENT = "Mozilla/5.0 (compatible; NewsToTweetBot/1.0; +https://github.com/)"

# --------------------------------------------------------------------------
# Trending Topic Gating
# --------------------------------------------------------------------------

TRENDING_ENABLED = True
TRENDING_GEOS = {
    "world": ["US", "GB", "FR"],
    "usa": ["US"],
}
TRENDING_MATCH_THRESHOLD = 0.05  # TEMP: lowered for smoke test to ensure matches fire with real data

# --------------------------------------------------------------------------
# Topic Penalties
# --------------------------------------------------------------------------

# Keywords are stemmed using the same stem() function as ranker.py
# so they match tokenized headlines. See ranker.stem() for stem rules.
TOPIC_PENALTIES = {
    "politics": (
        0.45,
        {
            "senat", "congr", "parli", "elect", "minis",  # senate, congress, parliament, election, minister
            "presi", "supre", "court", "lawma", "govern",  # president, supreme, court, lawmakers, governor
        },
    ),
}

# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

TOP_N = 5

# Scoring weights (must sum to 1.0)
WEIGHT_REPETITION = 0.40
WEIGHT_RECENCY = 0.35
WEIGHT_PROMINENCE = 0.25

RECENCY_HALF_LIFE_HOURS = 3.0

# Token-overlap threshold above which two headlines are considered the
# same underlying story.
CLUSTER_SIMILARITY_THRESHOLD = 0.45

# Hard constraint: whichever story lands in the #1 slot must be based on
# a report no older than this, regardless of its composite score.
TOP_STORY_MAX_AGE_HOURS = 1.0

# If nothing in the pull is under TOP_STORY_MAX_AGE_HOURS, widen step by
# step rather than either going silent or ignoring freshness altogether.
TOP_STORY_AGE_WINDOWS = [TOP_STORY_MAX_AGE_HOURS, 2.0, 3.0, 6.0]

# Prominence lookup — coarse tiers. Unknown sources default to 0.5.
SOURCE_PROMINENCE = {
    # Wire services / global gold-standard
    "reuters": 1.0, "associated press": 1.0, "ap": 1.0, "afp": 1.0,
    # Major global broadcasters / papers
    "bbc": 0.9, "bbc news": 0.9, "the new york times": 0.9, "nyt": 0.9,
    "the guardian": 0.9, "the washington post": 0.9, "al jazeera": 0.9,
    "cnn": 0.85, "npr": 0.85, "the wall street journal": 0.9, "wsj": 0.9,
    "bloomberg": 0.9, "financial times": 0.9, "the economist": 0.9,
    "france 24": 0.8, "deutsche welle": 0.8, "dw": 0.8,
    "abc news": 0.8, "cbs news": 0.8, "nbc news": 0.8, "sky news": 0.8,
    "the times": 0.8, "the telegraph": 0.75, "politico": 0.75, "axios": 0.75,
    "time": 0.75, "newsweek": 0.65, "the independent": 0.7,
}
DEFAULT_PROMINENCE = 0.5

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "it", "its", "this", "that", "after", "over", "into", "amid",
    "than", "his", "her", "their", "will", "has", "have", "had",
    "new", "says", "say", "said", "up", "out", "who", "how", "what",
}

# --------------------------------------------------------------------------
# Tweet composition
# --------------------------------------------------------------------------

TWEET_MAX_CHARS = 288
T_CO_LINK_LENGTH = 24  # any link is shortened to 23 chars by t.co + 1 space
INCLUDE_LINK = False

# Only used when INCLUDE_LINK is True: resolve Google News' redirect
# wrapper to the real publisher URL before posting.
RESOLVE_REAL_ARTICLE_URL = True
URL_RESOLVE_TIMEOUT = 6  # seconds

# Alt headlines from the same cluster are quoted in the tweet only when
# their wording differs enough from the main headline to add information.
ALT_HEADLINE_MAX_SIMILARITY = 0.6

# Optional paid rephrasing via the Anthropic API. Only used when
# ANTHROPIC_API_KEY is set; otherwise the free composer runs.
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# --------------------------------------------------------------------------
# Posting
# --------------------------------------------------------------------------

# How far back to look when checking for duplicates. Should comfortably
# exceed the posting interval (1h) so a story that trends across several
# consecutive hourly pulls doesn't get re-posted each time.
DEDUP_LOOKBACK_HOURS = 48

# Reuse the clustering threshold so "the same story, reworded by a
# different outlet an hour later" is caught.
DUPLICATE_SIMILARITY_THRESHOLD = CLUSTER_SIMILARITY_THRESHOLD

# Retry behavior for transient API failures (rate limit / server error).
MAX_RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SECONDS = 30

ENV_VAR_NAMES = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("news_to_tweet_bot")

# --------------------------------------------------------------------------
# Feed selection logic
# --------------------------------------------------------------------------

from datetime import datetime, timedelta

def get_active_feed_urls(dt_paris: datetime | None = None) -> list[str]:
    """
    Return the list of active feed URLs for a given Paris-local time.

    Normally returns [FEED_URL_WORLD] (07:00-18:00) or [FEED_URL_USA] (18:00-07:00).
    During overlap windows (60 minutes before/after each boundary), returns both.

    Args:
        dt_paris: datetime in Paris timezone. If None, uses current time.

    Returns:
        List of feed URLs to fetch. Normally 1, sometimes 2 during overlap windows.
    """
    if dt_paris is None:
        dt_paris = datetime.now(PARIS_TZ)

    hour = dt_paris.hour
    minute = dt_paris.minute

    # Minute-of-day for the current time
    minute_of_day = hour * 60 + minute

    # Boundaries in minutes since midnight
    world_start_minutes = FEED_SWITCH_HOUR_WORLD_START * 60
    usa_start_minutes = FEED_SWITCH_HOUR_USA_START * 60
    overlap_window_minutes = FEED_WINDOW_OVERLAP_MINUTES

    active_urls = []

    # Check if we're near the USA->WORLD boundary (07:00)
    if abs(minute_of_day - world_start_minutes) <= overlap_window_minutes:
        # Within overlap window around 07:00
        active_urls.append(FEED_URL_WORLD)
        active_urls.append(FEED_URL_USA)
    # Check if we're near the WORLD->USA boundary (18:00)
    elif abs(minute_of_day - usa_start_minutes) <= overlap_window_minutes:
        # Within overlap window around 18:00
        active_urls.append(FEED_URL_WORLD)
        active_urls.append(FEED_URL_USA)
    # During regular WORLD hours (07:00-18:00, outside overlap)
    elif world_start_minutes <= minute_of_day < usa_start_minutes:
        active_urls.append(FEED_URL_WORLD)
    # During regular USA hours (18:00-07:00, outside overlap)
    else:
        active_urls.append(FEED_URL_USA)

    return active_urls
