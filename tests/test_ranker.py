"""Tests for clustering, scoring, and freshness enforcement."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.ranker import (
    cluster_articles,
    headline_style_penalty,
    jaccard,
    rank_articles,
    tokenize,
)

PARIS = ZoneInfo("Europe/Paris")


def ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).astimezone(PARIS).isoformat()


def art(title: str, source: str, hours_ago: float, link: str = "https://x") -> dict:
    t = ts(hours_ago)
    return {"title": title, "source": source, "link": link, "published_utc": t, "published_paris": t}


class TestTokenizeAndSimilarity:
    def test_stemming_matches_morphological_variants(self):
        t1 = tokenize("EU leaders agree on new sanctions package targeting energy sector")
        t2 = tokenize("European Union agrees new sanctions package on energy sector")
        assert jaccard(t1, t2) >= 0.45  # must clear the clustering threshold

    def test_unrelated_titles_low_similarity(self):
        t1 = tokenize("Massive earthquake strikes off Japan coast")
        t2 = tokenize("Central bank holds interest rates steady")
        assert jaccard(t1, t2) < 0.2


class TestClustering:
    def test_same_story_different_wording_merges(self):
        articles = [
            art("Major earthquake strikes off the coast of Japan, tsunami warning issued", "Reuters", 0.5),
            art("Tsunami warning issued after major earthquake hits off Japan coast", "BBC", 0.4),
            art("Japan issues tsunami warning following major offshore earthquake", "Al Jazeera", 0.6),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_unrelated_stories_stay_separate(self):
        articles = [
            art("Major earthquake strikes off the coast of Japan", "Reuters", 0.5),
            art("Central bank holds interest rates steady amid inflation concerns", "Bloomberg", 1.0),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 2


class TestStylePenalty:
    def test_question_headline_penalized(self):
        assert headline_style_penalty("What is the messaging behind the funeral?") < 1.0

    def test_live_blog_penalized(self):
        assert headline_style_penalty("Live updates: mourning in Tehran") < 1.0

    def test_listicle_penalized(self):
        assert headline_style_penalty("5 things to know about the summit") < 1.0

    def test_hard_news_untouched(self):
        assert headline_style_penalty("Putin visits military installation, vowing to take more of Ukraine") == 1.0

    def test_hard_news_beats_fresher_explainer(self):
        articles = [
            art("What is the religious and political messaging behind the funeral?", "Al Jazeera", 0.2, "https://a"),
            art("Funeral draws hundreds of thousands to Tehran streets", "Reuters", 0.5, "https://b"),
        ]
        top = rank_articles(articles, top_n=2)
        assert top[0].title.startswith("Funeral draws")


class TestFreshnessEnforcement:
    def test_fresh_lower_score_story_promoted_over_stale_high_score(self):
        articles = [
            # High score (3-source cluster, top sources) but ~2h old
            art("World leaders reach historic climate accord at summit", "Reuters", 2.0, "https://x1"),
            art("Historic climate accord reached by world leaders at summit", "BBC", 2.2, "https://x2"),
            art("Summit ends with world leaders striking historic climate accord", "AP", 1.9, "https://x3"),
            # Lower score (single low-tier source) but 20 min old
            art("Small plane makes emergency landing near downtown airport", "Regional News Network", 0.33, "https://y1"),
        ]
        top = rank_articles(articles, top_n=5)
        assert top[0].title.startswith("Small plane")
        assert top[0].age_hours < 1.0

    def test_no_fresh_story_falls_back_to_score_order(self):
        articles = [
            art("Central bank holds interest rates steady", "Reuters", 8.0, "https://z1"),
            art("Regional election results confirm ruling party majority", "BBC", 12.0, "https://z2"),
        ]
        top = rank_articles(articles, top_n=5)
        assert top[0].title.startswith("Central bank")  # highest score wins
