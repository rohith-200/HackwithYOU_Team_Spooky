"""Best-effort delivery of swarm events to the optional command-center UI."""

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_UI_URL = "http://localhost:8000"
EMIT_TIMEOUT_SECONDS = 0.5
_EVENT_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("watchtower_event_context", default=None)


@contextmanager
def event_context(**metadata: Any):
    """Attach run metadata to this task's events, including nested CrewAI calls.

    Nested contexts extend the current metadata. Explicit event fields always
    win, and leaving a context restores the previous values, even on failure.
    """
    token = _EVENT_CONTEXT.set({**(_EVENT_CONTEXT.get() or {}), **metadata})
    try:
        yield
    finally:
        _EVENT_CONTEXT.reset(token)


def emit(event: dict[str, Any]) -> None:
    """POST an event to ``UI_URL/emit`` without interrupting the swarm.

    Events are unchanged unless the caller opted into ``event_context``. The
    caller owns the shared event schema and loads any .env configuration.
    UI_URL is read on each call and defaults to http://localhost:8000.
    Serialization, configuration, HTTP, and connection errors are ignored.
    The timeout applies to socket operations, rather than a total deadline.
    """
    try:
        base_url = os.environ.get("UI_URL", "").strip() or DEFAULT_UI_URL
        context = _EVENT_CONTEXT.get()
        if context:
            event = {**context, **event}
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = Request(
            f"{base_url.rstrip('/')}/emit",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=EMIT_TIMEOUT_SECONDS):
            pass
    except Exception:
        # UI delivery is optional: failures must not stop a containment decision.
        return
