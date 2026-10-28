"""C2 event receiver and browser broadcaster. Run: python ui/server.py."""

import argparse
import asyncio
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

    def add(self, socket: WebSocket) -> BrowserConnection:
        connection = BrowserConnection(socket)
        self.connections.add(connection)
        return connection

    def remove(self, connection: BrowserConnection) -> None:
        self.connections.discard(connection)

    def publish(self, event: dict[str, Any]) -> int:
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False)
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
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, OSError, RuntimeError):
            pass
        finally:
            hub.remove(connection)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
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
