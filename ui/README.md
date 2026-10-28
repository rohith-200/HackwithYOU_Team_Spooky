# C2 event bridge

This is Person C's event transport and development monitor. The final building interface can replace `index.html` while keeping the same event connection.

## Start on Windows

From the repository root, using Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ui\requirements.txt
.\.venv\Scripts\python.exe ui\server.py
```

Open http://localhost:8000. In a second terminal, replay simulated incidents:

```powershell
.\.venv\Scripts\python.exe ui\mock_replay.py
```

The browser shows incoming events and logs them to its developer console. The replay uses simulated events, scores, and delivery results; it does not contact the official scenario feed or Discord.

## Teammate integration

The C1 `src.events.emit(event)` helper posts to `UI_URL/emit`. Its default `UI_URL` is `http://localhost:8000`. Callers remain responsible for loading `.env`.

Person A's current A1 loop prints decisions but does not emit events yet. Use the mock replay to exercise this monitor until the loop calls C1 at each step.

- `POST /emit`: accepts a JSON object and queues the unchanged event for connected browsers. Responds with `{"ok": true, "clients": N}`; this acknowledges local queueing, not browser receipt.
- WebSocket `/ws`: streams those JSON events in order for each connected browser.
- `GET /`: serves `ui/index.html`, regardless of the terminal's working directory.

Event names and fields remain unchanged: `tick_start`, `agent_log`, `decision`, `score`, `dispatch`, and `metrics`. Partial objects and future event names are forwarded without alteration. The browser tolerates missing fields and reconnects every two seconds after a disconnection.

This local bridge runs in one process. It does not retain events for browsers that are disconnected. A slow browser has a bounded queue of 100 events; if it falls behind, its connection is closed and the browser reconnects. Other browsers and the agent loop continue independently.

A button that starts an actual tick needs a separate integration with Person A's loop. The event bridge only receives updates.

## Checks

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```
