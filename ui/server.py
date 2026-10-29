"""C2 event receiver and browser broadcaster. Run: python ui/server.py."""

import argparse
import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


UI_DIRECTORY = Path(__file__).resolve().parent
MAX_PENDING_EVENTS = 100
MAX_REPLAY_LOGS = 50
REPLAY_TYPES = {"tick_start", "agent_log", "decision", "score", "dispatch", "metrics"}


@dataclass(eq=False)
class BrowserConnection:
    socket: WebSocket
    outbox: asyncio.Queue[str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=MAX_PENDING_EVENTS)
    )
    overloaded: asyncio.Event = field(default_factory=asyncio.Event)


class EventHub:
    """Queue events independently for each browser; never wait on socket writes."""

    def __init__(self) -> None:
        self.connections: set[BrowserConnection] = set()
        self._run_id: str | None = None
        self._seen_runs: deque[str] = deque(maxlen=100)
        self._tick: tuple[Any, Any] | None = None
        self._seen_ticks: deque[tuple[Any, Any]] = deque(maxlen=100)
        self._latest: dict[str, tuple[int, str]] = {}
        self._logs: deque[tuple[int, str]] = deque(maxlen=MAX_REPLAY_LOGS)
        self._sequence = 0

    def add(self, socket: WebSocket) -> BrowserConnection:
        connection = BrowserConnection(socket)
        # A late browser receives the current tick before joining live delivery.
        # This synchronous step cannot interleave with publish(), and the replay
        # is at most five singleton events plus fifty agent reports.
        start = self._latest.get("tick_start")
        replay = sorted(
            [value for kind, value in self._latest.items() if kind != "tick_start"]
            + list(self._logs)
        )
        if start:
            connection.outbox.put_nowait(start[1])
        for _, payload in replay:
            connection.outbox.put_nowait(payload)
        self.connections.add(connection)
        return connection

    def _retain(self, event: dict[str, Any], payload: str) -> None:
        kind = event.get("type")
        if not isinstance(kind, str) or kind not in REPLAY_TYPES:
            return
        run_id = event.get("run_id")
        if isinstance(run_id, str) and run_id and run_id != self._run_id:
            if event.get("tick_id") is None:
                return
            if run_id in self._seen_runs:
                return  # A delayed result must not restore an earlier run.
            self._seen_runs.append(run_id)
            self._run_id = run_id
            self._tick = None
            self._seen_ticks.clear()
            self._latest.clear()
            self._logs.clear()
        tick_id = event.get("tick_id", self._tick[1] if self._tick else None)
        if tick_id is None:
            return
        facility = event.get("facility_id", self._tick[0] if self._tick else None)
        tick = (facility, tick_id)
        if kind == "tick_start" and tick != self._tick:
            if tick in self._seen_ticks:
                return
            self._seen_ticks.append(tick)
            self._tick = tick
            self._latest.clear()
            self._logs.clear()
        elif self._tick is None:
            self._tick = tick
        elif tick != self._tick:
            return  # Still broadcast late results, without replacing the replay.
        self._sequence += 1
        entry = (self._sequence, payload)
        if kind == "agent_log":
            self._logs.append(entry)
        else:
            self._latest[kind] = entry

    def remove(self, connection: BrowserConnection) -> None:
        self.connections.discard(connection)

    def publish(self, event: dict[str, Any]) -> int:
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False)
        self._retain(event, payload)
        queued = 0
        for connection in tuple(self.connections):
            try:
                connection.outbox.put_nowait(payload)
                queued += 1
            except asyncio.QueueFull:
                # A stalled browser reconnects instead of holding up the swarm.
                self.remove(connection)
                connection.overloaded.set()
        return queued


async def send_events(connection: BrowserConnection) -> None:
    while True:
        await connection.socket.send_text(await connection.outbox.get())


async def wait_for_disconnect(socket: WebSocket) -> None:
    while True:
        message = await socket.receive()
        if message["type"] == "websocket.disconnect":
            return


def create_app() -> FastAPI:
    application = FastAPI(title="Watchtower event bridge")
    hub = EventHub()
    application.state.hub = hub
    application.mount("/assets", StaticFiles(directory=UI_DIRECTORY / "assets"), name="assets")

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(UI_DIRECTORY / "index.html")

    @application.post("/emit")
    async def receive_event(event: dict[str, Any]) -> dict[str, Any]:
        # Preserve every field, including future event types and partial events.
        try:
            recipients = hub.publish(event)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Event must contain valid JSON values") from exc
        return {"ok": True, "clients": recipients}

    @application.websocket("/ws")
    async def browser_events(socket: WebSocket) -> None:
        await socket.accept()
        connection = hub.add(socket)
        tasks = [
            asyncio.create_task(send_events(connection)),
            asyncio.create_task(wait_for_disconnect(socket)),
            asyncio.create_task(connection.overloaded.wait()),
        ]
        disconnect_task = tasks[1]
        disconnected = False
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            disconnected = disconnect_task in done and not disconnect_task.cancelled()
            for task in done:
                if not task.cancelled():
                    task.result()
        except (WebSocketDisconnect, OSError, RuntimeError):
            pass
        finally:
            hub.remove(connection)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not disconnected:
                with suppress(Exception):
                    await asyncio.wait_for(
                        socket.close(code=1013 if connection.overloaded.is_set() else 1000),
                        timeout=0.2,
                    )

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the Watchtower C2 event bridge.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
