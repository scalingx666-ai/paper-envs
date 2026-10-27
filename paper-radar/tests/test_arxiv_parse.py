from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from paper_radar.arxiv_client import build_query, parse_feed

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_sample.xml"


class ArxivParseTests(unittest.TestCase):
    def test_parse_feed(self) -> None:
        papers, total = parse_feed(FIXTURE.read_bytes())
        self.assertEqual(total, 2)
        self.assertEqual(len(papers), 2)

        first = papers[0]
        self.assertEqual(first.canonical_id, "arxiv:2610.29281")
        self.assertEqual(first.source_id, "2610.29281v2")
        self.assertEqual(first.version, 2)
        self.assertEqual(first.title, "Flat-RLVR: Sharpness-Aware Reinforcement Learning")
        self.assertEqual(first.abstract, "We study RLVR. It is useful.")
        self.assertEqual(first.authors, ["Yeonbum Kim", "Hyun Kyu Lee"])
        self.assertEqual(first.categories, ["cs.LG", "cs.AI"])
        self.assertEqual(first.doi, "10.1234/example")
        self.assertEqual(first.url, "https://arxiv.org/abs/2610.29281v2")
        self.assertEqual(first.published_at.utcoffset().total_seconds(), 0)

    def test_build_query(self) -> None:
        query = build_query(
            "cat:cs.LG",
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 3, 12, 30, tzinfo=timezone.utc),
        )
        self.assertTrue(query.startswith("(cat:cs.LG)"))
        self.assertIn("submittedDate:[202610010000 TO 202610031230]", query)


if __name__ == "__main__":
    unittest.main()
