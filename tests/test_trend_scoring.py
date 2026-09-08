"""Tests for local trend scoring (bot/trend_scoring.py)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.models import RankedStory
from bot.trend_scoring import analyze_article, apply_trend_scoring

PARIS = ZoneInfo("Europe/Paris")


class TestAnalyzeArticle:
	def test_never_raises_on_empty_input(self):
		result = analyze_article("", "")
		assert 0.0 <= result["trend_score"] <= 1.25

	def test_death_keywords_raise_score_over_neutral_headline(self):
		neutral = analyze_article("Central bank holds interest rates steady")
		violent = analyze_article("Deadly missile strike kills dozens in attack")
		assert violent["trend_score"] > neutral["trend_score"]

	def test_geo_hot_count_detects_keywords(self):
		result = analyze_article("Israel and Gaza tensions rise as Hamas responds")
		assert result["geo_hot_count"] >= 2

	def test_urgency_detected_flag(self):
		result = analyze_article("BREAKING: developing story unfolding now")
		assert result["urgency_detected"] is True

	def test_score_is_bounded(self):
		text = "kill " * 500 + "israel gaza hamas iran putin " * 50
		result = analyze_article("terror attack", text)
		assert 0.0 <= result["trend_score"] <= 1.25


def make_story(title: str, trend_score: float | None = None, hours_ago: float = 1.0) -> RankedStory:
	published = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).astimezone(PARIS).isoformat()
	breakdown = {"feed_position": 0.9, "recency": 0.7, "prominence": 0.8, "repetition": 0.5, "style_penalty": 1.0}
	if trend_score is not None:
		breakdown["trend_score"] = trend_score
	return RankedStory(
		title=title,
		source="Reuters",
		link=f"https://example.com/{title[:20]}",
		published_paris=published,
		cluster_size=1,
		cluster_sources=["Reuters"],
		cluster_headlines=[{"title": title, "source": "Reuters"}],
		age_hours=hours_ago,
		score=0.8,
		score_breakdown=breakdown,
	)


class TestApplyTrendScoring:
	def test_position_one_is_never_moved(self):
		head = make_story("Calm headline about trade talks")
		violent_tail = make_story("Deadly bombing kills dozens in attack", hours_ago=3.0)
		ranked = [head, violent_tail]

		result = apply_trend_scoring(ranked)

		assert result[0] is head

	def test_single_candidate_is_returned_unchanged(self):
		only = make_story("Only story")
		assert apply_trend_scoring([only]) == [only]

	def test_empty_list_is_returned_unchanged(self):
		assert apply_trend_scoring([]) == []

	def test_tail_is_reordered_by_trend_score(self):
		head = make_story("Head story, untouched")
		low = make_story("Central bank holds interest rates steady", hours_ago=2.0)
		high = make_story("BREAKING: deadly missile attack kills dozens in Israel Gaza", hours_ago=2.0)
		ranked = [head, low, high]

		result = apply_trend_scoring(ranked)

		assert result[0] is head
		assert result[1] is high
		assert result[2] is low
