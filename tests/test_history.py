"""Tests for the dedup history store."""

from datetime import datetime, timedelta, timezone

from bot.history import is_duplicate, pick_non_duplicate, prune_history
from bot.models import PostedEntry, RankedStory


def make_story(title: str, link: str) -> RankedStory:
    return RankedStory(
        title=title, source="Reuters", link=link,
        published_paris="2026-07-05T14:00:00+02:00",
        cluster_size=1, cluster_sources=["Reuters"],
        cluster_headlines=[{"title": title, "source": "Reuters"}],
    )


def entry(title: str, link: str, hours_ago: float = 1.0) -> PostedEntry:
    posted_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return PostedEntry(link=link, title=title, posted_at=posted_at)


class TestIsDuplicate:
    def test_exact_link_match(self):
        story = make_story("Some headline", "https://a")
        assert is_duplicate(story, [entry("Different wording entirely", "https://a")])

    def test_similar_title_different_link(self):
        story = make_story("Major earthquake strikes off the coast of Japan, tsunami warning issued", "https://a")
        hist = [entry("Tsunami warning issued after major earthquake hits off Japan coast", "https://b")]
        assert is_duplicate(story, hist)

    def test_unrelated_story_not_duplicate(self):
        story = make_story("Central bank holds interest rates steady", "https://a")
        hist = [entry("Major earthquake strikes off Japan coast", "https://b")]
        assert not is_duplicate(story, hist)


class TestPickNonDuplicate:
    def test_skips_duplicate_and_picks_next(self):
        s1 = make_story("Major earthquake strikes off Japan coast", "https://a")
        s2 = make_story("Central bank holds interest rates steady", "https://b")
        hist = [entry("Major earthquake strikes off Japan coast", "https://a")]
        picked = pick_non_duplicate([s1, s2], hist)
        assert picked is s2

    def test_returns_none_when_all_duplicates(self):
        s1 = make_story("Major earthquake strikes off Japan coast", "https://a")
        hist = [entry("Major earthquake strikes off Japan coast", "https://a")]
        assert pick_non_duplicate([s1], hist) is None


class TestPruneHistory:
    def test_old_entries_pruned(self):
        hist = [entry("Old story", "https://old", hours_ago=100), entry("Recent story", "https://new", hours_ago=1)]
        kept = prune_history(hist, datetime.now(timezone.utc))
        assert len(kept) == 1
        assert kept[0].link == "https://new"
