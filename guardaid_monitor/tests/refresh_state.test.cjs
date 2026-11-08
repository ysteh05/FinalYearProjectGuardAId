const { test } = require('node:test');
const assert = require('node:assert/strict');
const { RefreshCoordinator, draftPayload } = require('../app/static/refresh_state.js');
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; };
const tick = () => new Promise(r => setImmediate(r));

test('automatic ticks coalesce rather than stacking requests', async () => {
  const gate = deferred(); let loads = 0;
  const r = new RefreshCoordinator(['alerts'], async () => { loads++; await gate.promise; return 1; }, () => {});
  const first = r.request(); await tick();
  for (let i=0; i<100; i++) assert.equal(r.request(true), first);
  assert.equal(loads, 1); gate.resolve(); await first;
  assert.equal(loads, 1); assert.equal(r.running, null);
});
test('mutation invalidates old read, waits for a fresh follow-up', async () => {
  const gate = deferred(); const seen = []; let calls = 0;
  const r = new RefreshCoordinator(['cameras'], async () => { if (++calls === 1) { await gate.promise; return 'deleted camera'; } return 'new state'; }, (_, v) => seen.push(v));
  const first = r.request(); await tick(); r.invalidate();
  const manual = r.request(); gate.resolve(); await manual; await first;
  assert.deepEqual(seen, ['new state']); assert.equal(calls, 2);
});
test('unrelated failure does not block successful alerts and is cleared on recovery', async () => {
  let fail = true; const seen = [];
  const r = new RefreshCoordinator(['settings','alerts'], async key => { if (key === 'settings' && fail) throw Error('offline'); return key; }, (key) => seen.push(key));
  await r.request(); assert.deepEqual(seen, ['alerts']); assert.equal(r.status.settings.lastSuccess, undefined);
  fail = false; await r.request(); assert.equal(r.status.settings.error, null); assert.ok(r.status.settings.lastSuccess);
});
test('all failures release lock and preserve last successful timestamp', async () => {
  let fail = false;
  const r = new RefreshCoordinator(['alerts'], async () => { if (fail) throw Error('timeout'); return []; }, () => {});
  await r.request(); const last = r.status.alerts.lastSuccess; fail = true;
  await r.request(); assert.equal(r.running, null); assert.equal(r.status.alerts.lastSuccess,last); assert.equal(r.status.alerts.error,'timeout');
});
test('draft payload keeps editor version, not latest poll version', () => {
  const base = {configVersion:10, weapon:{sampleFps:2}};
  const latest = {configVersion:11, weapon:{sampleFps:4}};
  const draft = draftPayload(base, {weapon:{sampleFps:3}});
  assert.equal(draft.configVersion,10); assert.notEqual(draft.configVersion,latest.configVersion);
  assert.equal(base.weapon.sampleFps,2);
});
