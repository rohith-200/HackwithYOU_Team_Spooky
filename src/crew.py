"""
crew.py: the Ghost Ops swarm (A3).

Flow:  triage -> router -> hot crew (5 agents) | calm crew (3 agents) -> finalize
Each agent sees only its slice of the tick (partial knowledge).
Final action = agents' findings through the decision table; rules.py guards against disagreement.

Use from main.py:   decision = await run_tick(tick)
"""
import json
import os
from typing import Literal, Optional

from crewai import Agent, Crew, Process, Task
from crewai.flow.flow import Flow, listen, or_, router, start
from pydantic import BaseModel

from src.llm import get_llm
from src.rules import decide as rule_decide
from src.rules import is_hot as rule_is_hot

TEAM = os.environ.get("TEAM_HANDLE", "team-xx").lower()
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

# ---------- optional pieces from teammates (work without them) ----------
try:
    from src.triage import is_hot as triage_is_hot   # Person B: embeddings triage
except Exception:
    triage_is_hot = rule_is_hot

LORE_TOOLS = []
try:
    from src.tools.youcom import youcom_search        # Person B: You.com tool
    LORE_TOOLS.append(youcom_search)
except Exception:
    pass

try:
    from src.events import emit                       # Person C: UI events
except Exception:
    def emit(event):  # noqa: E306
        pass


def ui(event: dict):
    try:
        emit(event)
    except Exception:
        pass


# ---------- structured outputs ----------
class SectorThreat(BaseModel):
    sector: str
    hot: bool
    reason: str

class SensorReport(BaseModel):
    sectors: list[SectorThreat]

class SectorPeople(BaseModel):
    sector: str
    at_risk: bool
    reason: str

class CivilianReport(BaseModel):
    sectors: list[SectorPeople]

class LoreReport(BaseModel):
    entity_class: str
    summary: str
    countermeasures: list[str]
    sources: list[str]

class CommanderDecision(BaseModel):
    action: Literal["contain", "evacuate", "observe"]
    sector: Optional[str] = None
    rationale: str
    serious: bool

class DispatchDraft(BaseModel):
    message: str


# ---------- agents ----------
# Model per agent (override any of these in .env if an ID differs on your key)
MODEL_FAST = os.environ.get("MODEL_FAST", "parasail-glm-53-flash")          # default swarm brain
MODEL_CHEAP = os.environ.get("MODEL_CHEAP", "parasail-deepseek-v4-flash")   # simple jobs
MODEL_REASON = os.environ.get("MODEL_REASON", "parasail-deepseek-v41-flash")  # final call

AGENT_MODELS = {
    "Sensor Analyst": MODEL_FAST,
    "Civilian Officer": MODEL_CHEAP,
    "Lore Researcher": MODEL_FAST,
    "Commander": MODEL_REASON,
    "Dispatcher": MODEL_CHEAP,
}


def _agent(role, goal, backstory, tools=None):
    return Agent(role=role, goal=goal, backstory=backstory, tools=tools or [],
                 llm=get_llm(AGENT_MODELS.get(role)), allow_delegation=False,
                 max_iter=3, verbose=False)

def sensor_agent():
    return _agent("Sensor Analyst",
                  "Judge which sectors show a real paranormal threat from sensor data alone.",
                  "You read EMF, temperature, motion and sightings. You know harmless control "
                  "readings exist, so you only call a sector hot on strong, consistent evidence.")

def civilian_agent():
    return _agent("Civilian Officer",
                  "Decide which sectors have people at risk.",
                  "You only see civilian status and containment state. People first, always.")

def lore_agent():
    return _agent("Lore Researcher",
                  "Find what an entity is and how to counter it, with sources.",
                  "You research entities on the web and cite where facts came from.",
                  tools=LORE_TOOLS)

def commander_agent():
    return _agent("Commander",
                  "Choose one action for the tick: contain, evacuate or observe.",
                  "Evacuate when people are at risk in a hot sector, contain a hot sector with no "
                  "one at risk, observe when nothing is hot. False alarms are costly.")

def dispatcher_agent():
    return _agent("Dispatcher",
                  "Write a one-line incident alert for the venue Discord channel.",
                  "You write short, factual alerts with the numbers that matter. No emojis.")


# ---------- tick slices (partial knowledge) ----------
def sensor_view(tick):
    return [{"sector": s.get("sector"), "sensors": s.get("sensors"), "sighting": s.get("sighting")}
            for s in tick.get("sector_reports", [])]

def civilian_view(tick):
    return [{"sector": s.get("sector"), "civilian_status": s.get("civilian_status"),
             "containment_state": s.get("containment_state")}
            for s in tick.get("sector_reports", [])]

