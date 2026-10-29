# Watchtower — Team Spooky

**Hack with YOU: Halloween Edition · Track 1: The Exorcism Protocol**
AWS Builder Loft, San Francisco · October 28, 2026

Watchtower is a supervised AI agent swarm that contains a paranormal outbreak in a live facility simulation. For every incident tick it triages each sector, sends a team of agents with **partial knowledge** to analyze it, and decides **contain / evacuate / observe**. Live runs submit decisions to the scenario scoreboard; Discord alerts are sent only when explicitly enabled. The system records tokens, cost and latency for every tick.

Built with **CrewAI** · **Parasail** · **You.com** · **Discord webhooks** · venue by AWS

---

## How it works

```mermaid
flowchart TD
    F[Scenario feed<br/>MCP: hack26-get-tick] --> T[Triage<br/>Parasail embeddings]
    T -->|hot| H[Hot crew · 5 agents]
    T -->|calm| C[Calm crew · 3 agents]
    H --> S1[Sensor Analyst]
    H --> S2[Civilian Officer]
    H --> S3[Lore Researcher<br/>You.com search]
    S1 & S2 & S3 --> CMD[Commander]
    CMD --> DSP[Dispatcher<br/>alert text]
    C --> CS1[Sensor Analyst]
    C --> CS2[Civilian Officer]
    CS1 & CS2 --> CCMD[Commander]
    DSP --> FIN[Finalize<br/>decision table + rule guard + two-key check]
    CCMD --> FIN
    FIN --> SUB[Submit to scoreboard<br/>hack26-submit-containment]
    FIN --> DIS[Discord webhook<br/>explicitly opted in]
    FIN --> MET[Metrics + live UI events]
```

1. **Fetch** a tick from the scenario MCP server (or the published fixtures when the feed is down).
2. **Triage** each sector with Parasail embeddings against calm and danger examples. Hot ticks go to the full crew; calm ticks go to a lighter crew with no research and no alert.
3. **Analyze in parallel.** Each agent sees only its slice of the tick:
   - **Sensor Analyst** sees EMF, temperature, motion and sightings.
   - **Civilian Officer** sees civilian status and containment state.
   - **Lore Researcher** sees only the entity class, plus live You.com evidence with source URLs.
4. **Decide.** The Commander chooses an action from the team's reports. The agents' structured findings go through a fixed decision table (people at risk in a hot sector → evacuate; hot with no one at risk → contain; nothing hot → observe), and a deterministic rule guards against disagreement to prevent false alarms.
5. **Act.** In submission modes, decisions go to the scoreboard. A Discord alert is drafted only when the Sensor Analyst says the sector is hot **and** the Commander marks it serious (two-key check); sending requires the explicit `--send-discord` option.
6. **Measure.** Tokens and cost per model, You.com searches, latency, agents used vs. budget, score and false alarms are printed per tick, streamed to the UI and saved to `metrics.json`.

## How each partner is used

| Partner | Role in Watchtower |
|---|---|
| **CrewAI** | The swarm: a Flow with a router (`triage → hot/calm → finalize`), 5 role-based agents, parallel tasks, structured Pydantic outputs |
| **Parasail** | Every agent's model (three models split by job), schema-locked JSON for the backup Commander, and embeddings for sector triage |
| **You.com** | Live web grounding for the Lore Researcher: a guaranteed search per hot tick plus a search tool the agent can call, with cited source URLs |
| **Discord webhook** | Posts a drafted incident alert only when explicitly enabled with `--send-discord`; configure `DISCORD_WEBHOOK_URL` in `.env` |

## Agents and models

| Agent | Sees | Parasail model | Output |
|---|---|---|---|
| Sensor Analyst | sensors, sightings | `parasail-glm-53-flash` | hot / not hot per sector |
| Civilian Officer | civilian status, containment | `parasail-minimax-m31-flash` | at risk / not per sector |
| Lore Researcher | entity class + You.com evidence | `parasail-glm-53-flash` | entity summary, countermeasures, sources |
| Commander | the three reports | `parasail-deepseek-v41-flash` | action, sector, rationale, serious |
| Dispatcher | everything above | `parasail-minimax-m31-flash` | one-line alert |

The reasoning model is reserved for the final call; cheaper flash models handle the narrow jobs. Models are configurable in `.env`.

## Reliability

- **Budget aware:** reads `max_active_agents` from every tick; drops research first if over budget.
- **Retries:** each model call retries on overload or rate limits; if a whole tick fails (overload, timeout or malformed JSON), it is retried once on a backup Parasail model.
- **Never loses a tick:** if the swarm still fails, the deterministic rule decides, so every tick gets a correct answer.
- **Feed outages:** decisions can be queued (`--dry-run`) and replayed to the scoreboard later (`--replay`).

