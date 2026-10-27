from __future__ import annotations

import re
from dataclasses import replace

from .config import Config, Topic
from .models import Paper
from .store import StoredPaper

_LLM_RE = re.compile(r"\bllms?\b|language models?", re.IGNORECASE)


def _contains(text: str, term: str) -> bool:
    return term.casefold() in text.casefold()


# 标题命中主题词比摘要命中重要得多：摘要里顺带提到的泛化词
# （quantization / inference / training）不应抢走一篇标题明确属于
# on-device 的论文。3:1 会被摘要词淹没，这里用 6:1。
_TITLE_HIT = 6
_ABSTRACT_HIT = 1


def score_topic(topic: Topic, paper: Paper) -> int:
    score = 0 if (topic.include_terms or topic.title_terms) else 1
    for term in topic.include_terms:
        if _contains(paper.title, term):
            score += _TITLE_HIT
        elif _contains(paper.abstract, term):
            score += _ABSTRACT_HIT
    for term in topic.title_terms:
        if _contains(paper.title, term):
            score += 1
    if _LLM_RE.search(paper.title):
        score += 1
    for term in topic.exclude_terms:
        if _contains(paper.title, term):
            score -= 10
        elif _contains(paper.abstract, term):
            score -= 3
    return score


def _title_include_hits(topic: Topic, paper: Paper) -> int:
    return sum(1 for term in topic.include_terms if _contains(paper.title, term))


def assign_best_topic(config: Config, stored: StoredPaper) -> tuple[Topic, int] | None:
    candidates = [
        (topic, score_topic(topic, stored.paper))
        for topic in config.topics
        if topic.id in stored.topics
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (item[1], _title_include_hits(item[0], stored.paper)),
    )


def select_for_digest(
    config: Config, papers: list[StoredPaper]
) -> tuple[list[StoredPaper], int]:
    """Pick the best-matching topic per paper and drop weak matches.

    Filtering happens after ingestion, so the SQLite store keeps every paper
    and the thresholds can be retuned without re-crawling.
    """
    selected: list[StoredPaper] = []
    filtered = 0
    for stored in papers:
        assigned = assign_best_topic(config, stored)
        if assigned is None:
            selected.append(stored)
            continue
        best_topic, best_score = assigned
        if best_score < best_topic.min_score:
            filtered += 1
            continue
        selected.append(replace(stored, primary_topic=best_topic.id))
    return selected, filtered
