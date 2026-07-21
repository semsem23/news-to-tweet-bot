"""Tests for clustering, scoring, and freshness enforcement."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.ranker import (
    cluster_articles,
    headline_style_penalty,
    jaccard,
    rank_articles,
    tokenize,
    topic_penalty,
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


class TestTopicPenalty:
    def test_political_topic_penalized(self):
        """Headlines about politics should be penalized."""
        assert topic_penalty("Senate passes new healthcare bill") < 1.0

    def test_political_topic_with_congress(self):
        """Congress keyword triggers penalty."""
        assert topic_penalty("Congress debates infrastructure spending") < 1.0

    def test_political_topic_with_election(self):
        """Election keyword triggers penalty."""
        assert topic_penalty("Presidential election results announced today") < 1.0

    def test_political_topic_with_parliament(self):
        """Parliament keyword triggers penalty."""
        assert topic_penalty("UK parliament votes on new legislation") < 1.0

    def test_non_political_headline_no_penalty(self):
        """Non-political headlines should not be penalized."""
        assert topic_penalty("Massive earthquake strikes off Japan coast") == 1.0

    def test_non_political_business_headline(self):
        """Business headlines without political keywords should not be penalized."""
        assert topic_penalty("Tech company announces new AI product") == 1.0

    def test_non_political_sports_headline(self):
        """Sports headlines should not be penalized."""
        assert topic_penalty("Team wins championship in overtime thriller") == 1.0

    def test_multiple_political_keywords(self):
        """Multiple political keywords should still trigger penalty (once)."""
        assert topic_penalty("President announces new senate legislation") < 1.0

    def test_case_insensitive_matching(self):
        """Political keyword matching should be case-insensitive."""
        assert topic_penalty("SENATE PASSES MAJOR REFORM BILL") < 1.0

    def test_stemmed_keyword_matching(self):
        """Keyword matching should respect stemming (e.g., 'elect' for 'election')."""
        assert topic_penalty("Election results show tight race") < 1.0

    def test_penalty_multiplier_value(self):
        """Politics penalty should be 0.45."""
        penalty = topic_penalty("Senate approves new bill")
        assert penalty == 0.45

    def test_empty_title_returns_one(self):
        """Empty title should return no penalty."""
        assert topic_penalty("") == 1.0

    def test_stopwords_only_title_returns_one(self):
        """Title with only stopwords should return no penalty."""
        assert topic_penalty("the and or in of") == 1.0

    def test_political_headline_affects_score(self):
        """Political story with same metrics as non-political should rank lower."""
        political = [
            art("Senate approves new tax bill after debate", "Reuters", 0.5, "https://p1"),
            art("Senate confirms new tax bill in historic vote", "AP", 0.4, "https://p2"),
            art("Senate passes major tax reform bill today", "Bloomberg", 0.6, "https://p3"),
        ]
        non_political = [
            art("Major earthquake strikes off Japan coast", "Reuters", 0.5, "https://n1"),
            art("Massive quake hits Japan triggering tsunami", "AP", 0.4, "https://n2"),
            art("Japan hit by major earthquake and tsunami", "Bloomberg", 0.6, "https://n3"),
        ]
        top_political = rank_articles(political, top_n=1)
        top_non_political = rank_articles(non_political, top_n=1)
        # Same cluster size (3), same freshness, same source prominence, but political penalized
        assert top_political[0].score < top_non_political[0].score


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
