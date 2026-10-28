"""Parasail clients used by Person B's integration lane.

The module keeps provider-specific configuration in one place.  It does not
make a network request on import, which keeps local tests and fixture replay
usable without credentials.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

DEFAULT_MODEL = "parasail-glm-53-flash"
FALLBACK_MODEL = "parasail-deepseek-v4-flash"
EMBEDDING_MODEL = "parasail-ai/GritLM-7B-vllm"
PARASAIL_BASE_URL = "https://api.parasail.io/v1"

T = TypeVar("T")


class ParasailConfigurationError(RuntimeError):
    """Raised when Parasail cannot be configured safely."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ParasailConfigurationError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class ParasailSettings:
    api_key: str
    model: str = DEFAULT_MODEL
    fallback_model: str = FALLBACK_MODEL
    embedding_model: str = EMBEDDING_MODEL
    base_url: str = PARASAIL_BASE_URL
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "ParasailSettings":
        return cls(
            api_key=_required_env("PARASAIL_API_KEY"),
            model=os.getenv("PARASAIL_MODEL", DEFAULT_MODEL),
            fallback_model=os.getenv("PARASAIL_FALLBACK_MODEL", FALLBACK_MODEL),
            embedding_model=os.getenv("PARASAIL_EMBEDDING_MODEL", EMBEDDING_MODEL),
            base_url=os.getenv("PARASAIL_BASE_URL", PARASAIL_BASE_URL).rstrip("/"),
        )


def create_openai_client(settings: ParasailSettings | None = None) -> Any:
    """Create Parasail's OpenAI-compatible client lazily."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on local setup
        raise ParasailConfigurationError(
            "Install the OpenAI-compatible client with: pip install openai"
        ) from exc

    settings = settings or ParasailSettings.from_env()
    return OpenAI(api_key=settings.api_key, base_url=settings.base_url)


def create_crewai_llm(settings: ParasailSettings | None = None) -> Any:
    """Create the CrewAI LLM wrapper backed by Parasail.

    CrewAI's provider prefix is ``openai/`` because Parasail exposes an
    OpenAI-compatible endpoint; the actual model request still goes to
    Parasail's base URL.
    """
    try:
        from crewai import LLM
    except ImportError as exc:  # pragma: no cover - depends on local setup
        raise ParasailConfigurationError(
            "Install CrewAI with: pip install crewai crewai-tools"
        ) from exc

    settings = settings or ParasailSettings.from_env()
    return LLM(
        model=f"openai/{settings.model}",
        base_url=settings.base_url,
        api_key=settings.api_key,
        temperature=0.0,
    )


def with_backoff(operation: Callable[[], T], *, max_retries: int = 2) -> T:
    """Retry transient provider failures with bounded exponential backoff.

    This intentionally does not loop forever: Parasail's event limits are low
    on free keys and a retry storm would make the hackathon demo worse.
    """
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            return operation()
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            response = getattr(exc, "response", None)
            response_status = getattr(response, "status_code", None)
            if attempt >= max_retries or (status not in (408, 429, 500, 502, 503, 504) and response_status not in (408, 429, 500, 502, 503, 504)):
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def chat_json(
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    settings: ParasailSettings | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Ask Parasail for schema-locked JSON, with one model fallback."""
    settings = settings or ParasailSettings.from_env()
    client = client or create_openai_client(settings)

    def request(model: str) -> dict[str, Any]:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        content = response.choices[0].message.content
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("Parasail returned JSON that was not an object")
        return value

    try:
        return with_backoff(lambda: request(settings.model), max_retries=settings.max_retries)
    except Exception:
        if settings.fallback_model == settings.model:
            raise
        return with_backoff(lambda: request(settings.fallback_model), max_retries=settings.max_retries)


def embed_texts(
    texts: list[str], *, settings: ParasailSettings | None = None, client: Any | None = None
) -> list[list[float]]:
    """Return Parasail embeddings for a batch of sector descriptions."""
    if not texts:
        return []
    settings = settings or ParasailSettings.from_env()
    client = client or create_openai_client(settings)
    response = with_backoff(
        lambda: client.embeddings.create(model=settings.embedding_model, input=texts),
        max_retries=settings.max_retries,
    )
    return [item.embedding for item in response.data]
