"""
feed.py: talk to the scenario MCP server, with a fixture fallback.

Live:     async with LiveFeed() as feed: tick = await feed.get_tick("outbreak-level-5")
Fallback: async with FixtureFeed() as feed: ...   (same methods)
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession

try:  # newer mcp versions
    from mcp.client.streamable_http import streamable_http_client as http_client
except ImportError:  # older mcp versions
    from mcp.client.streamable_http import streamablehttp_client as http_client

load_dotenv()

SCENARIOS = ["outbreak-level-5", "control-tick"]
TEAM = os.environ.get("TEAM_HANDLE", "").strip().lower()
TOKEN = os.environ.get("TEAM_TOKEN", "").strip()
URL = os.environ.get("SCENARIO_MCP", "https://api.you.com/mcp/halloween-hack-26")
QUEUE_FILE = Path("pending_submissions.jsonl")


def _parse(result) -> dict:
    """Turn an MCP tool result into a dict, whatever shape it comes in."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


def _unwrap_tick(data: dict) -> tuple[dict, bool]:
    """Return (tick, exhausted). Handles {..tick..} or {"tick": {...}}."""
    exhausted = bool(data.get("exhausted", False))
    tick = data.get("tick") if isinstance(data.get("tick"), dict) else data
    return tick, exhausted


def _normalize_submission(data: dict) -> dict:
    """Expose the official nested judgment without losing envelope status flags."""
    judgment = data.get("judgment")
    # Envelope fields take precedence, especially duplicate/ok/error: callers
    # use those to decide whether an incident alert is allowed to be sent.
    normalized = {**judgment, **data} if isinstance(judgment, dict) else dict(data)
    if "expected_action" not in normalized and "expected" in normalized:
        normalized["expected_action"] = normalized["expected"]
    return normalized


class LiveFeed:
    def __init__(self, url: str = URL, team: str = TEAM):
        if not team:
            raise ValueError("Set TEAM_HANDLE in .env (lowercase, e.g. team-04)")
        self.url, self.team = url, team
        self._client = self._session = None

    async def __aenter__(self):
        self._client = http_client(self.url)
        streams = await self._client.__aenter__()
        self._session = ClientSession(streams[0], streams[1])
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc):
        await self._session.__aexit__(*exc)
        await self._client.__aexit__(*exc)

    async def _call(self, tool: str, args: dict) -> dict:
        if TOKEN and tool in ("hack26-get-tick", "hack26-submit-containment"):
            args = {**args, "token": TOKEN}
        result = await self._session.call_tool(tool, args)
        data = _parse(result)
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            raise RuntimeError(f"{tool} failed: {data}")
        return data

    async def get_tick(self, scenario: str) -> tuple[dict, bool]:
        data = await self._call("hack26-get-tick", {"team": self.team, "scenario": scenario})
        return _unwrap_tick(data)

    async def submit(self, tick_id: int, action: str) -> dict:
        data = await self._call("hack26-submit-containment",
                                {"team": self.team, "tick_id": int(tick_id), "action": action})
        return _normalize_submission(data)

    async def scoreboard(self) -> dict:
        return await self._call("hack26-get-scoreboard", {})

    async def ask_oracle(self, question: str) -> str:
        data = await self._call("hack26-ask-oracle", {"question": question[:500]})
        return data.get("answer") or data.get("text") or json.dumps(data)


class FixtureFeed:
    """Same interface, reads fixtures/<scenario>.json. Submissions are queued to a file."""

    def __init__(self, folder: str = "fixtures"):
        self.folder = Path(folder)
        self._cursor: dict[str, int] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _ticks(self, scenario: str) -> list[dict]:
        data = json.loads((self.folder / f"{scenario}.json").read_text())
        if isinstance(data, list):
            return data
        return data.get("ticks", [data]) if isinstance(data, dict) else [data]

    async def get_tick(self, scenario: str) -> tuple[dict, bool]:
        ticks = self._ticks(scenario)
        i = self._cursor.get(scenario, 0)
        self._cursor[scenario] = i + 1
        if i >= len(ticks):
            return ticks[-1], True
        return ticks[i], False

    async def submit(self, tick_id: int, action: str) -> dict:
        with QUEUE_FILE.open("a") as f:
            f.write(json.dumps({"tick_id": tick_id, "action": action}) + "\n")
        return {"queued": True, "tick_id": tick_id, "action": action}

    async def scoreboard(self) -> dict:
        return {"offline": True}

    async def ask_oracle(self, question: str) -> str:
        return "(oracle offline)"
