"""Tests for tweet composition."""

from bot.models import RankedStory
from bot.rephraser import (
    compose_expanded_tweet,
    fit_to_budget,
    tighten_text,
    twitter_weighted_length,
)


def make_story(title: str, source: str, cluster_headlines: list, is_breaking: bool = True) -> RankedStory:
    return RankedStory(
        title=title,
        source=source,
        link="https://x",
        published_paris="2026-07-05T14:00:00+02:00",
        cluster_size=len(cluster_headlines),
        cluster_sources=sorted({h["source"] for h in cluster_headlines}),
        cluster_headlines=cluster_headlines,
        is_breaking=is_breaking,
    )


class TestTighten:
    def test_strips_lead_in_and_recapitalizes(self):
        out = tighten_text("According to officials, the city is set to announce new measures in order to reduce traffic")
        assert out == "The city will announce new measures to reduce traffic"

    def test_stylized_brand_casing_preserved(self):
        out = tighten_text("iPhone sales up due to the fact that demand rose")
        assert out.startswith("iPhone")

    def test_clean_headline_unchanged(self):
        title = "Putin visits military installation, vowing to take more of Ukraine"
        assert tighten_text(title) == title


class TestWeightedLength:
    def test_url_counts_as_23_chars(self):
        tweet = "Headline text " + "https://news.google.com/rss/articles/" + "A" * 200
        assert twitter_weighted_length(tweet) == len("Headline text ") + 23

    def test_no_url_counts_normally(self):
        assert twitter_weighted_length("Just plain text.") == len("Just plain text.")


class TestFitToBudget:
    def test_truncates_on_word_boundary_with_ellipsis(self):
        long_text = "word " * 100
        fitted = fit_to_budget(long_text.strip(), 50)
        assert len(fitted) <= 50
        assert fitted.endswith("…")

    def test_short_text_untouched(self):
        assert fit_to_budget("short", 288) == "short"


class TestComposeExpandedTweet:
    def test_multi_source_cluster_expands_with_attribution(self):
        story = make_story(
            "Massive earthquake strikes off Japan coast, tsunami warning issued",
            "Reuters",
            [
                {"title": "Massive earthquake strikes off Japan coast, tsunami warning issued", "source": "Reuters"},
                {"title": "Japan orders coastal evacuations after 7.4 magnitude quake", "source": "BBC"},
            ],
        )
        out = compose_expanded_tweet(story, char_budget=288)
        assert "(Reuters)" in out
        assert "Also reported" in out
        assert "(BBC)" in out
        assert len(out) <= 288

    def test_near_duplicate_alt_wording_skipped(self):
        story = make_story(
            "EU agrees new sanctions package on energy sector",
            "Reuters",
            [
                {"title": "EU agrees new sanctions package on energy sector", "source": "Reuters"},
                {"title": "EU agrees new sanctions package targeting energy sector", "source": "Politico"},
            ],
        )
        out = compose_expanded_tweet(story, char_budget=288)
        assert "Also reported" not in out  # one-word difference adds no info

    def test_single_source_gets_attribution_only(self):
        story = make_story(
            "As Christians are attacked in Israel, government shows little concern",
            "AP",
            [{"title": "As Christians are attacked in Israel, government shows little concern", "source": "AP"}],
        )
        out = compose_expanded_tweet(story, char_budget=288)
        assert out == "As Christians are attacked in Israel, government shows little concern (AP)"

    def test_tight_budget_drops_alt_lines_cleanly(self):
        story = make_story(
            "Massive earthquake strikes off Japan coast, tsunami warning issued",
            "Reuters",
            [
                {"title": "Massive earthquake strikes off Japan coast, tsunami warning issued", "source": "Reuters"},
                {"title": "Japan orders coastal evacuations after 7.4 magnitude quake", "source": "BBC"},
            ],
        )
        out = compose_expanded_tweet(story, char_budget=100)
        assert "Also reported" not in out
        assert len(out) <= 100
