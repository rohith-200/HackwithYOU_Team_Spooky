"""
rules.py: the deterministic decision rule (A1).
Later the CrewAI Flow router calls decide() too, so the final action stays predictable.

Tune the thresholds after looking at the real fixtures.
"""
import re

# ---- thresholds (tune these against the fixtures) ----
EMF_HOT = 50            # milligauss
TEMP_HOT = 5            # degrees C, colder = more haunted
MOTION_HOT = 10         # events per tick
CONFIDENCE_HOT = 0.6    # sighting confidence

CALM_WORDS = ("all clear", "clear", "safe", "none", "0 ")


def civilians_at_risk(sector: dict) -> bool:
    status = (sector.get("civilian_status") or "").lower().strip()
    if not status or any(status.startswith(w) for w in CALM_WORDS):
        return False
    if any(w in status for w in ("pinned", "trapped", "injured", "missing", "stuck")):
        return True
    nums = [int(n) for n in re.findall(r"\d+", status)]
    return any(n > 0 for n in nums)


def threat_score(sector: dict) -> int:
    """0 = calm. Higher = worse. Each warning sign adds a point."""
    s = sector.get("sensors") or {}
    sighting = sector.get("sighting") or {}
    score = 0
    if (sector.get("containment_state") or "").lower() == "breached":
        score += 3
    if (s.get("emf_milligauss") or 0) >= EMF_HOT:
        score += 1
    if s.get("temp_c") is not None and s["temp_c"] <= TEMP_HOT:
        score += 1
    if (s.get("motion_events") or 0) >= MOTION_HOT:
        score += 1
    if (sighting.get("confidence") or 0) >= CONFIDENCE_HOT:
        score += 1
    return score


HOT_THRESHOLD = 3   # breach alone = hot; otherwise need 3+ warning signs

def is_hot(sector: dict) -> bool:
    return threat_score(sector) >= HOT_THRESHOLD


def assess_sector(sector: dict) -> dict:
    return {
        "sector": sector.get("sector", "unknown"),
        "hot": is_hot(sector),
        "threat": threat_score(sector),
        "civilians_at_risk": civilians_at_risk(sector),
    }


def decide(tick: dict) -> dict:
    """Return {action, sector, rationale, assessments} for one tick."""
    sectors = tick.get("sector_reports") or []
    assessments = [assess_sector(s) for s in sectors]
    hot = [a for a in assessments if a["hot"]]

    if not hot:
        return {"action": "observe", "sector": None,
                "rationale": "All sectors calm", "assessments": assessments}

    worst = max(hot, key=lambda a: (a["civilians_at_risk"], a["threat"]))
    if worst["civilians_at_risk"]:
        action, why = "evacuate", "civilians at risk in a hot sector"
    else:
        action, why = "contain", "hot sector with no civilians at risk"

    return {"action": action, "sector": worst["sector"],
            "rationale": f"{worst['sector']}: {why} (threat {worst['threat']})",
            "assessments": assessments}