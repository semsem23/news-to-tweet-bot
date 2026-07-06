"""Dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Article:
    """A single item parsed from the RSS feed."""

    title: str
    raw_title: str
    source: str
    link: str
    published_utc: str
    published_paris: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RankedStory:
    """A clustered, scored story — one per underlying real-world event."""

    title: str
    source: str
    link: str
    published_paris: str
    cluster_size: int
    cluster_sources: list = field(default_factory=list)
    cluster_headlines: list = field(default_factory=list)  # [{"title", "source"}] — rep first
    age_hours: float = 0.0
    is_breaking: bool = False
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    tweet: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PostedEntry:
    """A record of a successfully posted story, for dedup."""

    link: str
    title: str
    posted_at: str  # ISO 8601 UTC

    def to_dict(self) -> dict:
        return {"link": self.link, "title": self.title, "posted_at": self.posted_at}
