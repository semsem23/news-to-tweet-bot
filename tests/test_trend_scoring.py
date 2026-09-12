"""Tests for local trend scoring (bot/trend_scoring.py)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from bot.config import DEDUP_LOOKBACK_HOURS
from bot.models import PostedEntry, RankedStory
from bot.trend_scoring import analyze_article, annotate_trend_scores, apply_trend_scoring

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


class TestCalibrationRegressions:
	"""Locks in the 2026-09-12 calibration against 706 posted tweets joined to
	their real impressions (account_analytics_content_2026-06-28_2026-08-19).

	Each case below is a real post from that sample where the pre-calibration
	weights ranked the headline in the opposite direction from its actual
	reach. Baseline median across the sample was 21 impressions.
	"""

	# (headline, real impressions, trend_score under the OLD weights)
	WATCH_LIVE_TOP = (
		"Watch Live: Lindsay Clancy trial jury hears testimony from medical examiner",
		1225, 0.076,
	)
	WATCH_LIVE_2 = (
		"Watch Live: Prosecution rests in Lindsay Clancy murder trial, defense set to call witness",
		418, 0.503,
	)
	GEO_LIVEBLOG_FLOP = (
		"Ukraine-Russia war latest: Zelensky mocks Putin’s reliance on North Korea after deadly strike",
		15, 0.723,
	)
	GEO_KHAMENEI_FLOP = (
		"Iran's Supreme Leader Khamenei is dead — killed in war. A multi-day state funeral begins",
		39, 0.664,
	)
	CELEB_HIT = (
		"Taylor Swift and Travis Kelce donated $26 million to 20 charities just before their wedding",
		335, 0.111,
	)
	QUESTION_FLOP = ("Is the housing market about to turn?", 13, None)

	def test_watch_live_outranks_top_geopolitical_liveblog(self):
		"""The sharpest divergence in the sample: the old weights scored a
		1225-impression 'Watch Live' headline at 0.076 (bottom decile) and a
		15-impression Ukraine liveblog at 0.723 (the single highest score)."""
		watch = analyze_article(self.WATCH_LIVE_TOP[0])
		liveblog = analyze_article(self.GEO_LIVEBLOG_FLOP[0])

		assert watch["watch_live"] is True
		assert watch["trend_score"] > liveblog["trend_score"]

	def test_watch_live_beats_khamenei_geopolitical_pileup(self):
		"""39 impressions but the 4th-highest old trend_score (0.664), because
		geo keywords (iran, khamenei-adjacent, war) stacked up."""
		watch = analyze_article(self.WATCH_LIVE_2[0])
		geo = analyze_article(self.GEO_KHAMENEI_FLOP[0])

		assert watch["trend_score"] > geo["trend_score"]

	def test_geopolitical_keyword_pileup_no_longer_dominates(self):
		"""geo_hot_count is still reported as a diagnostic, but with
		TREND_WEIGHTS['geo'] == 0.0 it must not move the score on its own."""
		plain = analyze_article("Council approves budget for the coming year")
		geo_heavy = analyze_article(
			"Council approves budget for the coming year "
			"israel gaza iran putin ukraine russia china taiwan"
		)

		assert geo_heavy["geo_hot_count"] >= 8
		assert geo_heavy["trend_score"] == plain["trend_score"]

	def test_celebrity_headline_is_rewarded(self):
		"""335 impressions (16x baseline) but 0.111 under the old weights,
		which had no celebrity/sports keywords at all."""
		result = analyze_article(self.CELEB_HIT[0])

		assert result["celebrity_detected"] is True
		assert result["trend_score"] > 0.111

	def test_question_headline_is_penalised(self):
		"""Question framing ran at 0.62x baseline across the sample."""
		question = analyze_article(self.QUESTION_FLOP[0])
		statement = analyze_article("The housing market is about to turn")

		assert question["question_headline"] is True
		assert question["trend_score"] < statement["trend_score"]

	def test_watch_live_and_live_blog_are_mutually_exclusive(self):
		"""A 'Watch Live' headline must score in the strong bucket only —
		the two weights must never stack."""
		result = analyze_article("Watch Live: live updates from the courtroom")

		assert result["watch_live"] is True
		assert result["live_blog"] is False

	def test_watch_live_outranks_plain_liveblog(self):
		"""19.1x vs 1.95x baseline respectively — the gap must be preserved."""
		watch = analyze_article("Watch Live: Senate hearing on the budget")
		blog = analyze_article("Live updates: Senate hearing on the budget")

		assert watch["trend_score"] > blog["trend_score"]

	def test_format_signals_do_not_fire_from_cluster_content(self):
		"""Format markers are headline-only: a sibling cluster headline that
		happens to say 'Watch Live' must not promote this story."""
		result = analyze_article(
			"Senate passes the budget bill",
			"Watch Live: Senate floor coverage | Live updates from the chamber",
		)

		assert result["watch_live"] is False
		assert result["live_blog"] is False

	def test_added_violence_keywords_are_detected(self):
		"""'shooting'/'wounded' were missing from the old DEATH_KEYWORDS."""
		result = analyze_article(
			"‘Pure chaos’: Mass shooting at Seattle Center leaves 2 dead, 5 wounded"
		)

		assert result["death_detected"] is True

	def test_analytical_framing_is_penalised(self):
		analytical = analyze_article("Analysis: what the budget vote means for the midterms")
		plain = analyze_article("Budget vote passes ahead of the midterms")

		assert analytical["analytical_headline"] is True
		assert analytical["trend_score"] < plain["trend_score"]


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


class TestShadowMode:
	"""annotate_trend_scores is the shadow-mode entry point: it must record a
	score for every candidate while leaving the ranking completely alone, so
	the score can be validated against real impressions before it is trusted
	to reorder anything."""

	def test_every_candidate_is_scored_including_the_head(self):
		"""The posted story is whichever candidate clears the duplicate check
		— often the head. An unscored head would leave holes in the sample."""
		ranked = [
			make_story("Head story about a calm trade summit"),
			make_story("Deadly missile attack kills dozens"),
			make_story("Central bank holds interest rates steady"),
		]

		annotate_trend_scores(ranked)

		assert all("trend_score" in s.score_breakdown for s in ranked)

	def test_order_is_not_changed(self):
		head = make_story("Calm headline about trade talks")
		violent = make_story("Deadly bombing kills dozens in attack")
		neutral = make_story("Central bank holds interest rates steady")
		ranked = [head, violent, neutral]

		result = annotate_trend_scores(ranked)

		assert result == [head, violent, neutral]
		assert [s.title for s in ranked] == [head.title, violent.title, neutral.title]

	def test_scores_match_apply_trend_scoring(self):
		"""Shadow scores must be the same numbers the live re-ranking would
		use, or the calibration sample would not describe the real behaviour."""
		title = "BREAKING: deadly missile attack kills dozens in Israel Gaza"
		shadow = make_story(title)
		annotate_trend_scores([shadow])

		expected = analyze_article(title, "")["trend_score"]

		assert shadow.score_breakdown["trend_score"] == expected

	def test_empty_list_is_safe(self):
		assert annotate_trend_scores([]) == []


class TestAnalyticsLog:
	def test_append_writes_one_json_line_per_post(self, tmp_path):
		from bot import history as history_module

		log_path = tmp_path / "posted_analytics.jsonl"
		entry = PostedEntry(
			link="https://example.com/a",
			title="Deadly missile attack kills dozens",
			posted_at=datetime.now(timezone.utc).isoformat(),
			tweet_id="123",
			source="Reuters",
			score=0.95,
			trend_score=0.62,
		)
		story = make_story("Deadly missile attack kills dozens", trend_score=0.62)

		with patch.object(history_module, "POST_ANALYTICS_PATH", log_path):
			history_module.append_analytics(entry, story=story, rank_position=2, candidate_count=5)
			history_module.append_analytics(entry, story=story, rank_position=1, candidate_count=5)

		lines = log_path.read_text(encoding="utf-8").strip().splitlines()
		assert len(lines) == 2

		record = json.loads(lines[0])
		assert record["tweet_id"] == "123"
		assert record["trend_score"] == 0.62
		assert record["rank_position"] == 2
		assert record["candidate_count"] == 5
		assert "score_breakdown" in record

	def test_append_survives_an_unwritable_path(self, tmp_path):
		"""The tweet is already posted by the time this runs — a logging
		failure must not raise and trigger a retry of an existing post."""
		from bot import history as history_module

		unwritable = tmp_path / "a-file-not-a-dir" / "nested" / "out.jsonl"
		unwritable.parent.parent.write_text("I am a file, not a directory")

		entry = PostedEntry(
			link="https://example.com/a",
			title="Story",
			posted_at=datetime.now(timezone.utc).isoformat(),
		)

		with patch.object(history_module, "POST_ANALYTICS_PATH", unwritable):
			history_module.append_analytics(entry)  # must not raise

	def test_analytics_log_is_not_pruned(self, tmp_path):
		"""The whole point of the second file: prune_history drops entries
		older than DEDUP_LOOKBACK_HOURS, the analytics log keeps them."""
		from bot import history as history_module

		log_path = tmp_path / "posted_analytics.jsonl"
		old = datetime.now(timezone.utc) - timedelta(hours=DEDUP_LOOKBACK_HOURS + 24)
		entry = PostedEntry(link="https://example.com/old", title="Old", posted_at=old.isoformat())

		with patch.object(history_module, "POST_ANALYTICS_PATH", log_path):
			history_module.append_analytics(entry)

		assert history_module.prune_history([entry], datetime.now(timezone.utc)) == []
		assert len(log_path.read_text(encoding="utf-8").strip().splitlines()) == 1
