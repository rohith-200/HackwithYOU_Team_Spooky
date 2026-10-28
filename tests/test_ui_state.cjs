/* Run: node --test tests/test_ui_state.cjs. No npm dependencies required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const stateSource = fs.readFileSync(path.join(__dirname, '../ui/assets/state.js'), 'utf8');
const modulePromise = import(`data:text/javascript;base64,${Buffer.from(stateSource).toString('base64')}`);
const demoSource = fs.readFileSync(path.join(__dirname, '../ui/assets/demo.js'), 'utf8');
const demoPromise = import(`data:text/javascript;base64,${Buffer.from(demoSource).toString('base64')}`);
const room = (id, values = {}) => ({ sector: id, containment_state: 'holding', hot: false, ...values });
const start = (tick_id, sectors, values = {}) => ({ type: 'tick_start', tick_id, facility_id: 'aws-builder-loft-sf', sectors, ...values });

async function state() {
  const { MissionState } = await modulePromise;
  return new MissionState();
}

test('empty mission is honest about missing input and unknown future events', async () => {
  const mission = await state();
  const empty = mission.snapshot();
  assert.equal(empty.tickId, null);
  assert.equal(empty.phase, 'idle');
  assert.deepEqual(empty.rooms, []);
  assert.equal(empty.score, null);
  assert.equal(empty.metrics, null);
  for (const value of [null, [], 'invalid', {}, { type: 'future_event', tick_id: 10 }]) {
    assert.deepEqual(mission.ingest(value), empty);
  }
});

test('normalized events preserve exact IDs, zero readings and explicit nulls', async () => {
  const mission = await state();
  mission.ingest(start(142, [room('catering_coffee_bar', {
    emf: 0, temp_c: -4, motion: 0, confidence: 0, entity_class: null, civilians: '4 pinned',
  })], { max_agents: 8, credits: 45000 }));
  const snapshot = mission.snapshot();
  assert.equal(snapshot.rooms[0].id, 'catering_coffee_bar');
  assert.equal(snapshot.rooms[0].label, 'Catering Coffee Bar');
  assert.equal(snapshot.rooms[0].emf, 0);
  assert.equal(snapshot.rooms[0].confidence, 0);
  assert.equal(snapshot.rooms[0].entity_class, null);
  assert.equal(snapshot.rooms[0].civilians, '4 pinned');
  assert.equal(snapshot.rooms[0].lastSeenTick, 142);
  assert.deepEqual(snapshot.metrics, { max_agents: 8, credits: 45000 });
});

test('raw supplied fixtures flatten without inferring a backend threat assessment', async () => {
  const mission = await state();
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, '../fixtures/outbreak-level-5.json'), 'utf8'));
  mission.ingest({ type: 'tick_start', ...fixture.ticks[0] });
  const [coffee, stage] = mission.snapshot().rooms;
  assert.equal(coffee.emf, 98);
  assert.equal(coffee.motion, 17);
  assert.equal(coffee.temp_c, -4);
  assert.equal(coffee.confidence, 0.82);
  assert.equal(coffee.civilians, '4 pinned');
  assert.equal(coffee.status, 'breached');
  assert.equal(stage.entity_class, null);
  assert.equal(stage.confidence, null);
  assert.equal(stage.status, 'unknown');
  assert.equal(stage.hot, null);
  assert.deepEqual(mission.snapshot().metrics, { max_agents: 8, credits: 45000 });
});

test('missing sectors become stale and slots stay stable through reordering and reappearance', async () => {
  const mission = await state();
  mission.ingest(start('outbreak-a', [room('coffee'), room('stage')]));
  mission.ingest(start('outbreak-z', [room('stage'), room('annex', { hot: true })]));
  let rooms = mission.snapshot().rooms;
  assert.deepEqual(rooms.map(value => [value.id, value.slot, value.status]), [
    ['coffee', 0, 'stale'], ['stage', 1, 'secure'], ['annex', 2, 'hot'],
  ]);
  assert.equal(rooms[0].lastSeenTick, 'outbreak-a');
  mission.ingest(start(900, [room('annex'), room('coffee', { containment_state: 'breached' })]));
  rooms = mission.snapshot().rooms;
  assert.deepEqual(rooms.map(value => [value.id, value.slot]), [['coffee', 0], ['stage', 1], ['annex', 2]]);
  assert.equal(rooms[0].status, 'breached');
  assert.equal(rooms[1].status, 'stale');
});

test('only explicit report evidence makes a room secure; breach overrides contradictory hot false', async () => {
  const mission = await state();
  const cases = [
    [{ containment_state: 'breached', hot: false }, 'breached'],
    [{ containment_state: 'intact', hot: true }, 'hot'],
    [{ containment_state: 'holding', hot: false }, 'secure'],
    [{ containment_state: 'secure', hot: false }, 'secure'],
    [{ containment_state: 'intact', hot: false }, 'secure'],
    [{ containment_state: undefined, hot: false }, 'unknown'],
    [{ containment_state: 'holding', hot: undefined }, 'unknown'],
    [{ containment_state: 'unrecognized-state', hot: false }, 'unknown'],
  ];
  mission.ingest(start(1, cases.map(([values], index) => room(String(index), values))));
  assert.deepEqual(mission.snapshot().rooms.map(value => value.status), cases.map(([, expected]) => expected));
});

test('a decision, score and dispatch never invent containment success or civilian rescue', async () => {
  const mission = await state();
  mission.ingest(start(1, [room('coffee', { containment_state: 'breached', hot: true, civilians: '4 pinned' })]));
  const before = mission.snapshot().rooms;
  mission.ingest({ type: 'decision', tick_id: 1, action: 'evacuate', sector: 'coffee' });
  mission.ingest({ type: 'score', tick_id: 1, score: 1, expected_action: 'evacuate' });
  mission.ingest({ type: 'dispatch', tick_id: 1, ok: true, content: 'Evacuate coffee' });
  assert.deepEqual(mission.snapshot().rooms, before);
});

test('tick changes reset active findings; late agent and score events cannot rewind active state', async () => {
  const mission = await state();
  mission.ingest(start(142, [room('coffee')]));
  mission.ingest({ type: 'agent_log', tick_id: 142, agent: 'sensor', message: 'old report' });
  mission.ingest({ type: 'decision', tick_id: 142, action: 'observe' });
  mission.ingest({ type: 'dispatch', tick_id: 142, ok: true });
  mission.ingest(start(900, [room('stage')]));
  mission.ingest({ type: 'agent_log', tick_id: 142, agent: 'lore', message: '[MOCK] late report' });
  mission.ingest({ type: 'score', tick_id: 142, score: 0.5, false_alarm: true });
  mission.ingest({ type: 'decision', tick_id: 902, action: 'contain' });
  const snapshot = mission.snapshot();
  assert.equal(snapshot.tickId, 900);
  assert.equal(snapshot.phase, 'receiving');
  assert.deepEqual(snapshot.logs, []);
  assert.equal(snapshot.decision, null);
  assert.equal(snapshot.dispatch, null);
  assert.equal(snapshot.score, null);
  assert.equal(snapshot.isMock, false);
  assert.deepEqual(snapshot.totals, { ticksScored: 1, averageScore: 0.5, falseAlarms: 1 });
  mission.ingest(start(142, [room('coffee')]));
  assert.equal(mission.snapshot().tickId, 900);
});

test('duplicate and corrected evaluations update totals once per facility and tick', async () => {
  const mission = await state();
  mission.ingest(start(1, []));
  const score = { type: 'score', tick_id: 1, score: 1, false_alarm: false };
  mission.ingest(score);
  mission.ingest(score);
  assert.deepEqual(mission.snapshot().totals, { ticksScored: 1, averageScore: 1, falseAlarms: 0 });
  mission.ingest({ ...score, score: 0.5, false_alarm: true });
  assert.deepEqual(mission.snapshot().totals, { ticksScored: 1, averageScore: 0.5, falseAlarms: 1 });
  mission.ingest({ ...score, score: null });
  assert.deepEqual(mission.snapshot().totals, { ticksScored: 1, averageScore: 0.5, falseAlarms: 0 });
  mission.ingest(start(2, []));
  mission.ingest({ ...score, tick_id: 2, score: 0 });
  assert.deepEqual(mission.snapshot().totals, { ticksScored: 2, averageScore: 0.25, falseAlarms: 0 });
});

test('mock labels are sticky within one tick and do not leak into later ticks', async () => {
  for (const tagged of [
    { type: 'tick_start', _mock: true },
    { type: 'agent_log', message: '[MOCK] sensor finding' },
    { type: 'decision', rationale: '[DEMO] simulated decision' },
    { type: 'dispatch', content: '[MOCK ONLY — NOT SENT] test' },
  ]) {
    const mission = await state();
    mission.ingest(start(1, []));
    mission.ingest({ ...tagged, tick_id: 1 });
    mission.ingest({ type: 'agent_log', tick_id: 1, message: 'no label here' });
    assert.equal(mission.snapshot().isMock, true);
    mission.ingest(start(2, []));
    assert.equal(mission.snapshot().isMock, false);
  }
});

test('phase progresses without being reset by a late agent message or dispatch result', async () => {
  const mission = await state();
  mission.ingest(start(1, []));
  assert.equal(mission.snapshot().phase, 'receiving');
  mission.ingest({ type: 'agent_log', tick_id: 1, message: 'working' });
  assert.equal(mission.snapshot().phase, 'analyzing');
  mission.ingest({ type: 'decision', tick_id: 1, action: 'observe' });
  assert.equal(mission.snapshot().phase, 'decided');
  mission.ingest({ type: 'score', tick_id: 1, score: 1 });
  assert.equal(mission.snapshot().phase, 'evaluated');
  mission.ingest({ type: 'metrics', tick_id: 1, total_tokens: 0 });
  assert.equal(mission.snapshot().phase, 'complete');
  mission.ingest({ type: 'agent_log', tick_id: 1, message: 'late log' });
  mission.ingest({ type: 'dispatch', tick_id: 1, ok: false });
  assert.equal(mission.snapshot().phase, 'complete');
});

test('facility changes isolate rooms and retain first-seen slot assignments', async () => {
  const mission = await state();
  mission.ingest(start(1, [room('coffee'), room('stage')]));
  mission.ingest(start(2, [room('lab')], { facility_id: 'another-building' }));
  assert.deepEqual(mission.snapshot().rooms.map(value => [value.id, value.slot]), [['lab', 0]]);
  mission.ingest({ type: 'score', tick_id: 1, score: 1 });
  assert.equal(mission.snapshot().facilityId, 'another-building');
  mission.ingest(start(3, [room('stage'), room('annex')]));
  assert.deepEqual(mission.snapshot().rooms.map(value => [value.id, value.slot, value.status]), [
    ['coffee', 0, 'stale'], ['stage', 1, 'secure'], ['annex', 2, 'secure'],
  ]);
});

test('log and detail history bounds retain active state and deduplicate old score receipts', async () => {
  const mission = await state();
  mission.ingest(start(0, []));
  for (let index = 0; index < 75; index++) {
    mission.ingest({ type: 'agent_log', tick_id: 0, message: String(index) });
  }
  assert.equal(mission.snapshot().logs.length, 50);
  assert.equal(mission.snapshot().logs[0].message, '25');
  for (let tick = 0; tick < 105; tick++) {
    mission.ingest(start(tick, []));
    mission.ingest({ type: 'score', tick_id: tick, score: 1, false_alarm: tick === 0 });
  }
  assert.ok(mission._records.size <= 100);
  assert.equal(mission.snapshot().tickId, 104);
  mission.ingest({ type: 'score', tick_id: 0, score: 1, false_alarm: true });
  assert.equal(mission.snapshot().tickId, 104);
  assert.deepEqual(mission.snapshot().totals, { ticksScored: 105, averageScore: 1, falseAlarms: 1 });
  mission.ingest(start(0, []));
  assert.equal(mission.snapshot().tickId, 104);
  assert.equal(mission.snapshot().tickCount, 105);
});

test('malformed sectors are ignored and snapshots cannot mutate internal data', async () => {
  const mission = await state();
  mission.ingest(start(1, [null, {}, { sector: 99 }, room('<script>_annex', { sighting: null, sensors: null })]));
  const snapshot = mission.snapshot();
  assert.equal(snapshot.rooms.length, 1);
  assert.equal(snapshot.rooms[0].id, '<script>_annex');
  snapshot.rooms[0].status = 'invented';
  snapshot.totals.averageScore = 500;
  assert.equal(mission.snapshot().rooms[0].status, 'secure');
  assert.equal(mission.snapshot().totals.averageScore, null);
});

test('mid-tick connections can bootstrap an event without requiring a previous tick_start', async () => {
  const mission = await state();
  mission.ingest({ type: 'agent_log', tick_id: 'current', agent: 'sensor', message: 'working' });
  assert.equal(mission.snapshot().tickId, 'current');
  assert.equal(mission.snapshot().logs.length, 1);
  mission.ingest({ type: 'decision', tick_id: 'future', action: 'observe' });
  assert.equal(mission.snapshot().tickId, 'current');
});

test('all demo scenarios finish through the same reducer without changing reported outcomes', async () => {
  const { getDemoPreview, getDemoSequence } = await demoPromise;
  for (const [scenario, action] of [['breach', 'evacuate'], ['control', 'observe'], ['unknown', 'contain']]) {
    const mission = await state();
    const preview = getDemoPreview(scenario);
    assert.equal(preview._mock, true);
    mission.ingest(preview);
    assert.equal(mission.snapshot().score, null);
    assert.equal(mission.snapshot().decision, null);
    const stages = getDemoSequence(scenario);
    for (const { delay, event } of stages) {
      assert.equal(event._mock, true, 'even the first tick must be explicitly simulated');
      assert.ok(Number.isFinite(delay) && delay >= 0);
      mission.ingest(event);
    }
    const final = mission.snapshot();
    assert.equal(final.phase, 'complete');
    assert.equal(final.isMock, true);
    assert.equal(final.decision.action, action);
    assert.equal(final.score.expected_action, action);
    assert.deepEqual(final.totals, { ticksScored: 1, averageScore: 1, falseAlarms: 0 });
    assert.equal(final.maxAgents, 8);
    assert.equal(final.credits, 45000);
    assert.equal(final.rooms.find(room => room.id === 'catering_coffee_bar').civilians, scenario === 'breach' ? '4 pinned' : 'clear');
    if (scenario === 'control') {
      assert.equal(final.dispatch, null, 'a quiet tick should not imply a sent alert');
      assert.equal(final.metrics.searches, 0);
      assert.equal(final.metrics.agents_used, 4);
    } else {
      assert.equal(final.dispatch.simulated, true);
      assert.equal(final.rooms.find(room => room.id === final.decision.sector).status, 'breached', 'correct decisions do not confirm successful containment');
    }
  }
});

test('unknown-sector demo adds its module through real tick data and keeps all initial slots', async () => {
  const { getDemoPreview, getDemoSequence } = await demoPromise;
  const mission = await state();
  mission.ingest(getDemoPreview('unknown'));
  const initialSlots = mission.snapshot().rooms.map(room => [room.id, room.slot]);
  assert.equal(mission.snapshot().rooms.some(room => room.id === 'archive_annex'), false);
  mission.ingest(getDemoSequence('unknown')[0].event);
  assert.deepEqual(mission.snapshot().rooms.slice(0, 3).map(room => [room.id, room.slot]), initialSlots);
  const annex = mission.snapshot().rooms.find(room => room.id === 'archive_annex');
  assert.equal(annex.id, 'archive_annex');
  assert.equal(annex.slot, 3);
  assert.equal(annex.status, 'breached');
  assert.equal(annex.emf, 76);
});

test('local demo data and received backend data remain independent mission instances', async () => {
  const { getDemoPreview, getDemoSequence } = await demoPromise;
  const local = await state();
  const backend = await state();
  local.ingest(getDemoPreview('breach'));
  backend.ingest(start(142, [room('server_room', { emf: 3 })]));
  backend.ingest({ type: 'score', tick_id: 142, score: 0.5, false_alarm: false });
  for (const { event } of getDemoSequence('breach')) local.ingest(event);
  assert.equal(local.snapshot().isMock, true);
  assert.equal(local.snapshot().totals.averageScore, 1);
  assert.equal(backend.snapshot().isMock, false);
  assert.equal(backend.snapshot().totals.averageScore, 0.5);
  assert.deepEqual(backend.snapshot().rooms.map(room => room.id), ['server_room']);
});
