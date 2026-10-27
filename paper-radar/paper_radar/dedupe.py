from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Iterable

from .models import Paper

_VERSION_RE = re.compile(r"^(?P<base>.+?)(?:v(?P<version>\d+))?$")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "via",
        "with",
    }
)


def parse_arxiv_id(raw: str) -> tuple[str, int]:
    """Return (base_id, version) from an arXiv id or abs URL."""
    value = raw.strip()
    if "/abs/" in value:
        value = value.split("/abs/", 1)[1]
    if value.startswith("arxiv:"):
        value = value[len("arxiv:") :]
    value = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    match = _VERSION_RE.match(value)
    if not match or not match.group("base"):
        raise ValueError(f"cannot parse arXiv id: {raw!r}")
    version = int(match.group("version") or 0)
    return match.group("base"), version


def canonical_arxiv_id(base_id: str) -> str:
    return f"arxiv:{base_id}"


def normalize_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", title).lower()
    value = re.sub(r"\$[^$]*\$", " ", value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def title_tokens(title: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(normalize_title(title))
        if len(token) > 1 and token not in _STOPWORDS
    }


def abstract_shingles(abstract: str, size: int = 4) -> set[tuple[str, ...]]:
    tokens = _TOKEN_RE.findall(unicodedata.normalize("NFKC", abstract).lower())
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def jaccard(left: Iterable[object], right: Iterable[object]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    union = left_set | right_set
    return len(left_set & right_set) / len(union)


def author_surnames(authors: Iterable[str]) -> list[str]:
    surnames: list[str] = []
    for author in authors:
        parts = [part for part in re.split(r"\s+", author.strip()) if part]
        if parts:
            surnames.append(re.sub(r"[^\w]", "", parts[-1]).lower())
    return surnames


def same_first_author(left: Iterable[str], right: Iterable[str]) -> bool:
    left_surnames = author_surnames(left)
    right_surnames = author_surnames(right)
    if not left_surnames or not right_surnames:
        return False
    return left_surnames[0] == right_surnames[0]


def near_duplicate_reason(
    candidate: Paper,
    existing: Paper,
    *,
    title_fuzzy: float = 0.94,
    title_token_jaccard: float = 0.90,
    abstract_jaccard: float = 0.90,
) -> str | None:
    if candidate.canonical_id == existing.canonical_id:
        return "canonical_id"
    if candidate.doi and existing.doi and candidate.doi.lower() == existing.doi.lower():
        return "doi"

    candidate_title = normalize_title(candidate.title)
    existing_title = normalize_title(existing.title)
    same_author = same_first_author(candidate.authors, existing.authors)
    title_ratio = difflib.SequenceMatcher(None, candidate_title, existing_title).ratio()

    if candidate_title == existing_title and same_author:
        return "title_exact"
    if same_author and title_ratio >= title_fuzzy:
        return "title_fuzzy"
    if same_author and jaccard(title_tokens(candidate.title), title_tokens(existing.title)) >= title_token_jaccard:
        return "title_tokens"
    if same_author and title_ratio >= 0.45 and jaccard(
        abstract_shingles(candidate.abstract), abstract_shingles(existing.abstract)
    ) >= abstract_jaccard:
        return "abstract_shingles"
    return None
