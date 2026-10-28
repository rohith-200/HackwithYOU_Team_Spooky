/**
 * Event-only mission state. Geometry is schematic; identifiers and telemetry
 * always come from the stream. An action never changes a room's reported state.
 */

const LOG_LIMIT = 50;
const HISTORY_LIMIT = 100;
const SECURE_STATES = new Set(['holding', 'secure', 'intact']);
const EVENT_TYPES = new Set(['tick_start', 'agent_log', 'decision', 'score', 'dispatch', 'metrics']);
const PHASE_RANK = { idle: 0, receiving: 1, analyzing: 2, decided: 3, evaluated: 4, complete: 5 };

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function copy(value) {
  return value === undefined ? null : structuredClone(value);
}

function firstDefined(...values) {
  return values.find(value => value !== undefined) ?? null;
}

function numeric(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function identifier(value) {
  return (typeof value === 'string' && value.length > 0) || numeric(value);
}

function labelFor(id) {
  return id.replace(/[_-]+/g, ' ').replace(/\b\p{L}/gu, letter => letter.toUpperCase());
}

function isMockEvent(event) {
  if (event._mock === true) return true;
  if (!['agent_log', 'decision', 'dispatch'].includes(event.type)) return false;
  return ['message', 'rationale', 'content'].some(
    field => typeof event[field] === 'string' && /\[(?:MOCK|DEMO)(?:\]|\s)/i.test(event[field]),
  );
}

function roomStatus(containment, hot) {
  const normalized = typeof containment === 'string' ? containment.toLowerCase().trim() : '';
  if (normalized === 'breached') return 'breached';
  if (hot === true) return 'hot';
  if (hot === false && SECURE_STATES.has(normalized)) return 'secure';
  return 'unknown';
}

function normalizeSector(sector, slot, tickId) {
  const sensors = object(sector.sensors) ? sector.sensors : {};
  const sighting = object(sector.sighting) ? sector.sighting : {};
  const containment = firstDefined(sector.containment_state);
  const hot = typeof sector.hot === 'boolean' ? sector.hot : null;
  return {
    id: sector.sector,
    label: labelFor(sector.sector),
    slot,
    status: roomStatus(containment, hot),
    emf: firstDefined(sector.emf, sensors.emf_milligauss),
    temp_c: firstDefined(sector.temp_c, sensors.temp_c),
    motion: firstDefined(sector.motion, sensors.motion_events),
    entity_class: firstDefined(sector.entity_class, sighting.entity_class),
    confidence: firstDefined(sector.confidence, sighting.confidence),
    civilians: firstDefined(sector.civilians, sector.civilian_status),
    containment_state: containment,
    hot,
    lastSeenTick: tickId,
  };
}

/** Reducer for the six documented event types; future events are harmless. */
export class MissionState {
  constructor() {
    this._activeKey = null;
    this._records = new Map();
    this._facilities = new Map();
    this._startedTicks = new Set();
    // Compact score receipts outlive the bounded display history. This prevents
    // an old retried result from inflating totals after its detail was evicted.
    this._scores = new Map();
  }

  _key(facilityId, tickId) {
    return JSON.stringify([facilityId, String(tickId)]);
  }

  _record(facilityId, tickId) {
    const key = this._key(facilityId, tickId);
    if (!this._records.has(key)) {
      this._records.set(key, {
        tickId,
        facilityId,
        started: false,
        logs: [],
        decision: null,
        score: null,
        dispatch: null,
        metrics: null,
        isMock: false,
        phase: 'receiving',
        hasMetrics: false,
      });
    }
    return [key, this._records.get(key)];
  }

  _trimHistory() {
    for (const key of this._records.keys()) {
      if (this._records.size <= HISTORY_LIMIT) break;
      if (key !== this._activeKey) this._records.delete(key);
    }
  }

  _advance(record, phase) {
    if (PHASE_RANK[phase] > PHASE_RANK[record.phase]) record.phase = phase;
    if (record.score && record.hasMetrics) record.phase = 'complete';
  }

  _findRecord(event) {
    const active = this._records.get(this._activeKey);
    const tickId = identifier(event.tick_id) ? event.tick_id : active?.tickId;
    if (!identifier(tickId)) return null;
    if (typeof event.facility_id === 'string' && event.facility_id.length) {
      return this._record(event.facility_id, tickId);
    }
    // Events usually omit facility_id. Prefer an unambiguous existing tick so
    // a late score is attributed correctly after changing facilities.
    const matches = [...this._records].filter(([, record]) => String(record.tickId) === String(tickId));
    if (matches.length === 1) return matches[0];
    return this._record(active?.facilityId ?? null, tickId);
  }

  ingest(event) {
    if (!object(event) || !EVENT_TYPES.has(event.type)) return this.snapshot();

    const found = this._findRecord(event);
    if (!found) return this.snapshot();
    const [key, record] = found;

    if (event.type === 'tick_start') {
      // A repeated old tick_start cannot rewind a mission. IDs need not be
      // consecutive or numeric; a genuinely new tick activates by arrival.
      const wasStarted = this._startedTicks.has(key);
      if (!wasStarted || key === this._activeKey) this._activeKey = key;
      this._startedTicks.add(key);
      record.started = true;

      if (key === this._activeKey) {
        let facility = this._facilities.get(record.facilityId);
        if (!facility) {
          facility = { rooms: new Map(), nextSlot: 0 };
          this._facilities.set(record.facilityId, facility);
        }
        if (!wasStarted) {
          for (const room of facility.rooms.values()) room.status = 'stale';
        }
        const sectors = Array.isArray(event.sectors)
          ? event.sectors
          : Array.isArray(event.sector_reports) ? event.sector_reports : [];
        for (const sector of sectors) {
          if (!object(sector) || typeof sector.sector !== 'string' || !sector.sector.length) continue;
          const slot = facility.rooms.get(sector.sector)?.slot ?? facility.nextSlot++;
          facility.rooms.set(sector.sector, normalizeSector(sector, slot, record.tickId));
        }
      }

      const budget = object(event.swarm_budget) ? event.swarm_budget : {};
      const maxAgents = firstDefined(event.max_agents, budget.max_active_agents);
      const credits = firstDefined(event.credits, budget.api_credits_remaining);
      if (maxAgents !== null || credits !== null) {
        record.metrics = { ...record.metrics };
        if (maxAgents !== null) record.metrics.max_agents = maxAgents;
        if (credits !== null) record.metrics.credits = credits;
      }
    } else {
      // If a browser joins halfway through a tick, show the first event rather
      // than dropping it. Once active, only tick_start can change the mission.
      if (this._activeKey === null) this._activeKey = key;

      switch (event.type) {
        case 'agent_log':
          record.logs.push(copy(event));
          record.logs = record.logs.slice(-LOG_LIMIT);
          this._advance(record, 'analyzing');
          break;
        case 'decision':
          record.decision = copy(event);
          this._advance(record, 'decided');
          break;
        case 'dispatch':
          record.dispatch = copy(event);
          break;
        case 'score': {
          record.score = copy(event);
          const previous = this._scores.get(key) ?? { score: null, falseAlarm: false };
          this._scores.set(key, {
            score: numeric(event.score) ? event.score : previous.score,
            falseAlarm: typeof event.false_alarm === 'boolean' ? event.false_alarm : previous.falseAlarm,
          });
          this._advance(record, 'evaluated');
          break;
        }
        case 'metrics':
          record.metrics = { ...record.metrics, ...copy(event) };
          record.hasMetrics = true;
          this._advance(record, record.phase);
          break;
      }
    }

    record.isMock ||= isMockEvent(event);
    this._trimHistory();
    return this.snapshot();
  }

  snapshot() {
    const active = this._records.get(this._activeKey);
    const totals = { ticksScored: 0, averageScore: null, falseAlarms: 0 };
    let scoreSum = 0;
    for (const result of this._scores.values()) {
      if (numeric(result.score)) {
        totals.ticksScored += 1;
        scoreSum += result.score;
      }
      if (result.falseAlarm) totals.falseAlarms += 1;
    }
    if (totals.ticksScored) totals.averageScore = scoreSum / totals.ticksScored;
    return copy({
      tickId: active?.tickId ?? null,
      facilityId: active?.facilityId ?? null,
      rooms: [...(this._facilities.get(active?.facilityId)?.rooms.values() ?? [])].sort((a, b) => a.slot - b.slot),
      logs: active?.logs ?? [],
      decision: active?.decision ?? null,
      score: active?.score ?? null,
      dispatch: active?.dispatch ?? null,
      metrics: active?.metrics ?? null,
      totals,
      maxAgents: active?.metrics?.max_agents ?? null,
      credits: active?.metrics?.credits ?? null,
      tickCount: this._startedTicks.size,
      isMock: active?.isMock ?? false,
      phase: active?.phase ?? 'idle',
    });
  }
}
