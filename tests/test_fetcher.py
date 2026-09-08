"""Tests for fetcher multi-feed support, deduplication, and headline filtering."""

from bot.fetcher import (
    compose_tweet_text,
    is_disqualified,
    merge_and_dedup_articles,
)
from bot.models import Article


class TestComposeTweetText:
    def test_compose_tweet_text(self):
        result = compose_tweet_text("Breaking news here", "Reuters")
        assert result == "Breaking news here (Reuters)"


class TestHeadlineFilters:
    def test_horoscope_excluded(self):
        is_disq, reason = is_disqualified("Horoscope for Saturday, August 22, 2026", "Astrology Today")
        assert is_disq
        assert "horoscope" in reason.lower()

    def test_horoscope_case_insensitive(self):
        is_disq, reason = is_disqualified("HOROSCOPE: Your week ahead", "Astrology")
        assert is_disq

    def test_horoscope_with_leading_quote(self):
        is_disq, reason = is_disqualified('"Horoscope for 2026', "Source")
        assert is_disq

    def test_question_ending_with_mark(self):
        is_disq, reason = is_disqualified(
            "What Made The CMF By Nothing Phones So Much Cheaper?", "Tech News"
        )
        assert is_disq
        assert "question mark" in reason.lower()

    def test_question_start_word(self):
        is_disq, reason = is_disqualified("Why is inflation still high", "Economics")
        assert is_disq
        assert "why" in reason.lower()

    def test_question_start_words_case_insensitive(self):
        for word in ["What", "How", "When", "Is", "Are", "Can", "Could", "Will"]:
            title = f"{word} happened yesterday?"
            is_disq, _ = is_disqualified(title, "Source")
            assert is_disq, f"Failed for word: {word}"

    def test_too_short_headline(self):
        # Less than 61 characters including source
        short = "Breaking news (TMZ)"  # 21 chars
        is_disq, reason = is_disqualified("Breaking news", "TMZ")
        assert is_disq
        assert "too short" in reason.lower()

    def test_exactly_61_chars_passes(self):
        # Exactly 61 characters should pass
        headline = "A" * 50
        source = "B" * 8  # "A"*50 + " (" + "B"*8 + ")" = 61 chars
        is_disq, _ = is_disqualified(headline, source)
        assert not is_disq

    def test_60_chars_fails(self):
        # 60 characters should fail
        headline = "A" * 50
        source = "B" * 7  # "A"*50 + " (" + "B"*7 + ")" = 60 chars
        is_disq, reason = is_disqualified(headline, source)
        assert is_disq
        assert "too short" in reason.lower()

    def test_normal_headline_passes(self):
        is_disq, reason = is_disqualified(
            "Major earthquake strikes off the coast of Japan, tsunami warning issued",
            "Reuters"
        )
        assert not is_disq

    def test_multiple_filters_horoscope_first(self):
        # Horoscope with question mark — should catch horoscope first
        is_disq, reason = is_disqualified("Horoscope: What will happen?", "Source")
        assert is_disq
        assert "horoscope" in reason.lower()


class TestMergeAndDedup:
    def test_single_feed_no_merge(self):
        articles = [
            Article(
                title="Breaking news", raw_title="Breaking news", source="Reuters",
                link="https://example.com/1", published_utc="2026-01-01T00:00:00+00:00",
                published_paris="2026-01-01T01:00:00+01:00", feed="WORLD", feeds=["WORLD"]
            ),
        ]
        result = merge_and_dedup_articles({"WORLD": articles})
        assert len(result) == 1
        assert result[0].feeds == ["WORLD"]

    def test_same_article_two_feeds(self):
        article1 = Article(
            title="Breaking news", raw_title="Breaking news", source="Reuters",
            link="https://example.com/1", published_utc="2026-01-01T00:00:00+00:00",
            published_paris="2026-01-01T01:00:00+01:00", feed="WORLD", feeds=["WORLD"]
        )
        article2 = Article(
            title="Breaking news", raw_title="Breaking news", source="Reuters",
            link="https://example.com/1", published_utc="2026-01-01T00:00:00+00:00",
            published_paris="2026-01-01T01:00:00+01:00", feed="NATION", feeds=["NATION"]
        )
        result = merge_and_dedup_articles({"WORLD": [article1], "NATION": [article2]})
        assert len(result) == 1
        assert set(result[0].feeds) == {"WORLD", "NATION"}

    def test_different_articles_stay_separate(self):
        article1 = Article(
            title="Breaking news", raw_title="Breaking news", source="Reuters",
            link="https://example.com/1", published_utc="2026-01-01T00:00:00+00:00",
            published_paris="2026-01-01T01:00:00+01:00", feed="WORLD", feeds=["WORLD"]
        )
        article2 = Article(
            title="Different story", raw_title="Different story", source="AP",
            link="https://example.com/2", published_utc="2026-01-01T00:00:00+00:00",
            published_paris="2026-01-01T01:00:00+01:00", feed="WORLD", feeds=["WORLD"]
        )
        result = merge_and_dedup_articles({"WORLD": [article1, article2]})
        assert len(result) == 2