def hot_entities(tick):
    return sorted({(s.get("sighting") or {}).get("entity_class")
                   for s in tick.get("sector_reports", [])
                   if triage_is_hot(s) and (s.get("sighting") or {}).get("entity_class")})


# ---------- tasks ----------
def sensor_task(agent, tick):
    return Task(description=f"Sensor data per sector (JSON): {json.dumps(sensor_view(tick))}\n"
                            "For EVERY sector decide hot true/false with a one-line reason.",
                expected_output="A hot/not-hot verdict and reason for every sector.",
                agent=agent, output_pydantic=SensorReport, async_execution=True)

def civilian_task(agent, tick):
    return Task(description=f"Civilian data per sector (JSON): {json.dumps(civilian_view(tick))}\n"
                            "For EVERY sector decide at_risk true/false with a one-line reason.",
                expected_output="An at-risk verdict and reason for every sector.",
                agent=agent, output_pydantic=CivilianReport, async_execution=True)

def lore_task(agent, entities):
    return Task(description=f"Entities sighted in hot sectors: {', '.join(entities) or 'unknown'}.\n"
                            "Find what the main entity is and the best countermeasures. Cite sources.",
                expected_output="Entity summary, countermeasures and source URLs.",
                agent=agent, output_pydantic=LoreReport, async_execution=True)

def commander_task(agent, context):
    return Task(description="Using your team's reports, choose ONE action for this tick and the "
                            "sector it is about. Set serious=true only if a sector is hot. "
                            "rationale: at most 2 short sentences, under 250 characters.",
                expected_output="action, sector, rationale, serious.",
                agent=agent, context=context, output_pydantic=CommanderDecision)

def dispatch_task(agent, context):
    return Task(description="Write ONE sentence, under 160 characters: entity, key sensor numbers, "
                            "civilian situation. No team name, no orders, no extra sentences.",
                expected_output="One alert line.", agent=agent, context=context,
                output_pydantic=DispatchDraft)


def _pyd(task):
    out = getattr(task, "output", None)
    return getattr(out, "pydantic", None) if out else None


# ---------- token counting: every model call goes through LiteLLM ----------
_LLM_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}


def _track_usage(kwargs, response, start_time, end_time):
    u = getattr(response, "usage", None)
    if u is None and isinstance(response, dict):
        u = response.get("usage")
    if u is None:
        return
    get = (lambda k: u.get(k, 0)) if isinstance(u, dict) else (lambda k: getattr(u, k, 0))
    _LLM_USAGE["prompt_tokens"] += get("prompt_tokens") or 0
    _LLM_USAGE["completion_tokens"] += get("completion_tokens") or 0
    _LLM_USAGE["requests"] += 1


try:
    import litellm
    if _track_usage not in litellm.success_callback:
        litellm.success_callback.append(_track_usage)
except Exception:
    pass


def _usage_snapshot() -> dict:
    return dict(_LLM_USAGE)


def _usage_since(before: dict) -> dict:
    return {k: _LLM_USAGE[k] - before.get(k, 0) for k in _LLM_USAGE}


def _usage(result, crew=None) -> dict:
    u = getattr(crew, "usage_metrics", None) if crew is not None else None
    if not u or not getattr(u, "total_tokens", 0):
        u = getattr(result, "token_usage", None)
    return {"prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
            "requests": getattr(u, "successful_requests", 0) or 0}


