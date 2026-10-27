from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from paper_radar.models import Paper
from paper_radar.store import Store

T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 22, tzinfo=timezone.utc)


def make_paper(
    *,
    canonical_id: str = "arxiv:2610.00001",
    title: str = "A Paper About Post-Training",
    authors: list[str] | None = None,
    version: int = 1,
    abstract: str = "A distinctive abstract about reinforcement learning with verifiable rewards.",
    doi: str | None = None,
) -> Paper:
    authors = authors or ["Alice Kim"]
    base = canonical_id.split(":", 1)[1]
    return Paper(
        source="arxiv",
        source_id=f"{base}v{version}" if version else base,
        canonical_id=canonical_id,
        title=title,
        abstract=abstract,
        authors=authors,
        categories=["cs.LG"],
        published_at=T0,
        updated_at=T0,
        version=version,
        url=f"https://arxiv.org/abs/{base}",
        doi=doi,
    )


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_new_then_version_then_duplicate(self) -> None:
        first = make_paper()
        self.assertEqual(self.store.upsert(first, "post-training", 1).status, "new")

        second = replace(first, version=2, source_id="2610.00001v2", updated_at=T1)
        self.assertEqual(self.store.upsert(second, "inference", 1).status, "updated")
        self.assertEqual(self.store.upsert(second, "post-training", 1).status, "duplicate")

        stored = self.store.get_paper(first.canonical_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.paper.version, 2)
        self.assertEqual(stored.topics, ["post-training", "inference"])
        self.assertEqual(len(self.store.get_unnotified()), 1)

    def test_same_title_same_author_different_id_is_suppressed(self) -> None:
        first = make_paper(canonical_id="arxiv:2610.00001")
        second = make_paper(canonical_id="arxiv:2610.00002")
        self.assertEqual(self.store.upsert(first, "post-training", 1).status, "new")

        outcome = self.store.upsert(second, "post-training", 1)
        self.assertEqual(outcome.status, "suppressed")
        self.assertEqual(outcome.matched_canonical_id, first.canonical_id)
        self.assertEqual(outcome.reason, "title_exact")

        stats = self.store.stats()
        self.assertEqual(stats["papers"], 1)
        self.assertEqual(stats["suppressed"], 1)

    def test_watermark_is_monotonic(self) -> None:
        self.store.record_topic_success("post-training", T1)
        self.store.record_topic_success("post-training", T0)
        self.assertEqual(self.store.get_watermark("post-training"), T1)

        self.store.record_topic_error("post-training", "boom")
        self.assertEqual(self.store.get_watermark("post-training"), T1)

    def test_mark_notified(self) -> None:
        paper = make_paper()
        self.store.upsert(paper, "post-training", 1)
        self.assertEqual(len(self.store.get_unnotified()), 1)
        self.store.mark_notified([paper.canonical_id])
        self.assertEqual(self.store.get_unnotified(), [])


if __name__ == "__main__":
    unittest.main()
