/**
 * Local, fictional incident replays for the command table.
 * Every event is explicitly marked as a mock. This module performs no I/O.
 * Sector IDs are preserved exactly; archive_annex is an invented demo sector.
 */

const SCENARIOS = new Set(["breach", "control", "unknown"]);

function scenarioName(value) {
  return SCENARIOS.has(value) ? value : "breach";
}

function event(type, tickId, details = {}) {
  return {
    type,
    tick_id: tickId,
    ...details,
    _mock: true,
    demo_label: "[DEMO] Fictional local replay",
  };
}

function baseSectors() {
  return [
    {
      sector: "catering_coffee_bar",
      emf: 8,
      temp_c: 21,
      motion: 2,
      entity_class: null,
      confidence: null,
      civilians: "clear",
      containment_state: "holding",
      hot: false,
    },
    {
      sector: "main_stage",
      emf: 12,
      temp_c: 19,
      motion: 2,
      entity_class: null,
      confidence: null,
      civilians: "clear",
      containment_state: "holding",
      hot: false,
    },
    {
      sector: "entrance",
      emf: 5,
      temp_c: 20,
      motion: 3,
      entity_class: null,
      confidence: null,
      civilians: "clear",
      containment_state: "holding",
      hot: false,
    },
  ];
}

function tick(scenario, includeNewSector = false) {
  const sectors = baseSectors();
  const tickId = { breach: 142, control: 900, unknown: 218 }[scenario];

  if (scenario === "breach") {
    Object.assign(sectors[0], {
      emf: 98,
      temp_c: -4,
      motion: 17,
      entity_class: "level-5-phantom",
      confidence: 0.82,
      civilians: "4 pinned",
      containment_state: "breached",
      hot: true,
    });
  }

  if (scenario === "control") {
    Object.assign(sectors[1], {
      emf: 3,
      temp_c: 21,
      motion: 41,
      entity_class: "human-crowd",
      confidence: 0.97,
    });
  }

  if (scenario === "unknown" && includeNewSector) {
    sectors.push({
      sector: "archive_annex",
      emf: 76,
      temp_c: 2,
      motion: 12,
      entity_class: "unclassified-anomaly",
      confidence: 0.71,
      civilians: "clear",
      containment_state: "breached",
      hot: true,
    });
  }

  return event("tick_start", tickId, {
    facility_id: "aws-builder-loft-sf",
    sectors,
    max_agents: 8,
    credits: 45000,
    scenario: `[DEMO] ${scenario}`,
    layout_note: "[DEMO] Fictional schematic; room positions are illustrative.",
  });
}

/** Initial scene only: no decision, score, dispatch, or claimed outcome. */
export function getDemoPreview(scenario = "breach") {
  return tick(scenarioName(scenario));
}

/** Delay is milliseconds before this event, not an absolute timestamp. */
export function getDemoSequence(scenario = "breach") {
  const name = scenarioName(scenario);
  const initial = tick(name, name === "unknown");
  const tickId = initial.tick_id;
  const sector = {
    breach: "catering_coffee_bar",
    control: "main_stage",
    unknown: "archive_annex",
  }[name];
  const stages = [{ delay: 400, event: initial }];

  function add(type, details, delay = 950) {
    stages.push({ delay, event: event(type, tickId, details) });
  }

  function report(agent, message, delay = 950) {
    add("agent_log", { agent, sector, message: `[DEMO] ${message}` }, delay);
  }

  if (name === "control") {
    // Four agents work; research is intentionally skipped for this clear case.
    report("sensor", "Motion spike detected: cross-checking the other signals.");
    report(
      "sensor",
      "41 motion events match a human crowd (97% confidence). EMF 3 mG and 21 C do not corroborate a breach.",
    );
    report("civilian", "Civilian status is clear and containment is holding.");
    report("commander", "Evidence supports observation. Skip lore research and preserve the agent budget.");
    add("decision", {
      sector,
      action: "observe",
      rationale: "[DEMO] Human crowd motion with normal EMF, stable temperature, clear civilians, and holding containment.",
    });
    report("dispatcher", "Escalation suppressed: no Discord message is warranted or sent.");
    add("score", {
      expected_action: "observe",
      score: 1,
      false_alarm: false,
    });
    add("metrics", {
      total_tokens: 920,
      cost_usd: 0.0016,
      latency_s: 1.9,
      agents_used: 4,
      max_agents: 8,
      searches: 0,
    });
    return stages;
  }

  if (name === "unknown") {
    report("sensor", "Previously unseen sector ID archive_annex received. EMF 76 mG, 2 C, and 12 motion events support a real anomaly within this fictional replay.");
    report("lore", "Simulated context review: entity is unclassified. No verified lore is claimed and no search was performed.");
    report("civilian", "The reported civilian status is clear. No civilians are reported at risk in this sector.");
    report("commander", "Choose containment for the hot sector with no reported civilians at risk. Spatial placement has no effect on the decision.");
    add("decision", {
      sector,
      action: "contain",
      rationale: "[DEMO] Breached containment and converging signals require containment; civilian status is clear. Archive annex is a fictional demonstration sector.",
    });
  } else {
    report("sensor", "98 mG EMF, -4 C, and 17 motion events converge on the breached coffee bar.");
    report("lore", "Simulated evidence review associates the reported level-5 phantom with high urgency. No web search or external model call was performed.");
    report("civilian", "Four civilians are reported pinned. Evacuation is recommended; rescue has not been confirmed.");
    report("commander", "Combine the sensor and civilian reports: prioritize evacuation and authorize a simulated escalation.");
    add("decision", {
      sector,
      action: "evacuate",
      rationale: "[DEMO] Breached containment, corroborating sensor readings, and four pinned civilians warrant evacuation. This is a recommendation, not a confirmed rescue.",
    });
  }

  const action = name === "unknown" ? "contain" : "evacuate";
  report("dispatcher", "Simulating the approved escalation locally. No external services are contacted.");
  add("dispatch", {
    sector,
    content: `[DEMO — NOT SENT] Tick ${tickId}: ${action.toUpperCase()} ${sector}. ${name === "breach" ? "Four civilians reported pinned." : "Civilian status clear; fictional sector."} Simulated delivery only.`,
    ok: true,
    simulated: true,
  });
  add("score", {
    expected_action: action,
    score: 1,
    false_alarm: false,
  });
  add("metrics", {
    total_tokens: name === "unknown" ? 1610 : 1840,
    cost_usd: name === "unknown" ? 0.0028 : 0.0032,
    latency_s: name === "unknown" ? 3.2 : 3.8,
    agents_used: 5,
    max_agents: 8,
    searches: 0,
  });
  return stages;
}