# ---------- the flow ----------
class GhostOpsFlow(Flow):

    @start()
    def triage(self):
        tick = self.state["tick"]
        hot = [s.get("sector") for s in tick.get("sector_reports", []) if triage_is_hot(s)]
        self.state["hot_sectors"] = hot
        self.state["max_agents"] = (tick.get("swarm_budget") or {}).get("max_active_agents", 8)
        ui({"type": "tick_start", "tick_id": tick.get("tick_id"), "facility_id": tick.get("facility_id"),
            "max_agents": self.state["max_agents"],
            "credits": (tick.get("swarm_budget") or {}).get("api_credits_remaining"),
            "sectors": [{"sector": s.get("sector"),
                         "emf": (s.get("sensors") or {}).get("emf_milligauss"),
                         "temp_c": (s.get("sensors") or {}).get("temp_c"),
                         "motion": (s.get("sensors") or {}).get("motion_events"),
                         "entity_class": (s.get("sighting") or {}).get("entity_class"),
                         "confidence": (s.get("sighting") or {}).get("confidence"),
                         "civilians": s.get("civilian_status"),
                         "containment_state": s.get("containment_state"),
                         "hot": s.get("sector") in hot} for s in tick.get("sector_reports", [])]})
        return hot

    @router(triage)
    def route(self):
        return "hot" if self.state["hot_sectors"] else "calm"

    @listen("hot")
    async def run_hot(self):
        tick = self.state["tick"]
        s_ag, c_ag, l_ag, cmd_ag, d_ag = (sensor_agent(), civilian_agent(), lore_agent(),
                                           commander_agent(), dispatcher_agent())
        st, ct = sensor_task(s_ag, tick), civilian_task(c_ag, tick)
        lt = lore_task(l_ag, hot_entities(tick))
        cmd = commander_task(cmd_ag, [st, ct, lt])
        dt = dispatch_task(d_ag, [st, ct, lt, cmd])
        agents, tasks = [s_ag, c_ag, l_ag, cmd_ag, d_ag], [st, ct, lt, cmd, dt]
        if len(agents) > self.state["max_agents"]:   # respect the budget: drop research first
            agents, tasks = [s_ag, c_ag, cmd_ag, d_ag], [st, ct, cmd, dt]
            cmd.context = [st, ct]
        crew = Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=False)
        before = _usage_snapshot()
        result = await crew.kickoff_async()
        counted = _usage_since(before)
        self.state.update(sensor=_pyd(st), civilian=_pyd(ct), lore=_pyd(lt) if lt in tasks else None,
                          commander=_pyd(cmd), draft=_pyd(dt), agents_used=len(agents),
                          usage=counted if counted["requests"] else _usage(result, crew))

    @listen("calm")
    async def run_calm(self):
        tick = self.state["tick"]
        s_ag, c_ag, cmd_ag = sensor_agent(), civilian_agent(), commander_agent()
        st, ct = sensor_task(s_ag, tick), civilian_task(c_ag, tick)
        cmd = commander_task(cmd_ag, [st, ct])
        crew = Crew(agents=[s_ag, c_ag, cmd_ag], tasks=[st, ct, cmd],
                    process=Process.sequential, verbose=False)
        before = _usage_snapshot()
        result = await crew.kickoff_async()
        counted = _usage_since(before)
        self.state.update(sensor=_pyd(st), civilian=_pyd(ct), lore=None, commander=_pyd(cmd),
                          draft=None, agents_used=3,
                          usage=counted if counted["requests"] else _usage(result, crew))

    @listen(or_(run_hot, run_calm))
    def finalize(self):
        tick, tid = self.state["tick"], self.state["tick"].get("tick_id")
        sensor, civ, lore, cmd = (self.state.get(k) for k in ("sensor", "civilian", "lore", "commander"))

        for name, rep in (("sensor", sensor), ("civilian", civ), ("lore", lore), ("commander", cmd)):
            if rep is not None:
                ui({"type": "agent_log", "tick_id": tid, "agent": name, "sector": None,
                    "message": rep.model_dump_json()[:400]})

        # decision table on the agents' findings
        hot = {x.sector for x in sensor.sectors if x.hot} if sensor else set()
        risk = {x.sector for x in civ.sectors if x.at_risk} if civ else set()
        if hot & risk:
            action, sector = "evacuate", sorted(hot & risk)[0]
        elif hot:
            action, sector = "contain", sorted(hot)[0]
        else:
            action, sector = "observe", None

        rule = rule_decide(tick)                       # guard: rule wins on disagreement
        overridden = rule["action"] != action
        if overridden:
            action, sector = rule["action"], rule["sector"]

        rationale = (cmd.rationale if cmd else rule["rationale"])
        two_key = bool(sector) and sector in hot and bool(cmd and cmd.serious)
        alert = None
        if action != "observe" and two_key:
            line = self.state["draft"].message if self.state.get("draft") else rationale
            line = line.strip().split("\n")[0][:160]
            alert = f"{TEAM} · {sector} · {action} · {line}"[:240]

        decision = {**rule, "action": action, "sector": sector, "rationale": rationale,
                    "source": "swarm" + (" (rule override)" if overridden else ""),
                    "alert": alert, "agents_used": self.state.get("agents_used", 0),
                    "usage": self.state.get("usage", {}),
                    "lore_sources": lore.sources if lore else []}
        ui({"type": "decision", "tick_id": tid, "action": action, "sector": sector,
            "rationale": rationale})
        return decision


async def run_tick(tick: dict) -> dict:
    """Run the swarm on one tick. Never raises: falls back to the rule on any error."""
    try:
        flow = GhostOpsFlow()
        return await flow.kickoff_async(inputs={"tick": tick})
    except Exception as e:
        d = rule_decide(tick)
        d.update(source=f"rule fallback ({type(e).__name__}: {str(e)[:120]})",
                 alert=None, agents_used=0, usage={}, lore_sources=[])
        return d