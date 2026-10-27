from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    digest_dir: Path
    overlap_days: int = 2
    initial_lookback_days: int = 3
    max_lookback_days: int = 21
    max_results_per_topic: int = 50
    page_size: int = 50
    request_timeout: float = 30.0
    retries: int = 5
    user_agent: str = "paper-radar/0.1"
    sort_by: str = "submittedDate"
    proxy: str | None = None
    near_dup_days: int = 45


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    query: str
    max_results: int
    include_terms: tuple[str, ...] = ()
    title_terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    min_score: int = 1


@dataclass(frozen=True)
class Config:
    path: Path
    settings: Settings
    topics: tuple[Topic, ...]

    def topic_by_id(self, topic_id: str) -> Topic:
        for topic in self.topics:
            if topic.id == topic_id:
                return topic
        raise ConfigError(f"unknown topic: {topic_id}")


def _int(raw: dict[str, Any], key: str, default: int, minimum: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"settings.{key} must be an integer")
    if value < minimum:
        raise ConfigError(f"settings.{key} must be >= {minimum}")
    return value


def _float(raw: dict[str, Any], key: str, default: float, minimum: float) -> float:
    value = raw.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"settings.{key} must be a number")
    if value < minimum:
        raise ConfigError(f"settings.{key} must be >= {minimum}")
    return float(value)


def _terms(raw: dict[str, Any], key: str, index: int) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigError(f"topics[{index}].{key} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def load_config(path: str | Path) -> Config:
    config_path = Path(path).expanduser().resolve()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    settings_raw = raw.get("settings", {})
    if not isinstance(settings_raw, dict):
        raise ConfigError("settings must be a table")

    base = config_path.parent

    def resolve_dir(key: str) -> Path:
        value = settings_raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"settings.{key} must be a non-empty path")
        return (base / value).expanduser().resolve()

    sort_by = settings_raw.get("sort_by", "submittedDate")
    if sort_by not in {"submittedDate", "lastUpdatedDate", "relevance"}:
        raise ConfigError("settings.sort_by must be submittedDate, lastUpdatedDate, or relevance")

    proxy = settings_raw.get("proxy")
    if proxy is not None and not isinstance(proxy, str):
        raise ConfigError("settings.proxy must be a string")

    settings = Settings(
        state_dir=resolve_dir("state_dir"),
        digest_dir=resolve_dir("digest_dir"),
        overlap_days=_int(settings_raw, "overlap_days", 2, 0),
        initial_lookback_days=_int(settings_raw, "initial_lookback_days", 3, 1),
        max_lookback_days=_int(settings_raw, "max_lookback_days", 21, 1),
        max_results_per_topic=_int(settings_raw, "max_results_per_topic", 50, 1),
        page_size=_int(settings_raw, "page_size", 50, 1),
        request_timeout=_float(settings_raw, "request_timeout", 30.0, 1.0),
        retries=_int(settings_raw, "retries", 5, 0),
        user_agent=str(settings_raw.get("user_agent", "paper-radar/0.1")),
        sort_by=sort_by,
        proxy=proxy,
        near_dup_days=_int(settings_raw, "near_dup_days", 45, 1),
    )

    topics_raw = raw.get("topics")
    if not isinstance(topics_raw, list) or not topics_raw:
        raise ConfigError("at least one [[topics]] entry is required")

    topics: list[Topic] = []
    seen: set[str] = set()
    for index, item in enumerate(topics_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"topics[{index}] must be a table")
        if item.get("enabled", True) is False:
            continue
        topic_id = item.get("id")
        name = item.get("name")
        query = item.get("query")
        if not isinstance(topic_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", topic_id):
            raise ConfigError(f"topics[{index}].id must match [a-z0-9][a-z0-9-]*")
        if topic_id in seen:
            raise ConfigError(f"duplicate topic id: {topic_id}")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"topics[{index}].name is required")
        if not isinstance(query, str) or not query.strip():
            raise ConfigError(f"topics[{index}].query is required")
        max_results = item.get("max_results", settings.max_results_per_topic)
        if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
            raise ConfigError(f"topics[{index}].max_results must be a positive integer")
        min_score = item.get("min_score", 1)
        if not isinstance(min_score, int) or isinstance(min_score, bool) or min_score < 0:
            raise ConfigError(f"topics[{index}].min_score must be a non-negative integer")
        seen.add(topic_id)
        topics.append(
            Topic(
                id=topic_id,
                name=name.strip(),
                query=query.strip(),
                max_results=max_results,
                include_terms=_terms(item, "include_terms", index),
                title_terms=_terms(item, "title_terms", index),
                exclude_terms=_terms(item, "exclude_terms", index),
                min_score=min_score,
            )
        )

    if not topics:
        raise ConfigError("all topics are disabled")

    return Config(path=config_path, settings=settings, topics=tuple(topics))
