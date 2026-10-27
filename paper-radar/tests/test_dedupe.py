from __future__ import annotations

import unittest
from datetime import datetime, timezone

from paper_radar.dedupe import (
    near_duplicate_reason,
    normalize_title,
    parse_arxiv_id,
)
from paper_radar.models import Paper

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def make_paper(
    *,
    canonical_id: str,
    title: str,
    authors: list[str],
    abstract: str = "A distinctive abstract about language model post-training.",
    doi: str | None = None,
) -> Paper:
    return Paper(
        source="arxiv",
        source_id=canonical_id.split(":", 1)[1],
        canonical_id=canonical_id,
        title=title,
        abstract=abstract,
        authors=authors,
        categories=["cs.LG"],
        published_at=NOW,
        updated_at=NOW,
        version=1,
        url=f"https://arxiv.org/abs/{canonical_id.split(':', 1)[1]}",
        doi=doi,
    )


class DedupeTests(unittest.TestCase):
    def test_parse_arxiv_id(self) -> None:
        self.assertEqual(parse_arxiv_id("2610.29281v2"), ("2610.29281", 2))
        self.assertEqual(parse_arxiv_id("https://arxiv.org/abs/2610.29281"), ("2610.29281", 0))
        self.assertEqual(parse_arxiv_id("hep-th/9901001v3"), ("hep-th/9901001", 3))

    def test_normalize_title(self) -> None:
        self.assertEqual(
            normalize_title("Flat-RLVR:  Sharpness-Aware\nReinforcement Learning"),
            "flat rlvr sharpness aware reinforcement learning",
        )

    def test_same_title_same_author_is_duplicate(self) -> None:
        left = make_paper(canonical_id="arxiv:1", title="Flat-RLVR: Sharpness-Aware RL", authors=["A Kim"])
        right = make_paper(canonical_id="arxiv:2", title="Flat RLVR — Sharpness Aware RL", authors=["A Kim"])
        self.assertEqual(near_duplicate_reason(left, right), "title_exact")

    def test_same_title_different_author_is_not_duplicate(self) -> None:
        left = make_paper(canonical_id="arxiv:1", title="A Generic Survey", authors=["A Kim"])
        right = make_paper(canonical_id="arxiv:2", title="A Generic Survey", authors=["B Lee"])
        self.assertIsNone(near_duplicate_reason(left, right))

    def test_different_papers_are_not_duplicates(self) -> None:
        left = make_paper(
            canonical_id="arxiv:1",
            title="KV Cache Compression for Long Context",
            authors=["A Kim"],
            abstract="We compress the key-value cache with low-rank projections.",
        )
        right = make_paper(
            canonical_id="arxiv:2",
            title="Speculative Decoding on Apple Silicon",
            authors=["A Kim"],
            abstract="We benchmark draft models on unified memory hardware.",
        )
        self.assertIsNone(near_duplicate_reason(left, right))

    def test_doi_match(self) -> None:
        left = make_paper(canonical_id="arxiv:1", title="One", authors=["A Kim"], doi="10.1/x")
        right = make_paper(canonical_id="arxiv:2", title="Two", authors=["B Lee"], doi="10.1/X")
        self.assertEqual(near_duplicate_reason(left, right), "doi")


if __name__ == "__main__":
    unittest.main()
