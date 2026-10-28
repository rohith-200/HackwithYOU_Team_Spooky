"""Small, cited You.com grounding tools for Watchtower."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

SEARCH_URL = "https://ydc-index.io/v1/search"
CONTENTS_URL = "https://ydc-index.io/v1/contents"


class YouComError(RuntimeError):
    """Raised when a You.com request cannot be completed."""


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class YouComClient:
    """You.com client with short-lived entity/query caching.

    Search and Contents both require ``X-API-Key``.  We deliberately return
    compact snippets and URLs so the agent context stays small.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 15.0,
        cache_ttl_s: int = 900,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_key = (api_key or os.getenv("YDC_API_KEY", "")).strip()
        self.timeout = timeout
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._http = http_client or httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise YouComError("Missing YDC_API_KEY")
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    def _get_cached(self, key: str) -> Any | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry and entry.expires_at > time.time():
                return entry.value
            self._cache.pop(key, None)
        return None

    def _put_cached(self, key: str, value: Any) -> Any:
        with self._lock:
            self._cache[key] = _CacheEntry(value=value, expires_at=time.time() + self.cache_ttl_s)
        return value

    def search(self, query: str, *, count: int = 3) -> list[dict[str, str]]:
        """Search for grounded evidence and return compact cited results."""
        query = " ".join(query.split())
        key = f"search:{query.lower()}:{count}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        response = self._http.post(
            SEARCH_URL,
            headers=self._headers(),
            json={
                "query": query,
                "count": min(max(count, 1), 3),
                "extraction": {"extraction_mode": "highlights"},
            },
        )
        if response.status_code == 429:
            raise YouComError("You.com rate limit reached; back off before retrying")
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise YouComError(f"You.com search failed: {exc}") from exc

        result_groups = payload.get("results", {})
        if isinstance(result_groups, dict):
            results = result_groups.get("web", [])
        else:
            results = result_groups or payload.get("hits", [])
        compact = []
        for result in results[:3]:
            contents = result.get("contents") or {}
            highlights = contents.get("highlights", []) if isinstance(contents, dict) else []
            snippet = "\n".join(str(item) for item in highlights)
            compact.append(
                {
                    "title": str(result.get("title", "")),
                    "url": str(result.get("url", result.get("link", ""))),
                    "snippet": snippet or str(
                        result.get("description", result.get("snippet", result.get("highlight", "")))
                    ),
                }
            )
        return self._put_cached(key, compact)

    def contents(self, urls: list[str]) -> list[dict[str, str]]:
        """Fetch clean content for URLs already discovered by search."""
        urls = [url.strip() for url in urls if url and url.strip()][:3]
        if not urls:
            return []
        key = "contents:" + "|".join(sorted(urls))
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        response = self._http.post(
            CONTENTS_URL,
            headers=self._headers(),
            json={"urls": urls, "format": "markdown"},
        )
        if response.status_code == 429:
            raise YouComError("You.com rate limit reached; back off before retrying")
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise YouComError(f"You.com contents failed: {exc}") from exc

        pages = payload.get("contents", payload.get("results", []))
        compact = [
            {"url": str(page.get("url", "")), "content": str(page.get("content", page.get("markdown", "")))}
            for page in pages[:3]
        ]
        return self._put_cached(key, compact)


_default_client: YouComClient | None = None


def _client() -> YouComClient:
    global _default_client
    if _default_client is None:
        _default_client = YouComClient()
    return _default_client


def youcom_search(query: str) -> list[dict[str, str]]:
    """CrewAI-friendly wrapper for live web search."""
    return _client().search(query)


def youcom_contents(urls: list[str]) -> list[dict[str, str]]:
    """CrewAI-friendly wrapper for clean page contents."""
    return _client().contents(urls)
