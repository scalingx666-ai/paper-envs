from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import Config
from .relevance import select_for_digest
from .store import StoredPaper


def _escape_link_text(value: str) -> str:
    return value.replace("[", r"\[").replace("]", r"\]")


def _truncate(value: str, limit: int = 900) -> str:
    if len(value) <= limit:
        return value
    cut = value.rfind(" ", 0, limit)
    if cut <= 0:
        cut = limit
    return value[:cut].rstrip() + "…"


def _authors(value: list[str]) -> str:
    if len(value) <= 5:
        return ", ".join(value)
    return ", ".join(value[:5]) + ", et al."


def render_digest(
    config: Config,
    papers: list[StoredPaper],
    window_start: datetime,
    window_end: datetime,
    stats: dict[str, int],
) -> str:
    papers, filtered = select_for_digest(config, papers)
    local_now = datetime.now().astimezone()
    filtered_note = (
        f"- 重建自本地数据库 · 窗口内 **{stats['window_total']}** 篇"
        if stats.get("rebuild")
        else (
            f"- 新增 **{stats.get('inserted', 0)}** 篇 · 版本更新 {stats.get('updated', 0)} · "
            f"重复命中 {stats.get('duplicates', 0)} · 跨源抑制 {stats.get('suppressed', 0)}"
        )
    )
    lines: list[str] = [
        f"# arXiv Paper Radar · {local_now:%Y-%m-%d}",
        "",
        f"- 抓取窗口（UTC）：{window_start:%Y-%m-%d %H:%M} → {window_end:%Y-%m-%d %H:%M}",
        filtered_note,
        f"- 摘要展示 **{len(papers)}** 篇 · 相关性过滤 {filtered} 篇",
        "",
    ]

    if not papers:
        if filtered:
            lines.append(f"本次窗口有 {filtered} 篇新论文，但均未达到主题相关性阈值。")
        else:
            lines.append("本次窗口没有新论文。")
        lines.append("")
        lines.append("---")
        lines.append("数据源：arXiv API · 去重：规范 ID + 标题/作者 + 摘要 shingle")
        return "\n".join(lines) + "\n"

    grouped: dict[str, list[StoredPaper]] = {topic.id: [] for topic in config.topics}
    other: list[StoredPaper] = []
    for stored in papers:
        if stored.primary_topic in grouped:
            grouped[stored.primary_topic].append(stored)
        else:
            other.append(stored)

    for topic in config.topics:
        entries = grouped[topic.id]
        if not entries:
            continue
        lines.append(f"## {topic.name}（{len(entries)} 篇）")
        lines.append("")
        for index, stored in enumerate(entries, start=1):
            paper = stored.paper
            published = paper.published_at.astimezone().strftime("%Y-%m-%d")
            lines.append(f"### {index}. [{_escape_link_text(paper.title)}]({paper.url})")
            lines.append("")
            lines.append(f"- **arXiv**：`{paper.canonical_id}` · 发布 {published}")
            lines.append(f"- **作者**：{_authors(paper.authors)}")
            if paper.categories:
                lines.append(f"- **分类**：{', '.join(paper.categories)}")
            if stored.topics:
                lines.append(f"- **主题**：{', '.join(stored.topics)}")
            lines.append(f"- **摘要**：{_truncate(paper.abstract)}")
            lines.append("")

    if other:
        lines.append(f"## 其他（{len(other)} 篇）")
        lines.append("")
        for index, stored in enumerate(other, start=1):
            paper = stored.paper
            published = paper.published_at.astimezone().strftime("%Y-%m-%d")
            lines.append(f"### {index}. [{_escape_link_text(paper.title)}]({paper.url})")
            lines.append("")
            lines.append(f"- **arXiv**：`{paper.canonical_id}` · 发布 {published}")
            lines.append(f"- **作者**：{_authors(paper.authors)}")
            lines.append(f"- **摘要**：{_truncate(paper.abstract)}")
            lines.append("")

    lines.append("---")
    lines.append("数据源：arXiv API · 去重：规范 ID + 标题/作者 + 摘要 shingle")
    return "\n".join(lines) + "\n"


def write_digest(digest_dir: str | Path, text: str) -> Path:
    directory = Path(digest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y-%m-%d")
    path = directory / f"{stamp}.md"
    if path.exists():
        path = directory / f"{stamp}-{now:%H%M}.md"
    path.write_text(text, encoding="utf-8")
    # 空摘要（周末无新论文 / 全部被相关性过滤）不覆盖 latest.md，
    # 让 latest 始终指向最近一份有内容的摘要。
    if "\n### " in text:
        (directory / "latest.md").write_text(text, encoding="utf-8")
    return path
