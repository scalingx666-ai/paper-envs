"""Load selected papers from the paper-radar survey CSV and group by topic."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from config import SURVEY_CSV, TOPICS


@dataclass
class Paper:
    arxiv_id: str
    title: str
    abstract: str
    authors: list[str]
    topic: str
    topics: list[str]
    score: int
    url: str
    published: str
    updated: str
    categories: list[str]
    selected: bool = True

    @property
    def arxiv_base(self) -> str:
        return self.arxiv_id.removeprefix("arxiv:")

    @property
    def slug(self) -> str:
        return make_slug(self.title, self.arxiv_base)

    @property
    def year(self) -> str:
        return self.published[:4] if self.published else ""

    def to_dict(self) -> dict:
        return {
            "arxiv_id": self.arxiv_id,
            "arxiv_base": self.arxiv_base,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "topic": self.topic,
            "topic_name": TOPICS.get(self.topic, self.topic),
            "topics": self.topics,
            "score": self.score,
            "url": self.url,
            "published": self.published,
            "updated": self.updated,
            "categories": self.categories,
            "slug": self.slug,
        }


_STOP_SLUG = {
    "a",
    "an",
    "the",
    "of",
    "for",
    "and",
    "or",
    "to",
    "in",
    "on",
    "with",
    "via",
    "toward",
    "towards",
    "from",
    "by",
    "is",
    "are",
}


def make_slug(title: str, arxiv_base: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    keep = [w for w in words if w not in _STOP_SLUG][:6]
    stem = "-".join(keep)[:40].strip("-") or "paper"
    suffix = re.sub(r"[^a-z0-9]+", "", arxiv_base.lower())[-6:] or "000000"
    return f"{stem}-{suffix}"


_IMPLEMENTATION_HINTS = (
    "we propose",
    "we introduce",
    "we present",
    "we develop",
    "we design",
    "we build",
    "framework",
    "system",
    "algorithm",
    "method",
    "efficient",
    "scalable",
    "implementation",
    "training",
    "inference",
    "serving",
    "scheduler",
    "cache",
    "agent",
    "environment",
    "pipeline",
)

_NON_IMPLEMENTATION = (
    "survey",
    "systematic review",
    "literature review",
    "position paper",
    "perspective",
    "call for papers",
    "editorial",
    "tutorial",
)


def _rank_score(row: dict, blob: str) -> float:
    score = float(row.get("score") or 0)
    bonus = sum(1.5 for hint in _IMPLEMENTATION_HINTS if hint in blob)
    penalty = sum(8.0 for hint in _NON_IMPLEMENTATION if hint in blob)
    abstract_len = len(row.get("abstract") or "")
    if abstract_len < 300:
        penalty += 6.0
    return score + bonus - penalty


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return date.min


def load_papers(
    csv_path: str | Path = SURVEY_CSV,
    *,
    limit_per_topic: int | None = None,
    only: list[str] | None = None,
    include_unselected: bool = False,
) -> list[Paper]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"survey csv not found: {path}")

    rows: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not include_unselected and row.get("selected") != "1":
                continue
            if row.get("topic") not in TOPICS:
                continue
            rows.append(row)

    candidates: dict[str, list[tuple[float, date, dict]]] = {t: [] for t in TOPICS}
    for row in rows:
        blob = f"{row.get('title', '')} {row.get('abstract', '')}".lower()
        candidates[row["topic"]].append(
            (_rank_score(row, blob), _parse_date(row.get("published", "")), row)
        )

    wanted = {item.strip().lower() for item in (only or []) if item.strip()}

    chosen: list[Paper] = []
    for topic in TOPICS:
        ranked = sorted(candidates[topic], key=lambda item: (item[0], item[1]), reverse=True)
        picked = 0
        for _, _, row in ranked:
            paper = Paper(
                arxiv_id=row["arxiv_id"].strip(),
                title=row["title"].strip(),
                abstract=row["abstract"].strip(),
                authors=[a.strip() for a in row["authors"].split(";") if a.strip()],
                topic=row["topic"].strip(),
                topics=[t.strip() for t in (row.get("all_topics") or "").split(";") if t.strip()],
                score=int(float(row.get("score") or 0)),
                url=row["url"].strip(),
                published=row.get("published", "").strip(),
                updated=row.get("updated", "").strip(),
                categories=[c.strip() for c in row.get("categories", "").split(";") if c.strip()],
            )
            if wanted and not (
                paper.arxiv_base.lower() in wanted
                or paper.arxiv_id.lower() in wanted
                or paper.slug in wanted
            ):
                continue
            chosen.append(paper)
            picked += 1
            if limit_per_topic is not None and picked >= limit_per_topic:
                break
        if wanted and picked == 0:
            # `only` may name a paper filed under a sibling topic; it will be
            # picked by that topic's pass instead.
            continue

    return chosen


def group_by_topic(papers: list[Paper]) -> dict[str, list[Paper]]:
    grouped: dict[str, list[Paper]] = {topic: [] for topic in TOPICS}
    for paper in papers:
        grouped.setdefault(paper.topic, []).append(paper)
    return grouped
