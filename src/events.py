"""Best-effort delivery of swarm events to the optional command-center UI."""

import json
import os
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_UI_URL = "http://localhost:8000"
EMIT_TIMEOUT_SECONDS = 0.5


def emit(event: dict[str, Any]) -> None:
    """POST an event unchanged to ``UI_URL/emit`` without interrupting the swarm.

    The caller owns the shared event schema and loads any .env configuration.
    UI_URL is read on each call and defaults to http://localhost:8000.
    Serialization, configuration, HTTP, and connection errors are ignored.
    The timeout applies to socket operations, rather than a total deadline.
    """
    try:
        base_url = os.environ.get("UI_URL", "").strip() or DEFAULT_UI_URL
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
