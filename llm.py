"""Minimal OpenAI-compatible client for the tierflow endpoint.

Uses only the standard library so the pipeline has no install step. JSON mode
is requested when available, with a strict extraction + one repair round.
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from config import Settings


class LLMError(RuntimeError):
    pass


@dataclass
class ChatResult:
    content: str
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed: float = 0.0
    model: str = ""
    attempts: int = 1


def _extract_json(text: str) -> Any:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = min(
        (idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0),
        default=-1,
    )
    if start < 0:
        raise LLMError("no JSON object found in reply")
    depth = 0
    in_string = False
    escape = False
    opener = cleaned[start]
    closer = "}" if opener == "{" else "]"
    for idx in range(start, len(cleaned)):
        char = cleaned[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                chunk = cleaned[start : idx + 1]
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError as exc:
                    raise LLMError(f"invalid JSON: {exc}") from exc
    raise LLMError("unterminated JSON object")


class LLMClient:
    def __init__(self, settings: Settings):
        if not settings.has_key():
            raise LLMError(
                "no API key: set PAPER_ENV_API_KEY, write paper-envs/.env.local, "
                "or configure ~/.minimax/config.yaml"
            )
        self.settings = settings
        self.calls = 0
        self.usage_totals: dict[str, int] = {}
        self.last_result: ChatResult | None = None

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        url = self.settings.api_base.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:800]
                last_error = LLMError(f"HTTP {exc.code}: {detail}")
                if exc.code in (408, 429) or exc.code >= 500:
                    time.sleep(min(2**attempt, 8))
                    continue
                break
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_error = LLMError(f"network error: {exc}")
                time.sleep(min(2**attempt, 8))
        raise last_error or LLMError("request failed")

    def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_effort": self.settings.effort,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.time()
        try:
            data = self._post(payload)
        except LLMError as exc:
            if json_mode and "response_format" in str(exc).lower():
                payload.pop("response_format", None)
                data = self._post(payload)
            else:
                raise
        elapsed = time.time() - started
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"empty choices: {json.dumps(data)[:400]}")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not content.strip():
            finish = choices[0].get("finish_reason")
            reasoning = message.get("reasoning_content") or ""
            raise LLMError(
                "empty content in reply "
                f"(finish_reason={finish}, reasoning_chars={len(reasoning)}, "
                f"max_tokens={max_tokens}, usage={json.dumps(data.get('usage') or {})}). "
                "Reasoning likely consumed the whole output budget; raise max_tokens."
            )
        usage = data.get("usage") or {}
        for key, value in usage.items():
            if isinstance(value, int):
                self.usage_totals[key] = self.usage_totals.get(key, 0) + value
        self.calls += 1
        result = ChatResult(
            content=content,
            usage=usage,
            elapsed=elapsed,
            model=data.get("model") or self.settings.model,
        )
        self.last_result = result
        return result
    def chat_text(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 6000,
        temperature: float = 0.2,
    ) -> str:
        return self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_mode=False,
            max_tokens=max_tokens,
            temperature=temperature,
        ).content

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 16000,
        temperature: float = 0.2,
        repair: bool = True,
        on_attempt: Callable[[int, float], None] | None = None,
    ) -> Any:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: LLMError | None = None
        result = None
        for attempt in range(1, 3):
            result = self._chat(
                messages, json_mode=True, max_tokens=max_tokens, temperature=temperature
            )
            if on_attempt:
                on_attempt(attempt, result.elapsed)
            try:
                return _extract_json(result.content)
            except LLMError as exc:
                last_error = exc
                if not repair:
                    raise
                time.sleep(1)
        assert result is not None
        messages.extend(
            [
                {"role": "assistant", "content": result.content},
                {
                    "role": "user",
                    "content": (
                        "That reply was not valid JSON. Return the same content again as "
                        "a single valid JSON object, with no markdown fence and no prose."
                    ),
                },
            ]
        )
        repaired = self._chat(
            messages, json_mode=True, max_tokens=max_tokens, temperature=0.0
        )
        if on_attempt:
            on_attempt(3, repaired.elapsed)
        return _extract_json(repaired.content)
