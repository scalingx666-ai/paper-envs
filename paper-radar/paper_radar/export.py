from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .config import Config
from .relevance import assign_best_topic
from .store import StoredPaper


def _cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ").strip()


def _link_text(value: str) -> str:
    return _cell(value).replace("[", r"\[").replace("]", r"\]")


def _authors(authors: list[str], limit: int = 5) -> str:
    if len(authors) <= limit:
        return ", ".join(authors)
    return ", ".join(authors[:limit]) + ", et al."


def _rows(config: Config, papers: list[StoredPaper]) -> list[tuple[StoredPaper, str, int]]:
    rows: list[tuple[StoredPaper, str, int]] = []
    for stored in papers:
        assigned = assign_best_topic(config, stored)
        if assigned is None:
            topic_id = stored.primary_topic or "other"
            score = 0
        else:
            topic_id, score = assigned[0].id, assigned[1]
        rows.append((stored, topic_id, score))
    return rows


def _sort_key(row: tuple[StoredPaper, str, int]) -> tuple[int, float]:
    stored, _, score = row
    return (-score, -stored.paper.published_at.timestamp())


def render_table(
    config: Config,
    papers: list[StoredPaper],
    window_start: datetime,
    window_end: datetime,
) -> str:
    rows = _rows(config, papers)
    min_scores = {topic.id: topic.min_score for topic in config.topics}

    def selected(row: tuple[StoredPaper, str, int]) -> bool:
        return row[1] not in min_scores or row[2] >= min_scores[row[1]]

    selected_rows = [row for row in rows if selected(row)]
    ingested: dict[str, int] = {topic.id: 0 for topic in config.topics}
    grouped: dict[str, list[tuple[StoredPaper, str, int]]] = {
        topic.id: [] for topic in config.topics
    }
    other: list[tuple[StoredPaper, str, int]] = []
    for row in rows:
        if row[1] in ingested:
            ingested[row[1]] += 1
    for row in selected_rows:
        if row[1] in grouped:
            grouped[row[1]].append(row)
        else:
            other.append(row)

    lines = [
        "# arXiv 论文调研表",
        "",
        f"- 窗口（UTC）：{window_start:%Y-%m-%d} → {window_end:%Y-%m-%d}",
        f"- 入库唯一论文：**{len(rows)}** 篇（跨主题重复已合并，保留最佳主题）",
        f"- 表内展示：**{len(selected_rows)}** 篇（达到主题相关性阈值）",
        "- 收录规则：每个主题取 arXiv relevance 排序前 N 篇，再按 `min_score` 过滤；"
        "完整数据见同名 CSV 的 `selected` 列",
        "- 列说明：标题即 arXiv 链接；摘要为原文（已压缩空白）",
        "",
        "## 主题总览",
        "",
        "| 主题 | 表内 | 入库 |",
        "|:--|--:|--:|",
    ]
    for topic in config.topics:
        lines.append(
            f"| {_cell(topic.name)} | {len(grouped[topic.id])} | {ingested[topic.id]} |"
        )
    if other:
        lines.append(f"| 其他 | {len(other)} | {len(rows) - sum(ingested.values())} |")

    for topic in config.topics:
        entries = sorted(grouped[topic.id], key=_sort_key)
        lines.extend(
            [
                "",
                f"## {topic.name}（表内 {len(entries)} / 入库 {ingested[topic.id]} 篇）",
                "",
            ]
        )
        lines.append(f"- 查询：`{topic.query}`")
        lines.append(
            f"- 抓取上限：{topic.max_results} 篇（按 relevance 排序）· "
            f"相关性阈值：{topic.min_score}"
        )
        lines.append("")
        if not entries:
            lines.append("本主题没有论文达到相关性阈值，完整命中见 CSV。")
            continue
        lines.extend(
            [
                "| # | 标题 / Link | arXiv | 发布 | 作者 | 摘要 |",
                "|---:|:--|:--|:--|:--|:--|",
            ]
        )
        for index, (stored, _, score) in enumerate(entries, start=1):
            paper = stored.paper
            published = paper.published_at.astimezone().strftime("%Y-%m-%d")
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        f"[{_link_text(paper.title)}]({paper.url})",
                        f"`{paper.canonical_id}`",
                        published,
                        _cell(_authors(paper.authors)),
                        _cell(paper.abstract),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "---",
            "数据源：arXiv API。去重：规范 ID、版本 upsert、DOI、标题 + 第一作者、摘要 shingle。",
            "主题归属：标题主题词权重 6、摘要主题词权重 1，同分时标题命中数优先。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_exports(
    config: Config,
    papers: list[StoredPaper],
    window_start: datetime,
    window_end: datetime,
    out_dir: str | Path,
    name: str,
) -> tuple[Path, Path]:
    directory = Path(out_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    rows = _rows(config, papers)
    order = {topic.id: index for index, topic in enumerate(config.topics)}

    md_path = directory / f"{name}.md"
    csv_path = directory / f"{name}.csv"

    md_path.write_text(
        render_table(config, papers, window_start, window_end), encoding="utf-8"
    )

    ordered = sorted(
        rows,
        key=lambda row: (order.get(row[1], 99), -row[2], -row[0].paper.published_at.timestamp()),
    )
    min_scores = {topic.id: topic.min_score for topic in config.topics}
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "selected",
                "topic",
                "score",
                "title",
                "arxiv_id",
                "url",
                "published",
                "updated",
                "authors",
                "categories",
                "all_topics",
                "abstract",
            ]
        )
        for stored, topic_id, score in ordered:
            paper = stored.paper
            selected = 1 if topic_id not in min_scores or score >= min_scores[topic_id] else 0
            writer.writerow(
                [
                    selected,
                    topic_id,
                    score,
                    paper.title,
                    paper.canonical_id,
                    paper.url,
                    paper.published_at.astimezone().strftime("%Y-%m-%d"),
                    paper.updated_at.astimezone().strftime("%Y-%m-%d"),
                    "; ".join(paper.authors),
                    "; ".join(paper.categories),
                    "; ".join(stored.topics),
                    paper.abstract,
                ]
            )
    return md_path, csv_path
