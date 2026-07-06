"""Posting to X/Twitter: credential loading, client construction, and a
retry-aware post function.

Retry policy:
- 429 rate limit: retry with backoff, respecting X's reset header.
- 5xx server error: retry with linear backoff.
- 401 auth failure: NOT retried — a bad credential won't fix itself.
- 403 Forbidden / 400 Bad Request: NOT retried — content/permission issue.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import tweepy

from .config import (
    ENV_VAR_NAMES,
    MAX_RETRY_ATTEMPTS,
    RETRY_BACKOFF_BASE_SECONDS,
    log,
)


def load_x_credentials() -> dict:
    values = {name: os.environ.get(name) for name in ENV_VAR_NAMES}
    missing = [name for name, val in values.items() if not val]
    if missing:
        raise RuntimeError(
            "Missing required X API credentials in environment: "
            f"{', '.join(missing)}. Set all of {ENV_VAR_NAMES} before running."
        )
    return values


def build_x_client() -> tweepy.Client:
    creds = load_x_credentials()
    return tweepy.Client(
        consumer_key=creds["X_API_KEY"],
        consumer_secret=creds["X_API_SECRET"],
        access_token=creds["X_ACCESS_TOKEN"],
        access_token_secret=creds["X_ACCESS_TOKEN_SECRET"],
        wait_on_rate_limit=False,  # we handle 429s ourselves in post_story()
    )


def post_story(client: tweepy.Client, text: str) -> Optional[str]:
    """
    Attempts to post `text` to X. Returns the new tweet's ID on success,
    or None if it could not be posted after retries — callers must treat
    None as "not posted" and must NOT record it in history.
    """
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            response = client.create_tweet(text=text)
            tweet_id = response.data.get("id") if response and response.data else None
            log.info("Posted tweet id=%s", tweet_id)
            return tweet_id

        except tweepy.errors.Unauthorized as exc:
            log.error("X API authentication failed (401): %s. Not retrying.", exc)
            return None

        except tweepy.errors.TooManyRequests as exc:
            reset_header = None
            try:
                reset_header = exc.response.headers.get("x-rate-limit-reset")
            except AttributeError:
                pass
            if reset_header:
                wait_seconds = max(1, int(reset_header) - int(time.time()))
            else:
                wait_seconds = RETRY_BACKOFF_BASE_SECONDS * attempt
            log.warning(
                "Rate limited (429) on attempt %d/%d; waiting %ds before retry.",
                attempt, MAX_RETRY_ATTEMPTS, wait_seconds,
            )
            if attempt < MAX_RETRY_ATTEMPTS:
                time.sleep(wait_seconds)
                continue
            log.error("Exhausted retries after repeated rate limiting; skipping this cycle.")
            return None

        except tweepy.errors.TwitterServerError as exc:
            wait_seconds = RETRY_BACKOFF_BASE_SECONDS * attempt
            log.warning(
                "X server error on attempt %d/%d (%s); waiting %ds before retry.",
                attempt, MAX_RETRY_ATTEMPTS, exc, wait_seconds,
            )
            if attempt < MAX_RETRY_ATTEMPTS:
                time.sleep(wait_seconds)
                continue
            log.error("Exhausted retries after repeated server errors; skipping this cycle.")
            return None

        except tweepy.errors.Forbidden as exc:
            # Commonly: duplicate content X itself detected, suspended
            # account, or a policy violation. Not retryable.
            log.error("X API rejected the post (403 Forbidden): %s", exc)
            return None

        except (tweepy.errors.BadRequest, tweepy.errors.NotFound) as exc:
            log.error("X API rejected the post (%s): %s", type(exc).__name__, exc)
            return None

        except tweepy.TweepyException as exc:
            log.error("Unexpected Tweepy error: %s", exc)
            return None

    return None
