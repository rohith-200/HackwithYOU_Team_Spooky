"""Sector triage using Parasail embeddings and deterministic thresholds."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from .llm import ParasailSettings, embed_texts


@dataclass(frozen=True)
class TriageResult:
    hot: bool
    similarity: float
    nearest_label: str


def sector_text(report: dict[str, Any]) -> str:
    """Serialize an organizer-shaped sector report without renaming fields."""
    sensors = report.get("sensors") or {}
    sighting = report.get("sighting") or {}
    return (
        f"sector={report.get('sector', 'unknown')}; "
        f"emf={sensors.get('emf_milligauss', 'unknown')}; "
        f"temperature={sensors.get('temp_c', 'unknown')}C; "
        f"motion={sensors.get('motion_events', 'unknown')}; "
        f"entity={sighting.get('entity_class', 'none')}; "
        f"confidence={sighting.get('confidence', 0)}; "
        f"civilians={report.get('civilian_status', 'unknown')}; "
        f"containment={report.get('containment_state', 'unknown')}"
    )


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        raise ValueError("Embedding vectors must be non-empty and the same length")
    numerator = sum(x * y for x, y in zip(a, b))
    denominator = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return numerator / denominator if denominator else 0.0


def triage_sector(
    report: dict[str, Any],
    *,
    calm_examples: list[str],
    danger_examples: list[str],
    settings: ParasailSettings | None = None,
    embedding_client: Any | None = None,
    hot_threshold: float = 0.72,
) -> TriageResult:
    """Classify a sector against calm/danger examples using Parasail."""
    text = sector_text(report)
    examples = [("calm", value) for value in calm_examples] + [
        ("danger", value) for value in danger_examples
    ]
    if not examples:
        raise ValueError("At least one calm or danger example is required")
    vectors = embed_texts([text] + [value for _, value in examples], settings=settings, client=embedding_client)
    current = vectors[0]
    nearest_label, nearest_vector = max(
        zip((label for label, _ in examples), vectors[1:]),
        key=lambda item: cosine_similarity(current, item[1]),
    )
    similarity = cosine_similarity(current, nearest_vector)
    explicitly_breached = report.get("containment_state") == "breached"
    pinned = any(word in str(report.get("civilian_status", "")).lower() for word in ("pinned", "trapped"))
    hot = nearest_label == "danger" and similarity >= hot_threshold
    return TriageResult(hot=bool(hot or explicitly_breached or pinned), similarity=similarity, nearest_label=nearest_label)
