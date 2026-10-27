from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .dedupe import author_surnames, near_duplicate_reason, normalize_title
from .models import Paper, UpsertOutcome
from .timeutil import from_iso, to_iso, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    doi TEXT,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    abstract TEXT NOT NULL,
    authors_json TEXT NOT NULL,
    categories_json TEXT NOT NULL,
    published_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL,
    url TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_seen_run INTEGER,
    notified_at TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_papers_title_norm ON papers(title_norm);
CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)
    WHERE doi IS NOT NULL AND doi != '';

CREATE TABLE IF NOT EXISTS paper_topics (
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (paper_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_topics_topic ON paper_topics(topic_id);

CREATE TABLE IF NOT EXISTS suppressed_duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT NOT NULL,
    matched_canonical_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    run_id INTEGER,
    UNIQUE (canonical_id, matched_canonical_id)
);

CREATE TABLE IF NOT EXISTS topic_state (
    topic_id TEXT PRIMARY KEY,
    watermark TEXT,
    last_run_at TEXT,
    last_success_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    topics_json TEXT NOT NULL,
    fetched INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    suppressed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""


@dataclass
class StoredPaper:
    paper: Paper
    topics: list[str]
    primary_topic: str | None
    first_seen_at: datetime


class Store:
    def __init__(self, path: str | Path, *, near_dup_days: int = 45):
        self.path = Path(path).expanduser()
        self.near_dup_days = near_dup_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start_run(self, topic_ids: Iterable[str]) -> int:
        cursor = self.conn.execute(
            "INSERT INTO runs (started_at, status, topics_json) VALUES (?, ?, ?)",
            (to_iso(utc_now()), "running", json.dumps(list(topic_ids))),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        *,
        fetched: int,
        inserted: int,
        updated: int,
        duplicates: int,
        suppressed: int,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE runs
            SET finished_at = ?, status = ?, fetched = ?, inserted = ?, updated = ?,
                duplicates = ?, suppressed = ?, error = ?
            WHERE id = ?
            """,
            (
                to_iso(utc_now()),
                status,
                fetched,
                inserted,
                updated,
                duplicates,
                suppressed,
                error,
                run_id,
            ),
        )
        self.conn.commit()

    def get_watermark(self, topic_id: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT watermark FROM topic_state WHERE topic_id = ?", (topic_id,)
        ).fetchone()
        if not row or not row["watermark"]:
            return None
        return from_iso(row["watermark"])

    def record_topic_success(self, topic_id: str, watermark: datetime | None) -> None:
        now = to_iso(utc_now())
        current = self.get_watermark(topic_id)
        if watermark is None:
            watermark = current
        elif current is not None:
            watermark = max(current, watermark)
        self.conn.execute(
            """
            INSERT INTO topic_state (topic_id, watermark, last_run_at, last_success_at, last_error)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(topic_id) DO UPDATE SET
                watermark = excluded.watermark,
                last_run_at = excluded.last_run_at,
                last_success_at = excluded.last_success_at,
                last_error = NULL
            """,
            (topic_id, to_iso(watermark) if watermark else None, now, now),
        )
        self.conn.commit()

    def record_topic_error(self, topic_id: str, error: str) -> None:
        now = to_iso(utc_now())
        self.conn.execute(
            """
            INSERT INTO topic_state (topic_id, watermark, last_run_at, last_success_at, last_error)
            VALUES (?, NULL, ?, NULL, ?)
            ON CONFLICT(topic_id) DO UPDATE SET
                last_run_at = excluded.last_run_at,
                last_error = excluded.last_error
            """,
            (topic_id, now, error),
        )
        self.conn.commit()

    def upsert(self, paper: Paper, topic_id: str, run_id: int | None = None) -> UpsertOutcome:
        now = to_iso(utc_now())
        existing_row = self.conn.execute(
            "SELECT * FROM papers WHERE canonical_id = ?", (paper.canonical_id,)
        ).fetchone()

        if existing_row is not None:
            paper_id = int(existing_row["id"])
            self._add_topic(paper_id, topic_id, primary=False)
            if paper.version > int(existing_row["version"]):
                self.conn.execute(
                    """
                    UPDATE papers
                    SET source_id = ?, doi = COALESCE(?, doi), title = ?, title_norm = ?,
                        abstract = ?, authors_json = ?, categories_json = ?, updated_at = ?,
                        version = ?, url = ?, last_seen_at = ?, raw_json = ?
                    WHERE id = ?
                    """,
                    (
                        paper.source_id,
                        paper.doi,
                        paper.title,
                        normalize_title(paper.title),
                        paper.abstract,
                        json.dumps(paper.authors, ensure_ascii=False),
                        json.dumps(paper.categories, ensure_ascii=False),
                        to_iso(paper.updated_at),
                        paper.version,
                        paper.url,
                        now,
                        self._raw_json(paper),
                        paper_id,
                    ),
                )
                self.conn.commit()
                return UpsertOutcome("updated", paper.canonical_id, paper_id=paper_id)
            self.conn.execute(
                "UPDATE papers SET last_seen_at = ? WHERE id = ?", (now, paper_id)
            )
            self.conn.commit()
            return UpsertOutcome("duplicate", paper.canonical_id, paper_id=paper_id)

        matched = self._find_duplicate(paper)
        if matched is not None:
            existing, reason = matched
            self.conn.execute(
                """
                INSERT OR IGNORE INTO suppressed_duplicates
                    (canonical_id, matched_canonical_id, reason, seen_at, run_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (paper.canonical_id, existing.canonical_id, reason, now, run_id),
            )
            self.conn.commit()
            return UpsertOutcome(
                "suppressed",
                paper.canonical_id,
                matched_canonical_id=existing.canonical_id,
                reason=reason,
            )

        cursor = self.conn.execute(
            """
            INSERT INTO papers (
                canonical_id, source, source_id, doi, title, title_norm, abstract,
                authors_json, categories_json, published_at, updated_at, version,
                url, first_seen_at, last_seen_at, first_seen_run, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper.canonical_id,
                paper.source,
                paper.source_id,
                paper.doi,
                paper.title,
                normalize_title(paper.title),
                paper.abstract,
                json.dumps(paper.authors, ensure_ascii=False),
                json.dumps(paper.categories, ensure_ascii=False),
                to_iso(paper.published_at),
                to_iso(paper.updated_at),
                paper.version,
                paper.url,
                now,
                now,
                run_id,
                self._raw_json(paper),
            ),
        )
        paper_id = int(cursor.lastrowid)
        self._add_topic(paper_id, topic_id, primary=True)
        self.conn.commit()
        return UpsertOutcome("new", paper.canonical_id, paper_id=paper_id)

    def _find_duplicate(self, paper: Paper) -> tuple[Paper, str] | None:
        if paper.doi:
            row = self.conn.execute(
                "SELECT * FROM papers WHERE lower(doi) = lower(?) LIMIT 1", (paper.doi,)
            ).fetchone()
            if row is not None:
                existing = self._row_to_paper(row)
                reason = near_duplicate_reason(paper, existing) or "doi"
                return existing, reason

        title_norm = normalize_title(paper.title)
        rows = self.conn.execute(
            "SELECT * FROM papers WHERE title_norm = ? LIMIT 20", (title_norm,)
        ).fetchall()
        for row in rows:
            existing = self._row_to_paper(row)
            reason = near_duplicate_reason(paper, existing)
            if reason:
                return existing, reason

        surnames = author_surnames(paper.authors)
        if not surnames:
            return None
        cutoff = to_iso(paper.published_at - timedelta(days=self.near_dup_days))
        needle = f'"{surnames[0]}"'
        rows = self.conn.execute(
            """
            SELECT * FROM papers
            WHERE published_at >= ? AND instr(lower(authors_json), ?) > 0
            ORDER BY published_at DESC
            LIMIT 200
            """,
            (cutoff, needle),
        ).fetchall()
        for row in rows:
            existing = self._row_to_paper(row)
            reason = near_duplicate_reason(paper, existing)
            if reason:
                return existing, reason
        return None

    def _add_topic(self, paper_id: int, topic_id: str, *, primary: bool) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO paper_topics (paper_id, topic_id, is_primary)
            VALUES (?, ?, ?)
            """,
            (paper_id, topic_id, 1 if primary else 0),
        )

    @staticmethod
    def _raw_json(paper: Paper) -> str:
        return json.dumps(
            {
                "source": paper.source,
                "source_id": paper.source_id,
                "canonical_id": paper.canonical_id,
                "doi": paper.doi,
                "title": paper.title,
                "authors": paper.authors,
                "categories": paper.categories,
                "published_at": to_iso(paper.published_at),
                "updated_at": to_iso(paper.updated_at),
                "version": paper.version,
                "url": paper.url,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _row_to_paper(row: sqlite3.Row) -> Paper:
        return Paper(
            source=row["source"],
            source_id=row["source_id"],
            canonical_id=row["canonical_id"],
            title=row["title"],
            abstract=row["abstract"],
            authors=json.loads(row["authors_json"]),
            categories=json.loads(row["categories_json"]),
            published_at=from_iso(row["published_at"]),
            updated_at=from_iso(row["updated_at"]),
            version=int(row["version"]),
            url=row["url"],
            doi=row["doi"],
        )

    def get_unnotified(self) -> list[StoredPaper]:
        rows = self.conn.execute(
            "SELECT * FROM papers WHERE notified_at IS NULL ORDER BY published_at DESC"
        ).fetchall()
        return [self._to_stored(row) for row in rows]

    def get_published_since(self, since: datetime) -> list[StoredPaper]:
        rows = self.conn.execute(
            "SELECT * FROM papers WHERE published_at >= ? ORDER BY published_at DESC",
            (to_iso(since),),
        ).fetchall()
        return [self._to_stored(row) for row in rows]

    def earliest_published(self) -> datetime | None:
        row = self.conn.execute("SELECT MIN(published_at) AS value FROM papers").fetchone()
        return from_iso(row["value"]) if row and row["value"] else None

    def get_paper(self, canonical_id: str) -> StoredPaper | None:
        row = self.conn.execute(
            "SELECT * FROM papers WHERE canonical_id = ?", (canonical_id,)
        ).fetchone()
        return self._to_stored(row) if row else None

    def _to_stored(self, row: sqlite3.Row) -> StoredPaper:
        topic_rows = self.conn.execute(
            """
            SELECT topic_id, is_primary FROM paper_topics
            WHERE paper_id = ?
            ORDER BY is_primary DESC, topic_id
            """,
            (row["id"],),
        ).fetchall()
        topics = [topic["topic_id"] for topic in topic_rows]
        primary = next((topic["topic_id"] for topic in topic_rows if topic["is_primary"]), None)
        return StoredPaper(
            paper=self._row_to_paper(row),
            topics=topics,
            primary_topic=primary,
            first_seen_at=from_iso(row["first_seen_at"]),
        )

    def mark_notified(self, canonical_ids: Iterable[str]) -> None:
        ids = list(canonical_ids)
        if not ids:
            return
        now = to_iso(utc_now())
        placeholders = ",".join("?" for _ in ids)
        self.conn.execute(
            f"UPDATE papers SET notified_at = ? WHERE canonical_id IN ({placeholders})",
            [now, *ids],
        )
        self.conn.commit()

    def stats(self) -> dict[str, object]:
        total = self.conn.execute("SELECT COUNT(*) AS n FROM papers").fetchone()["n"]
        unnotified = self.conn.execute(
            "SELECT COUNT(*) AS n FROM papers WHERE notified_at IS NULL"
        ).fetchone()["n"]
        suppressed = self.conn.execute(
            "SELECT COUNT(*) AS n FROM suppressed_duplicates"
        ).fetchone()["n"]
        topics = self.conn.execute(
            """
            SELECT topic_id, COUNT(*) AS n FROM paper_topics
            GROUP BY topic_id ORDER BY topic_id
            """
        ).fetchall()
        states = self.conn.execute(
            "SELECT * FROM topic_state ORDER BY topic_id"
        ).fetchall()
        runs = self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 5"
        ).fetchall()
        return {
            "papers": int(total),
            "unnotified": int(unnotified),
            "suppressed": int(suppressed),
            "topics": [dict(row) for row in topics],
            "states": [dict(row) for row in states],
            "runs": [dict(row) for row in runs],
        }
