"""
crew.py: the Ghost Ops swarm (A3).

Flow:  triage -> router -> hot crew (5 agents) | calm crew (3 agents) -> finalize
Each agent sees only its slice of the tick (partial knowledge).
Final action = agents' findings through the decision table; rules.py guards against disagreement.

Use from main.py:   decision = await run_tick(tick)
"""
import asyncio
import json
import os
from typing import Literal, Optional

from crewai import Agent, Crew, Process, Task
from crewai.flow.flow import Flow, listen, or_, router, start
from pydantic import BaseModel

from dotenv import load_dotenv

load_dotenv()

from src.rules import decide as rule_decide          # noqa: E402
from src.rules import is_hot as rule_is_hot          # noqa: E402

# LLM: works with A's temporary llm.py (get_llm) or B's llm.py (ParasailSettings)
try:
    from src.llm import get_llm                      # A's temporary version
except ImportError:
    from dataclasses import replace as _replace
    from src.llm import ParasailSettings, create_crewai_llm   # Person B's version

    def get_llm(model=None, temperature=0.2):
        settings = ParasailSettings.from_env()
        return create_crewai_llm(_replace(settings, model=model) if model else settings)

TEAM = os.environ.get("TEAM_HANDLE", "team-xx").lower()
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

# ---------- optional pieces from teammates (work without them) ----------
# --- Person B: embeddings triage (falls back to the rule if embeddings fail) ---
CALM_EXAMPLES = [
    "sector=lobby; emf=2; temperature=21C; motion=5; entity=none; confidence=0; civilians=all clear; containment=secure",
    "sector=main_stage; emf=4; temperature=20C; motion=40; entity=human crowd; confidence=0.95; civilians=all clear; containment=holding",
]
DANGER_EXAMPLES = [
    "sector=kitchen; emf=95; temperature=-5C; motion=20; entity=level-5-phantom; confidence=0.85; civilians=3 pinned; containment=breached",
    "sector=storage; emf=80; temperature=-2C; motion=15; entity=poltergeist; confidence=0.8; civilians=all clear; containment=breached",
]
_TRIAGE_CACHE: dict[str, dict] = {}

try:
    from src.triage import sector_text, triage_sector

    def triage_info(sector: dict) -> dict:
        key = sector_text(sector)
        if key not in _TRIAGE_CACHE:
            try:
                r = triage_sector(sector, calm_examples=CALM_EXAMPLES, danger_examples=DANGER_EXAMPLES)
                _TRIAGE_CACHE[key] = {"hot": r.hot, "similarity": round(r.similarity, 3),
                                      "label": r.nearest_label, "method": "embeddings"}
            except Exception as e:
                _TRIAGE_CACHE[key] = {"hot": rule_is_hot(sector), "similarity": None,
                                      "label": None, "method": f"rule ({type(e).__name__})"}
        return _TRIAGE_CACHE[key]
except Exception:
    def triage_info(sector: dict) -> dict:
        return {"hot": rule_is_hot(sector), "similarity": None, "label": None, "method": "rule"}


def triage_is_hot(sector: dict) -> bool:
    return triage_info(sector)["hot"]


# --- Person B: You.com grounding, called directly AND offered as a CrewAI tool ---
LORE_TOOLS = []
try:
    from crewai.tools import tool
    from src.tools.youcom import youcom_search

    def youcom_evidence(query: str) -> tuple[str, list[str]]:
        results = youcom_search(query)
        text = "\n\n".join(f"[{i + 1}] {r.get('title', '')}\n{r.get('url', '')}\n{r.get('snippet', '')[:600]}"
                             for i, r in enumerate(results))
        return text, [r.get("url", "") for r in results if r.get("url")]

    @tool("youcom_web_search")
    def youcom_search_tool(query: str) -> str:
        """Search the live web with You.com. Returns titles, URLs and snippets to cite."""
        return youcom_evidence(query)[0] or "No results."

    LORE_TOOLS.append(youcom_search_tool)
except Exception:
    def youcom_evidence(query: str) -> tuple[str, list[str]]:
        return "", []

# --- Person B: schema-locked Commander, used as a backup ---
try:
    from src.commander_output import commander_decision as b_commander_decision
except Exception:
    b_commander_decision = None

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


def _agent(role, goal, backstory, tools=None, max_iter=3):
    return Agent(role=role, goal=goal, backstory=backstory, tools=tools or [],
                 llm=get_llm(AGENT_MODELS.get(role)), allow_delegation=False,
                 max_iter=max_iter, verbose=False)

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
                  "You research entities on the web and cite where facts came from. "
                  "You already receive You.com evidence; search again only if it is empty.",
                  tools=LORE_TOOLS, max_iter=5)

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

