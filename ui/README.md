# Watchtower command center

Person C's C3 interface turns the C2 event stream into an interactive 3D incident board. The fixed, extruded schematic follows `pic3.jpeg`: purple `main_stage`, dark-blue `catering_coffee_bar`, teal `entrance`, and the reference-colored unknown regions. The layout is illustrative, not an architectural floor plan of AWS Builder Loft. These three mapped sectors display the exact backend IDs; other backend sectors stay in mission data, decisions, and scores but do not create map rooms or camera targets. Mapped areas absent from a later tick show **Not updated**, rather than being declared safe.

## Start on Windows

From the repository root, using Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ui\requirements.txt
.\.venv\Scripts\python.exe ui\server.py
```

Open http://localhost:8000. The page starts in **Simulation** mode with a fictional incident preview. No API keys are needed to develop or demonstrate the UI. Python serves the files directly: there is no npm install, frontend build, CDN dependency, or external asset request. Three.js 0.186.1 and OrbitControls are vendored under `assets/vendor/three/`, including their license.

## Explore the board

- Drag the scene to rotate and use the mouse wheel or zoom controls to move closer or farther away.
- Select `main_stage`, `catering_coffee_bar`, or `entrance` on the map or in the directory to inspect the reported readings, civilian status, and containment state.
- Use **Focus** to frame the selected sector and **Overview** to frame the whole board.
- Switch between **Isometric** and **Top** camera views.
- Turn motion off for a still scene; the interface also respects the system's reduced-motion preference.

Sector colors reflect reported breach, anomaly, secure, unverified, or not-updated states. Choosing an action or receiving a perfect score does not change the reported containment or civilian status. A decision to evacuate is not shown as a confirmed rescue.

The page includes agent reports, the commander's decision and rationale, evaluation results, dispatch status, and reported usage metrics. Missing values remain unknown. If WebGL is unavailable, rooms remain selectable in a schematic fallback.

## Local simulations and backend events

**Simulation** runs entirely in the browser. Choose a replay and start it to see the complete event sequence:

- **Breach:** corroborating coffee-bar readings and four pinned civilians lead to a simulated evacuation decision.
- **Control:** human crowd motion at the main stage leads to observation, with no research or escalation.
- **Unknown sector:** the fictional `archive_annex` stays off the map but remains in mission results and receives a simulated containment decision and score.

All local replay events are tagged `_mock: true` and marked as demo data. Scores, model usage, and dispatch results are fictional. The replay runs no agents and contacts no scenario API, model API, You.com service, or Discord channel.

**Backend** displays events received through `/ws`. Its mission state is kept separate from the local simulation, so demo results do not become backend results. A connected WebSocket means the event bridge is reachable; it does not mean the agent loop or sponsor integrations are running. Labelled mock events received from the backend remain marked as simulated.

To test backend delivery, switch to Backend mode and run the existing Python replay in a second terminal:

```powershell
.\.venv\Scripts\python.exe ui\mock_replay.py
```

The Python replay posts fictional events to the local bridge; it does not contact the official scenario feed or Discord. It defaults to one event every two seconds across two ticks. Use `--interval` to change the delay and `--cycles 0` to repeat until interrupted.

**Run Next Tick** is disabled until Person A provides a backend trigger. Starting a local replay demonstrates the UI only; it does not fetch or submit an official tick.

## Teammate integration

The C1 `src.events.emit(event)` helper posts to `UI_URL/emit`. Its default `UI_URL` is `http://localhost:8000`. Callers remain responsible for loading `.env`.

`main.py` sends rule-mode tick starts and decisions, along with received scoreboard results and per-tick metrics, through `src.events.emit()`. CrewAI runs stream their triage, agent reports, and decision from `src.crew`; rule fallbacks also emit a complete lifecycle. These events update the dashboard only: the UI bridge does not trigger a tick or submit a decision. A separate trigger integration is still needed before the dashboard can start an actual tick.

- `POST /emit`: accepts a JSON object and queues the unchanged event for connected browsers. Responds with `{"ok": true, "clients": N}`; this acknowledges local queueing, not browser receipt.
- WebSocket `/ws`: streams those JSON events in order for each connected browser.
- `GET /`: serves `ui/index.html`, regardless of the terminal's working directory.
- `GET /assets/...`: serves the local JavaScript, styles, and vendored Three.js modules.

Event names remain unchanged: `tick_start`, `agent_log`, `decision`, `score`, `dispatch`, and `metrics`. Partial objects and future event names are forwarded without alteration. The browser tolerates missing fields and reconnects every two seconds after a disconnection.

For `tick_start`, the browser accepts the agreed flat `sectors` array (`sector`, `emf`, `temp_c`, `motion`, `entity_class`, `confidence`, `civilians`, `containment_state`, `hot`). It also accepts the supplied fixture's `sector_reports` shape with nested `sensors` and `sighting`, `civilian_status`, and `swarm_budget`. It preserves sector identifiers and does not calculate threat classifications from sensor values. A backend `hot` assessment must be supplied to label a holding room secure or anomalous; an explicit containment breach is shown directly.

Give every event its `tick_id`, and include `facility_id` when available. Agent logs use `agent`, `sector`, and `message`. Decisions use `action`, `sector`, and `rationale`. Scores use `expected_action`, `score`, and `false_alarm`. Dispatches use `content`, `status`, and `ok` when a send was attempted; a draft is distinct from a sent or failed alert. Metrics use `total_tokens`, `cost_usd`, `latency_s`, `agents_used`, `max_agents`, and `searches`. Missing fields display as unknown. The UI does not invent scores or sponsor activity.

Discord webhook alerts are disabled by default. `main.py --send-discord` explicitly opts in, and sends an alert only after a non-dry-run, non-duplicate submission. The one-ticket read-only inspection never submits or sends alerts.

This local bridge runs in one process. It does not retain events for browsers that are disconnected. A slow browser has a bounded queue of 100 events; if it falls behind, its connection is closed and the browser reconnects. Other browsers and the agent loop continue independently.

A button that starts an actual tick needs a separate integration with Person A's loop. The event bridge only receives updates.

## Checks

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node --test tests/test_ui_state.cjs
```

The Python checks cover event delivery and the bridge. The Node checks exercise event-state handling, exact sector IDs, mapped-area aliases, unmapped decision and score retention, stale readings, late and duplicate results, mock labels, and the separation between decisions and reported outcomes. The state tests require no npm dependencies.
