"""Parasail schema and validation for the CrewAI Commander."""

from __future__ import annotations

from typing import Any

from .llm import ParasailSettings, chat_json

COMMANDER_SCHEMA = {
    "name": "commander_decision",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["contain", "evacuate", "observe"]},
            "sector": {"type": "string"},
            "rationale": {"type": "string"},
            "serious": {"type": "boolean"},
        },
        "required": ["action", "sector", "rationale", "serious"],
    },
}


def commander_decision(
    tick_text: str,
    evidence: str,
    *,
    settings: ParasailSettings | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Produce the Commander recommendation used by Person A's router."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are the Watchtower Commander. Analyze the incident and return only the required JSON. "
                "Recommend evacuate when civilians are pinned/trapped and threat is breached/high; "
                "recommend contain for a serious breach without civilians at risk; otherwise observe. "
                "Never invent sensor values."
            ),
        },
        {"role": "user", "content": f"INCIDENT:\n{tick_text}\n\nGROUNDED EVIDENCE:\n{evidence}"},
    ]
    result = chat_json(messages, COMMANDER_SCHEMA, settings=settings, client=client)
    validate_commander_output(result)
    return result


def validate_commander_output(value: dict[str, Any]) -> None:
    required = {"action", "sector", "rationale", "serious"}
    missing = required - value.keys()
    if missing:
        raise ValueError(f"Commander output missing fields: {sorted(missing)}")
    if value["action"] not in {"contain", "evacuate", "observe"}:
        raise ValueError(f"Invalid Commander action: {value['action']}")
    if not isinstance(value["sector"], str) or not value["sector"].strip():
        raise ValueError("Commander sector must be a non-empty string")
    if not isinstance(value["rationale"], str) or not value["rationale"].strip():
        raise ValueError("Commander rationale must be a non-empty string")
    if not isinstance(value["serious"], bool):
        raise ValueError("Commander serious must be boolean")
