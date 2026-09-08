"""Tests for PostedEntry's optional metadata fields."""

from bot.models import PostedEntry


class TestPostedEntryBackwardCompatibility:
	def test_loads_from_legacy_dict_missing_new_fields(self):
		legacy = {"link": "https://a", "title": "Old story", "posted_at": "2026-09-06T18:05:24+00:00"}
		entry = PostedEntry(**legacy)
		assert entry.tweet_id is None
		assert entry.source is None
		assert entry.score is None
		assert entry.trend_score is None

	def test_to_dict_includes_new_fields_as_none_for_legacy_entry(self):
		entry = PostedEntry(link="https://a", title="Old story", posted_at="2026-09-06T18:05:24+00:00")
		d = entry.to_dict()
		assert d["tweet_id"] is None
		assert d["source"] is None
		assert d["score"] is None
		assert d["trend_score"] is None


class TestPostedEntryNewFields:
	def test_round_trips_all_fields_through_to_dict(self):
		entry = PostedEntry(
			link="https://a", title="New story", posted_at="2026-09-08T18:05:24+00:00",
			tweet_id="123456", source="Reuters", score=0.87, trend_score=0.42,
		)
		d = entry.to_dict()
		assert d == {
			"link": "https://a",
			"title": "New story",
			"posted_at": "2026-09-08T18:05:24+00:00",
			"tweet_id": "123456",
			"source": "Reuters",
			"score": 0.87,
			"trend_score": 0.42,
		}
