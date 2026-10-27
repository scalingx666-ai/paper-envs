from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from paper_radar.config import load_config
from paper_radar.digest import render_digest, write_digest
from paper_radar.models import Paper
from paper_radar.store import StoredPaper

CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)


class DigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG)

    def test_empty_digest(self) -> None:
        text = render_digest(
            self.config,
            [],
            datetime(2026, 10, 22, tzinfo=timezone.utc),
            datetime(2026, 10, 25, tzinfo=timezone.utc),
            {"inserted": 0},
        )
        self.assertIn("本次窗口没有新论文", text)

    def test_renders_paper(self) -> None:
        paper = Paper(
            source="arxiv",
            source_id="2610.29281v1",
            canonical_id="arxiv:2610.29281",
            title="Flat-RLVR: Sharpness-Aware RL",
            abstract="We study RLVR.",
            authors=["Yeonbum Kim"],
            categories=["cs.LG"],
            published_at=T0,
            updated_at=T0,
            version=1,
            url="https://arxiv.org/abs/2610.29281v1",
        )
        stored = StoredPaper(
            paper=paper,
            topics=["post-training"],
            primary_topic="post-training",
            first_seen_at=T0,
        )
        text = render_digest(
            self.config,
            [stored],
            datetime(2026, 10, 22, tzinfo=timezone.utc),
            datetime(2026, 10, 25, tzinfo=timezone.utc),
            {"inserted": 1},
        )
        self.assertIn("后训练 / Post-training", text)
        self.assertIn("Flat-RLVR", text)
        self.assertIn("https://arxiv.org/abs/2610.29281v1", text)

    def test_empty_digest_keeps_previous_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest.md"
            latest.write_text("previous digest", encoding="utf-8")
            write_digest(tmp, "# arXiv Paper Radar\n\n本次窗口没有新论文。\n")
            self.assertEqual(latest.read_text(encoding="utf-8"), "previous digest")


if __name__ == "__main__":
    unittest.main()
