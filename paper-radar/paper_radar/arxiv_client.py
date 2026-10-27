from __future__ import annotations

import logging
import random
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .config import Settings, Topic
from .dedupe import canonical_arxiv_id, parse_arxiv_id
from .models import FetchResult, Paper

logger = logging.getLogger("paper_radar.arxiv")

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

ENDPOINT = "https://export.arxiv.org/api/query"
MIN_REQUEST_INTERVAL = 3.0


class ArxivError(RuntimeError):
    pass


def build_query(topic_query: str, since: datetime, until: datetime) -> str:
    since_text = since.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    until_text = until.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    return f"({topic_query}) AND submittedDate:[{since_text} TO {until_text}]"


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _parse_datetime(value: str | None, fallback: datetime | None = None) -> datetime:
    if not value:
        if fallback is not None:
            return fallback
        raise ValueError("missing date in arXiv entry")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_feed(data: bytes | str) -> tuple[list[Paper], int]:
    if isinstance(data, str):
        data = data.encode("utf-8")
    root = ET.fromstring(data)
    total_text = root.findtext(f"{OPENSEARCH}totalResults")
    total = int(total_text) if total_text and total_text.isdigit() else 0

    papers: list[Paper] = []
    for entry in root.findall(f"{ATOM}entry"):
        raw_id = _clean(entry.findtext(f"{ATOM}id"))
        if not raw_id:
            continue
        base_id, version = parse_arxiv_id(raw_id)
        published = _parse_datetime(entry.findtext(f"{ATOM}published"))
        updated = _parse_datetime(entry.findtext(f"{ATOM}updated"), fallback=published)
        authors = [
            _clean(author.findtext(f"{ATOM}name"))
            for author in entry.findall(f"{ATOM}author")
            if _clean(author.findtext(f"{ATOM}name"))
        ]
        categories: list[str] = []
        for category in entry.findall(f"{ATOM}category"):
            term = category.get("term")
            if term and term not in categories:
                categories.append(term)
        source_id = f"{base_id}v{version}" if version else base_id
        papers.append(
            Paper(
                source="arxiv",
                source_id=source_id,
                canonical_id=canonical_arxiv_id(base_id),
                title=_clean(entry.findtext(f"{ATOM}title")),
                abstract=_clean(entry.findtext(f"{ATOM}summary")),
                authors=authors,
                categories=categories,
                published_at=published,
                updated_at=updated,
                version=version,
                url=f"https://arxiv.org/abs/{source_id}",
                doi=_clean(entry.findtext(f"{ARXIV}doi")) or None,
            )
        )
    return papers, total


class ArxivClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._use_curl = False
        if settings.proxy is None:
            self._opener = urllib.request.build_opener()
        elif settings.proxy == "":
            self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            proxy = settings.proxy
            self._opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )

    def _request_curl(self, url: str) -> bytes:
        curl = shutil.which("curl")
        if curl is None:
            raise ArxivError("curl fallback unavailable")
        args = [
            curl,
            "-sS",
            "--max-time",
            str(int(self.settings.request_timeout)),
            "-A",
            self.settings.user_agent,
            "-H",
            "Accept: application/atom+xml",
        ]
        if self.settings.proxy:
            args.extend(["--proxy", self.settings.proxy])
        elif self.settings.proxy == "":
            args.extend(["--noproxy", "*"])
        args.append(url)
        result = subprocess.run(args, capture_output=True)
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()
            raise ArxivError(message or f"curl exited {result.returncode}")
        return result.stdout

    def _request(self, params: dict[str, str | int]) -> bytes:
        url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
        last_error: Exception | None = None
        if self._use_curl:
            try:
                return self._request_curl(url)
            except ArxivError as exc:
                last_error = exc
                logger.warning("curl failed (%s); falling back to urllib", exc)
        curl_tried = False
        for attempt in range(self.settings.retries + 1):
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept": "application/atom+xml",
                    "Connection": "close",
                },
            )
            try:
                with self._opener.open(request, timeout=self.settings.request_timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                last_error = exc
                retryable = exc.code in {429, 500, 502, 503, 504}
                if not retryable or attempt >= self.settings.retries:
                    raise ArxivError(f"arXiv HTTP {exc.code}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last_error = exc
                # macOS 上 Python 的 TLS 栈偶尔对 export.arxiv.org 抛
                # UNEXPECTED_EOF；curl 走 LibreSSL，通常能直接成功。
                if not curl_tried:
                    curl_tried = True
                    try:
                        logger.warning("urllib failed (%s); retrying with curl", exc)
                        payload = self._request_curl(url)
                        self._use_curl = True
                        return payload
                    except ArxivError as curl_exc:
                        last_error = curl_exc
                if attempt >= self.settings.retries:
                    raise ArxivError(f"arXiv request failed: {last_error}") from exc

            delay = min(2**attempt, 30) + random.uniform(0, 1)
            logger.warning("arXiv request failed (%s), retrying in %.1fs", last_error, delay)
            time.sleep(delay)

        raise ArxivError(f"arXiv request failed: {last_error}")

    def _fetch_page(self, query: str, start: int, count: int) -> tuple[list[Paper], int]:
        payload = self._request(
            {
                "search_query": query,
                "start": start,
                "max_results": count,
                "sortBy": self.settings.sort_by,
                "sortOrder": "descending",
            }
        )
        return parse_feed(payload)

    def fetch_topic(self, topic: Topic, since: datetime, until: datetime) -> FetchResult:
        query = build_query(topic.query, since, until)
        collected: dict[str, Paper] = {}
        start = 0
        pages = 0
        total = 0

        while len(collected) < topic.max_results:
            if pages:
                time.sleep(MIN_REQUEST_INTERVAL)
            want = min(self.settings.page_size, topic.max_results - len(collected))
            page_papers, total = self._fetch_page(query, start, want)
            pages += 1
            if not page_papers:
                break

            reached_before_window = False
            for paper in page_papers:
                if paper.published_at < since:
                    reached_before_window = True
                    continue
                existing = collected.get(paper.canonical_id)
                if existing is None or paper.version > existing.version:
                    collected[paper.canonical_id] = paper

            start += len(page_papers)
            if reached_before_window or len(page_papers) < want or start >= total:
                break

        papers = sorted(collected.values(), key=lambda item: item.published_at, reverse=True)
        return FetchResult(papers=papers, total_results=total, pages=pages, query=query)