## Quick start

Requires **Python 3.10–3.13** (CrewAI does not support 3.14).

```bash
git clone https://github.com/rohith-200/HackwithYOU_Team_Spooky.git
cd HackwithYOU_Team_Spooky
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in your keys
python main.py --crew       # run the swarm on the fixtures (nothing is submitted)
```

### Dashboard and backend together

Start the dashboard bridge from the repository root in one terminal:

```bash
python ui/server.py
```

Open http://localhost:8000/, then run `python main.py` in a second terminal
for rule-only fixture processing, or `python main.py --crew` for the configured
swarm. The dashboard automatically selects Backend when results arrive unless
you explicitly selected Simulation. It shows the backend's measured per-tick
tokens, estimated cost, latency, searches, and agent budget. Rule-only runs use
zero model tokens and cost; fixture runs have no official score.

The bridge retains the latest tick while it is running, so refreshing or
opening the browser after a completed tick restores its metrics. Start the
bridge before the backend; restarting the bridge clears that retained state.
See [the UI setup instructions](ui/README.md) for Windows commands and the
lightweight rule-only dependency setup.

### `.env`

```properties
# keys
YDC_API_KEY=
PARASAIL_API_KEY=
DISCORD_WEBHOOK_URL=

# event
TEAM_HANDLE=team-spooky
TEAM_TOKEN=
SCENARIO_MCP=https://api.you.com/mcp/halloween-hack-26
UI_URL=http://localhost:8000

# Parasail
PARASAIL_BASE_URL=https://api.parasail.io/v1
PARASAIL_MODEL=parasail-glm-53-flash
PARASAIL_FALLBACK_MODEL=parasail-minimax-m31-flash
PARASAIL_EMBEDDING_MODEL=parasail-bge-m3
MODEL_FAST=parasail-glm-53-flash
MODEL_CHEAP=parasail-minimax-m31-flash
MODEL_REASON=parasail-deepseek-v41-flash

CREWAI_TRACING_ENABLED=false
```

Never commit `.env`.

For a **read-only live-ticket check**, copy the team handle and any issued
`TEAM_TOKEN` into `.env`. Run the rules-only check first; it fetches and analyzes
one ticket without submitting a decision or sending an alert:

```bash
.\.venv\Scripts\python.exe -m scripts.test_live_ticket
```

For CrewAI analysis, install `requirements.txt`, set `PARASAIL_API_KEY` in
`.env`, then run
`.\.venv\Scripts\python.exe -m scripts.test_live_ticket --crew`.
`YDC_API_KEY` is optional and is used only if the CrewAI path performs live
research. Do not paste credentials into chat or commit `.env`.

Both inspection commands stream their decision and measured usage to the
running dashboard. Each inspects one ticket without writing a pending
submission, posting to Discord, or creating an official score. CrewAI cost is
marked as a partial estimate because embeddings and some tool calls are not
fully tracked.

### Run modes

| Command | What it does |
|---|---|
| `python main.py` | Rule-only decisions on the fixtures; nothing submitted |
| `python main.py --crew` | Full swarm on the fixtures; nothing submitted |
| `python main.py --crew --live --dry-run` | Swarm on live ticks; decisions queued, not submitted |
| `python main.py --replay` | Submit queued decisions to the live scoreboard |
| `python main.py --crew --live` | Swarm on live ticks, submitting each decision |
| `python main.py --crew --submit-fixtures` | Swarm on the fixture ticks, submitting to the live scoreboard |

Discord sending is **off by default**. Add `--send-discord` to explicitly enable
alerts, and only use it when the run is intentionally submitting decisions.
The read-only `scripts.test_live_ticket` command never sends alerts, regardless
of `.env` contents.
To send one explicit test message without contacting the scenario feed or
submitting a scoreboard decision, run `python main.py --test-discord`. This
does post a real message to the configured webhook.

The scoreboard accepts **one answer per tick**, so the first submission is final.
Append `--send-discord` to a submission mode only when you explicitly want
Discord alerts. For example, `python main.py --crew --live --send-discord`
submits live decisions and sends qualifying alerts. Dry runs and duplicate
judgments never send alerts.

Useful checks:

```bash
python scripts/probe_feed.py     # list the scenario MCP tools and read the scoreboard
python -m src.llm                # quick Parasail check (if using a get_llm-based llm.py)
```

---

*Event: [Hack with YOU — Halloween Edition](https://youdotcom-oss.github.io/halloween-hack-2026/)*
