from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from paper_radar.config import load_config
from paper_radar.export import render_table, write_exports
from paper_radar.models import Paper
from paper_radar.store import StoredPaper

CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)


def make_stored(
    title: str = "Flat-RLVR: Sharpness-Aware RL",
    abstract: str = "We study RLVR with a verifiable reward.",
    canonical_id: str = "arxiv:2610.00002",
) -> StoredPaper:
    paper = Paper(
        source="arxiv",
        source_id=f"{canonical_id.split(':')[1]}v1",
        canonical_id=canonical_id,
        title=title,
        abstract=abstract,
        authors=["Alice Kim", "Bob Lee"],
        categories=["cs.LG"],
        published_at=T0,
        updated_at=T0,
        version=1,
        url=f"https://arxiv.org/abs/{canonical_id.split(':')[1]}v1",
    )
    return StoredPaper(
        paper=paper,
        topics=["post-training"],
        primary_topic="post-training",
        first_seen_at=T0,
    )


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG)
        self.papers = [make_stored()]

    def test_render_table_has_link_and_abstract(self) -> None:
        text = render_table(
            self.config,
            self.papers,
            datetime(2026, 10, 20, tzinfo=timezone.utc),
            datetime(2026, 10, 25, tzinfo=timezone.utc),
        )
        self.assertIn("后训练 / Post-training", text)
        self.assertIn("(https://arxiv.org/abs/2610.00002v1)", text)
        self.assertIn("We study RLVR with a verifiable reward.", text)

    def test_low_score_paper_is_only_in_csv(self) -> None:
        weak = make_stored(
            title="A Survey of Cooking",
            abstract="We mention training once.",
            canonical_id="arxiv:2610.00003",
        )
        papers = [*self.papers, weak]
        text = render_table(
            self.config,
            papers,
            datetime(2026, 10, 20, tzinfo=timezone.utc),
            datetime(2026, 10, 25, tzinfo=timezone.utc),
        )
        self.assertNotIn("A Survey of Cooking", text)
        with tempfile.TemporaryDirectory() as tmp:
            _, csv_path = write_exports(
                self.config,
                papers,
                datetime(2026, 10, 20, tzinfo=timezone.utc),
                datetime(2026, 10, 25, tzinfo=timezone.utc),
                tmp,
                "survey-test",
            )
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = {row["title"]: row for row in csv.DictReader(handle)}
        self.assertEqual(rows["A Survey of Cooking"]["selected"], "0")
        self.assertEqual(rows["Flat-RLVR: Sharpness-Aware RL"]["selected"], "1")

    def test_write_exports_creates_markdown_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md_path, csv_path = write_exports(
                self.config,
                self.papers,
                datetime(2026, 10, 20, tzinfo=timezone.utc),
                datetime(2026, 10, 25, tzinfo=timezone.utc),
                tmp,
                "survey-test",
            )
            self.assertTrue(md_path.exists())
            self.assertTrue(csv_path.exists())
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["topic"], "post-training")
            self.assertEqual(rows[0]["url"], "https://arxiv.org/abs/2610.00002v1")
            self.assertIn("RLVR", rows[0]["abstract"])


if __name__ == "__main__":
    unittest.main()
