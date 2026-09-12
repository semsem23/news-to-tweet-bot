"""Central configuration for the News-to-Tweet Bot.

Every tunable lives here. Values marked (env) can be overridden with an
environment variable of the same name; everything else is edited in place.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
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

FEEDS = {
    "WORLD": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "NATION": "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-US&gl=US&ceid=US:en",
    "BUSINESS": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    "ENTERTAINMENT": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en",
    "SPORTS": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en",
}

REQUEST_TIMEOUT = 15  # seconds
USER_AGENT = "Mozilla/5.0 (compatible; NewsToTweetBot/1.0; +https://github.com/)"


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

TOP_N = 5

# Scoring weights (must sum to 1.0)
WEIGHT_FEED_POSITION = 0.65
WEIGHT_RECENCY = 0.25
WEIGHT_PROMINENCE = 0.05
WEIGHT_REPETITION = 0.05

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

# Momentum-aware freshness gate: a story whose momentum is at least this
# may lead the ranking even when older than TOP_STORY_MAX_AGE_HOURS,
# allowing a sustained-coverage trend to rank over a fresher one-off.
TREND_LEAD_MOMENTUM_FLOOR = 0.35

# ...but never let a story older than this lead, however strong its momentum.
# Prevents stale trending stories from monopolizing the top slot.
TREND_MAX_LEAD_AGE_HOURS = 6.0

# Local "trend_score" re-ranking (bot/trend_scoring.py): sentiment, death/
# violence keywords, live-coverage format, celebrity/sports, urgency markers.
# Off by default for a gradual rollout. When enabled, it only reorders
# ranked[1:] — the #1 slot stays whatever enforce_top_story_freshness above
# already decided, so this can never undermine that freshness guarantee.
ENABLE_TREND_SCORING = False

# --------------------------------------------------------------------------
# Trend scoring weights and keyword sets
# --------------------------------------------------------------------------
#
# Calibrated 2026-09-12 against 706 posted tweets (Jun 28 - Aug 19 2026) joined
# to their real per-post impressions. Baseline median = 21 impressions.
#
# Measured Spearman(trend_score, impressions) over those 706 posts:
#     previous weights  +0.223
#     these weights     +0.353
# Bootstrap (400 resamples) 95% CI on the improvement: [+0.080, +0.191];
# the gain holds in both chronological halves and survives excluding the
# four "Watch Live" posts, so it is not an artifact of that small cohort.
#
# Per-feature Spearman vs. impressions measured on the same sample:
#     death/violence keyword   +0.262      urgency marker       -0.005
#     live-coverage format     +0.168      geopolitical count   +0.023
#     |sentiment|              +0.185      question headline    -0.082
#     very negative            +0.169      analytical/opinion   -0.005
#
# GEO is deliberately 0.0: geopolitical keyword count showed no usable
# relationship with reach (median impressions by geo count: 21 / 20 / 21.5 /
# 35 / 20), yet under the previous weights it was the single largest term
# (0.20 x geo_factor up to 1.5 = 0.30). It promoted Ukraine/Iran liveblogs
# that earned 15-72 impressions over "Watch Live" trial coverage that earned
# 384-1225. Left in the output dict as a diagnostic; set above 0.0 only if a
# future sample shows it earning its place.
TREND_WEIGHTS = {
    "watch_live": 0.75,   # broadcast/video coverage: n=4, median 401 imp (19.1x)
    "live_blog": 0.25,    # text liveblog/"latest": n=35, median 41 imp (1.95x)
    "death": 0.25,        # crime/violence/disaster: n=237, median 29 imp (1.38x)
    "celebrity": 0.20,    # celebrity/sports: n=51, median 31 imp (1.48x)
    "urgency": 0.10,      # weak on its own; kept small as a tiebreaker
    "geo": 0.00,          # see note above — measured as noise, disabled
    "sentiment_abs": 0.10,
    "very_negative": 0.08,
    "entity": 0.06,       # inert unless spacy + en_core_web_sm are installed;
                          # NOT part of the 706-post calibration (spacy absent)
    "question_penalty": 0.20,    # n=10, median 13 imp (0.62x baseline)
    "analytical_penalty": 0.12,  # n=2 here; penalty carried over from the brief
}

TREND_SCORE_MAX = 1.25

# Neutral headlines would otherwise land on the 0.0 floor, where the question
# and analytical penalties become invisible and every low-signal candidate
# ties — apply_trend_scoring sorts the tail by this value, so ties there are
# decided arbitrarily. This offset lifts the neutral case off the floor so the
# penalties can still separate candidates. A constant offset does not change
# rank order anywhere else.
TREND_SCORE_BASELINE = 0.25

# Broadcast/video live coverage. Matched against the headline only — this is a
# format signal that lives in the headline prefix, not in body text.
WATCH_LIVE_MARKERS = ["watch live", "watch:"]

# Text liveblogs and rolling "latest" pages. Real but far weaker than the
# above, so scored separately rather than lumped into one "live" bucket.
LIVE_BLOG_MARKERS = [
    "live updates", "live:", "war live", "liveblog", "live blog",
    "latest:", "updates:", " live ",
]

DEATH_KEYWORDS = [
    "kill", "killed", "dead", "died", "death", "murder", "massacre",
    "bomb", "blast", "explosion", "attack", "terror", "terrorism",
    "hostage", "crash", "disaster", "flood", "quake", "missile", "strike",
    # added in the 2026-09-12 calibration pass
    "shooting", "shot", "stabbed", "wounded", "avalanche", "earthquake",
    "wildfire",
]

# Celebrity / sports. Absent from the previous keyword set entirely, despite
# this cohort running ~1.5x baseline (and Taylor Swift/Kelce posts ~7.8x).
CELEBRITY_KEYWORDS = [
    "taylor swift", "kelce", "kardashian", "beyonce", "drake",
    "actor", "actress", "singer", "rapper", "album", "movie", "film",
    "celebrity", "wedding", "divorce",
    "nfl", "nba", "mlb", "fifa", "world cup", "olympic", "soccer",
    "super bowl", "ufc", "boxing", "championship", "tournament",
    "coach", "player",
]

HOT_GEOPOLITICAL_KEYWORDS = [
    "israel", "gaza", "hamas", "netanyahu", "palestin", "west bank",
    "iran", "trump", "putin", "ukraine", "zelensky", "russia",
    "taiwan", "china", "india", "pakistan", "houthi", "yemen", "syria",
    "lebanon", "hezbollah", "north korea", "kim",
]

# "now"/"today"/"fast"/"rapid"/"moment" were dropped: they fire on ordinary
# headlines and diluted the signal to nothing (measured Spearman -0.005).
URGENCY_WORDS = [
    "breaking", "urgent", "alert", "just in", "developing", "unfolding",
]

# Analytical/explainer framing — underperforms per the Jun-Aug sample.
ANALYTICAL_MARKERS = [
    "analysis", "opinion", "explainer", "what to know", "here's why",
    "here is why", "the case for", "commentary", "perspective",
]

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
# Headline Filters
# --------------------------------------------------------------------------

MIN_TWEET_CHARS = 61
EXCLUDE_HOROSCOPE = True
EXCLUDE_QUESTION_HEADLINES = True
QUESTION_START_WORDS = {
    "what", "why", "how", "who", "when", "where", "which",
    "is", "are", "can", "could", "should", "would", "will", "does", "do", "did",
}

# --------------------------------------------------------------------------
# Posting
# --------------------------------------------------------------------------

# Minimum time between posts. The workflow's cron is scheduled to run every
# 80 minutes already; this gate is a safety net against cron jitter/overlap.
POST_MIN_INTERVAL_MINUTES = 80

# How far back to look when checking for duplicates. Should comfortably
# exceed the posting interval so a story that trends across several
# consecutive pulls doesn't get re-posted each time.
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