def lore_task(agent, entities, evidence=""):
    return Task(description=f"Entities sighted in hot sectors: {', '.join(entities) or 'unknown'}.\n"
                            f"Web evidence from You.com:\n{evidence or '(none yet: use your search tool)'}\n\n"
                            "Using the evidence above (do NOT search again unless it is empty), "
                            "summarize the entity and the best countermeasures in under 60 words. "
                            "Put the URLs you relied on in sources.",
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


def alert_line(tick: dict, sector: str) -> str:
    """Short factual alert built from the tick itself."""
    rep = next((r for r in tick.get("sector_reports", []) if r.get("sector") == sector), {}) or {}
    sen, sig = rep.get("sensors") or {}, rep.get("sighting") or {}
    parts = []
    if sig.get("entity_class"):
        conf = f" ({round(sig['confidence'] * 100)}%)" if sig.get("confidence") is not None else ""
        parts.append(f"{sig['entity_class']}{conf}")
    if sen.get("emf_milligauss") is not None:
        parts.append(f"EMF {sen['emf_milligauss']} mG")
    if sen.get("temp_c") is not None:
        parts.append(f"{sen['temp_c']}°C")
    if rep.get("civilian_status"):
        parts.append(str(rep["civilian_status"]))
    if rep.get("containment_state"):
        parts.append(f"containment {rep['containment_state']}")
    return ", ".join(parts)


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
    litellm.num_retries = 3          # retry overloaded / 429 / 5xx calls with backoff
    litellm.request_timeout = 90
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
        entities = hot_entities(tick)
        evidence, urls = "", []
        if entities:
            try:
                evidence, urls = youcom_evidence(f"{entities[0].replace('-', ' ')} paranormal entity countermeasures")
            except Exception as e:
                evidence = f"(You.com search failed: {type(e).__name__})"
        self.state["searches"] = 1 if entities else 0
        self.state["search_urls"] = urls
        lt = lore_task(l_ag, entities, evidence)
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

        if cmd is None and b_commander_decision is not None:   # CrewAI commander unreadable
            try:
                from dataclasses import replace as _r
                from src.llm import ParasailSettings
                b = b_commander_decision(json.dumps(tick), json.dumps(
                    {"hot": sorted(hot), "at_risk": sorted(risk)}),
                    settings=_r(ParasailSettings.from_env(), model=MODEL_REASON))
                cmd = CommanderDecision(action=b["action"], sector=b["sector"],
                                        rationale=b["rationale"], serious=b["serious"])
            except Exception:
                pass
        rationale = (cmd.rationale if cmd else rule["rationale"])
        two_key = bool(sector) and sector in hot and bool(cmd and cmd.serious)
        alert = None
        if action != "observe" and two_key:
            draft = self.state["draft"].message.strip() if self.state.get("draft") else ""
            line = draft if 0 < len(draft) <= 160 and "\n" not in draft else alert_line(tick, sector)
            alert = f"{TEAM} · {sector} · {action} · {line}"

        decision = {**rule, "action": action, "sector": sector, "rationale": rationale,
                    "source": "swarm" + (" (rule override)" if overridden else ""),
                    "alert": alert, "agents_used": self.state.get("agents_used", 0),
                    "usage": self.state.get("usage", {}),
                    "lore_sources": (lore.sources if lore and lore.sources else self.state.get("search_urls", [])),
                    "searches": self.state.get("searches", 0),
                    "triage": {sec.get("sector"): triage_info(sec) for sec in tick.get("sector_reports", [])}}
        ui({"type": "decision", "tick_id": tid, "action": action, "sector": sector,
            "rationale": rationale})
        return decision


TICK_TIMEOUT_S = float(os.environ.get("TICK_TIMEOUT_S", "150"))
RETRYABLE = ("RateLimit", "Overloaded", "overloaded", "429", "503", "Timeout", "ServiceUnavailable",
             "APIConnection", "InternalServer",
             "ConverterError", "Pydantic", "JSONDecode", "ValidationError")   # bad JSON from a model


def _is_retryable(e: Exception) -> bool:
    text = f"{type(e).__name__} {e}"
    return any(k in text for k in RETRYABLE)


async def run_tick(tick: dict) -> dict:
    """Run the swarm on one tick. Never raises.
    Try 1: normal models. Try 2 (only on overload/rate limit): every agent on the fallback model.
    Last resort: the rule decision."""
    fallback = os.environ.get("PARASAIL_FALLBACK_MODEL", "parasail-minimax-m31-flash")
    original = dict(AGENT_MODELS)
    last_error = None
    for attempt in (1, 2):
        try:
            if attempt == 2:
                for role in AGENT_MODELS:
                    AGENT_MODELS[role] = fallback
            flow = GhostOpsFlow()
            decision = await asyncio.wait_for(flow.kickoff_async(inputs={"tick": tick}),
                                              timeout=TICK_TIMEOUT_S)
            if attempt == 2:
                decision["source"] = decision.get("source", "swarm") + f" (retry on {fallback})"
            return decision
        except Exception as e:
            last_error = e
            if attempt == 1 and _is_retryable(e):
                print(f"  swarm hit {type(e).__name__}: {str(e)[:90]}; retrying tick with {fallback} ...")
                continue
            break
        finally:
            AGENT_MODELS.clear()
            AGENT_MODELS.update(original)

    d = rule_decide(tick)
    e = last_error
    d.update(source=f"rule fallback ({type(e).__name__}: {str(e)[:120]})",
             alert=None, agents_used=0, usage={}, lore_sources=[], searches=0, triage={})
    return d