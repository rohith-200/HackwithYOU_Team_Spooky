"""Replay labelled, fictional UI events without running agents or sending alerts.

Run ``python ui/mock_replay.py`` after starting the UI server. The default replay
contains two ticks, sends one event every two seconds, and then exits. Set
``--cycles 0`` to repeat until interrupted. Only ``UI_URL/emit`` is contacted;
scores, model usage, and Discord delivery in this replay are simulated.
"""

import argparse
import json
import math
import os
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_UI_URL = "http://localhost:8000"
REQUEST_TIMEOUT_SECONDS = 2.0


def sample_events(first_tick_id: int = 142) -> list[dict[str, Any]]:
    """Return all six event types across an outbreak and a quiet follow-up tick."""
    events: list[dict[str, Any]] = []
    for offset, hot in enumerate((True, False)):
        tick_id = first_tick_id + offset
        sector = "catering_coffee_bar"
        events.append(
            {
                "type": "tick_start",
                "tick_id": tick_id,
                "facility_id": "aws-builder-loft-sf",
                "sectors": [
                    {
                        "sector": sector,
                        "emf": 98 if hot else 8,
                        "temp_c": -4 if hot else 21,
                        "motion": 17 if hot else 1,
                        "entity_class": "level-5-phantom" if hot else None,
                        "confidence": 0.82 if hot else None,
                        "civilians": "4 pinned" if hot else "4 safe",
                        "containment_state": "breached" if hot else "intact",
                        "hot": hot,
                    }
                ],
                "max_agents": 8,
                "credits": 45000,
            }
        )

        reports = (
            (
                "sensor",
                "EMF, temperature, and motion agree on a serious anomaly."
                if hot
                else "Readings are stable and within the fictional baseline.",
            ),
            (
                "lore",
                "Fictional protocol review supports intervention; no search was run."
                if hot
                else "No new anomaly needs research; no search was run.",
            ),
            (
                "civilian",
                "Four civilians are in the affected sector; evacuation is recommended."
                if hot
                else "Four civilians are safe; no evacuation is recommended.",
            ),
            (
                "commander",
                "Combine the specialist reports and choose evacuation."
                if hot
                else "Combine the specialist reports and continue observation.",
            ),
        )
        for agent, message in reports:
            events.append(
                {
                    "type": "agent_log",
                    "tick_id": tick_id,
                    "agent": agent,
                    "sector": sector,
                    "message": f"[MOCK] {message}",
                }
            )

        action = "evacuate" if hot else "observe"
        events.append(
            {
                "type": "decision",
                "tick_id": tick_id,
                "action": action,
                "sector": sector,
                "rationale": (
                    "[MOCK] A breach and converging sensor evidence put civilians at risk."
                    if hot
                    else "[MOCK] Stable readings and intact containment support observation."
                ),
            }
        )
        events.append(
            {
                "type": "agent_log",
                "tick_id": tick_id,
                "agent": "dispatcher",
                "sector": sector,
                "message": (
                    "[MOCK] Simulating an approved escalation; Discord is not contacted."
                    if hot
                    else "[MOCK] No escalation is warranted; Discord is not contacted."
                ),
            }
        )
        if hot:
            events.append(
                {
                    "type": "dispatch",
                    "tick_id": tick_id,
                    "content": (
                        "[MOCK ONLY — NOT SENT] Tick "
                        f"{tick_id}: evacuate catering_coffee_bar; four civilians at risk."
                    ),
                    "ok": True,
                }
            )
        events.extend(
            [
                {
                    "type": "score",
                    "tick_id": tick_id,
                    "expected_action": action,
                    "score": 1.0,
                    "false_alarm": False,
                },
                {
                    "type": "metrics",
                    "tick_id": tick_id,
                    "total_tokens": 1840 if hot else 920,
                    "cost_usd": 0.0032 if hot else 0.0016,
                    "latency_s": 3.8 if hot else 1.9,
                    "agents_used": 5 if hot else 4,
                    "max_agents": 8,
                    "searches": 1 if hot else 0,
                },
            ]
        )
    return events


def post_event(endpoint: str, event: dict[str, Any]) -> None:
    """Send a mock event, surfacing delivery failures to the replay operator."""
    payload = json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS):
        pass


def nonnegative_interval(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or interval < 0:
        raise argparse.ArgumentTypeError("interval must be a finite number >= 0")
    return interval


def nonnegative_cycles(value: str) -> int:
    cycles = int(value)
    if cycles < 0:
        raise argparse.ArgumentTypeError("cycles must be >= 0 (0 means repeat forever)")
    return cycles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interval",
        type=nonnegative_interval,
        default=2.0,
        help="seconds between mock events (default: 2)",
    )
    parser.add_argument(
        "--cycles",
        type=nonnegative_cycles,
        default=1,
        help="two-tick replay cycles; 0 repeats forever (default: 1)",
    )
    args = parser.parse_args(argv)
    base_url = os.environ.get("UI_URL", "").strip() or DEFAULT_UI_URL
    endpoint = f"{base_url.rstrip('/')}/emit"
    print(f"MOCK REPLAY -> {endpoint}", flush=True)
    print(
        "All scores, usage, and delivery statuses are fictional. "
        "No agents, scenario API, or Discord calls are made.",
        flush=True,
    )

    cycle = 0
    sent = 0
    try:
        while args.cycles == 0 or cycle < args.cycles:
            for event in sample_events(142 + cycle * 2):
                if sent:
                    time.sleep(args.interval)
                post_event(endpoint, event)
                sent += 1
                print(
                    f"[MOCK] tick {event['tick_id']}: {event['type']}",
                    flush=True,
                )
            cycle += 1
    except KeyboardInterrupt:
        print(f"\nMock replay stopped after {sent} events.", flush=True)
        return 0
    except (OSError, URLError, ValueError) as exc:
        print(
            f"Mock replay could not deliver to {endpoint}: {exc}. "
            "Start the UI server and check UI_URL, then retry.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(f"Mock replay complete: {sent} events across {cycle * 2} ticks.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
