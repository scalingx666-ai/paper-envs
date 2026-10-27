from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Paper:
    source: str
    source_id: str
    canonical_id: str
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    published_at: datetime
    updated_at: datetime
    version: int
    url: str
    doi: Optional[str] = None


@dataclass
class FetchResult:
    papers: list[Paper]
    total_results: int
    pages: int
    query: str


@dataclass
class UpsertOutcome:
    status: str  # new | updated | duplicate | suppressed
    canonical_id: str
    paper_id: Optional[int] = None
    matched_canonical_id: Optional[str] = None
    reason: Optional[str] = None
