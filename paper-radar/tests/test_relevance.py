from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from paper_radar.config import load_config
from paper_radar.models import Paper
from paper_radar.relevance import score_topic, select_for_digest
from paper_radar.store import StoredPaper

CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)


def make_paper(title: str, abstract: str) -> Paper:
    return Paper(
        source="arxiv",
        source_id="2610.00001v1",
        canonical_id="arxiv:2610.00001",
        title=title,
        abstract=abstract,
        authors=["Alice Kim"],
        categories=["cs.LG"],
        published_at=T0,
        updated_at=T0,
        version=1,
        url="https://arxiv.org/abs/2610.00001v1",
    )


def stored(paper: Paper, topics: list[str], primary: str) -> StoredPaper:
    return StoredPaper(
        paper=paper,
        topics=topics,
        primary_topic=primary,
        first_seen_at=T0,
    )


class RelevanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG)
        self.post = self.config.topic_by_id("post-training")

    def test_strong_title_match_passes(self) -> None:
        paper = make_paper(
            "Flat-RLVR: Sharpness-Aware Reinforcement Learning",
            "We study reinforcement learning with verifiable rewards.",
        )
        self.assertGreaterEqual(score_topic(self.post, paper), self.post.min_score)

    def test_single_abstract_hit_is_filtered(self) -> None:
        paper = make_paper(
            "PAIR: Preference-Aligned Intent Retrieval",
            "We use preference optimization for people recommendation at LinkedIn.",
        )
        selected, filtered = select_for_digest(
            self.config, [stored(paper, ["post-training"], "post-training")]
        )
        self.assertEqual(selected, [])
        self.assertEqual(filtered, 1)

    def test_best_topic_wins_for_cross_topic_paper(self) -> None:
        paper = make_paper(
            "Evaluating the Applicability of LLM Inference Optimizations on Apple Silicon",
            "We benchmark LLM inference and post-training quantization on Apple Silicon.",
        )
        selected, filtered = select_for_digest(
            self.config,
            [stored(paper, ["post-training", "inference", "on-device"], "post-training")],
        )
        self.assertEqual(filtered, 0)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].primary_topic, "on-device")

    def test_title_include_breaks_score_tie(self) -> None:
        paper = make_paper(
            "Local Inference of LLMs on Long Contexts",
            "We optimize KV cache, prefill, decode, quantization, and serving.",
        )
        selected, filtered = select_for_digest(
            self.config, [stored(paper, ["inference", "on-device"], "inference")]
        )
        self.assertEqual(filtered, 0)
        self.assertEqual(selected[0].primary_topic, "on-device")

    def test_exclude_terms_penalize(self) -> None:
        paper = make_paper(
            "LLM Post-Training for EEG Classification",
            "We apply RLVR to EEG signals.",
        )
        topic = self.config.topic_by_id("post-training")
        baseline = score_topic(topic, paper)
        penalized = score_topic(
            type(topic)(
                id=topic.id,
                name=topic.name,
                query=topic.query,
                max_results=topic.max_results,
                include_terms=topic.include_terms,
                title_terms=topic.title_terms,
                exclude_terms=("EEG",),
                min_score=topic.min_score,
            ),
            paper,
        )
        self.assertLess(penalized, baseline)


if __name__ == "__main__":
    unittest.main()
