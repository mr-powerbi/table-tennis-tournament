// Full-tournament simulations on the in-browser engine (docs/engine.js).
// Run: node tests/engine.test.mjs
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const store = new Map();
const ctx = {
  localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) },
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(readFileSync(new URL('../docs/engine.js', import.meta.url), 'utf8'), ctx);
const E = ctx.TTEngine;

function playOut(s) {
  let guard = 0;
  while (s.phase !== 'done') {
    assert.ok(guard++ < 200, 'the tournament got stuck in a loop');
    s = E.simulateStage();
  }
  return s;
}

let checked = 0;
for (let n = 3; n <= 40; n++) {
  store.clear();
  E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
  let s = E.request('api/admin', { action: 'draw' });
  s = playOut(s);
  const played = s.ranking.map((r) => r.played);
  assert.ok(Math.min(...played) >= 2, `n=${n}: everyone plays at least two matches`);
  const first = new Set(s.ko.rounds[0].flatMap((m) => [m.a, m.b]));
  assert.equal(first.size, s.K, `n=${n}: full bracket`);
  checked++;
}

// A points result is validated the same way as on the server.
store.clear();
E.freshWithPlayers(['A1', 'B2', 'C3', 'D4']);
let s = E.request('api/admin', { action: 'draw' });
assert.throws(() => E.request('api/score', { stage: 'r1', id: s.r1[0].id, pa: 11, pb: 10 }));
s = E.request('api/score', { stage: 'r1', id: s.r1[0].id, pa: 12, pb: 10 });
assert.deepEqual([s.r1[0].sa, s.r1[0].sb], [1, 0]);

// Match to two sets: the match score is derived from the points of every set.
store.clear();
E.freshWithPlayers(['A1', 'B2', 'C3', 'D4']);
E.request('api/admin', { action: 'settings', setsToWin: 2 });
s = E.request('api/admin', { action: 'draw' });
const mid = s.r1[0].id;
s = E.request('api/score', { stage: 'r1', id: mid, sets: [[11, 9], [5, 11], [11, 8]] });
assert.deepEqual([s.r1[0].sa, s.r1[0].sb, s.r1[0].pa, s.r1[0].pb], [2, 1, 27, 28]);
assert.equal(JSON.stringify(s.r1[0].sets), JSON.stringify([[11, 9], [5, 11], [11, 8]]));
for (const bad of [[[11, 9]], [[11, 9], [11, 3], [11, 5]], [[11, 10], [11, 3]]])
  assert.throws(() => E.request('api/score', { stage: 'r1', id: mid, sets: bad }));
assert.throws(() => E.request('api/score', { stage: 'r1', id: mid, pa: 11, pb: 3 }));
s = E.request('api/score', { stage: 'r1', id: mid, sa: 2, sb: 0 }); // a sets-only result still works
assert.equal(s.r1[0].sets, null);
assert.throws(() => E.request('api/admin', { action: 'settings', scoreMode: 'nonsense' }));
// Tournaments to 2 and 3 sets (in both entry modes) reach the podium.
for (const [sets, mode] of [[2, 'points'], [3, 'points'], [2, 'sets'], [1, 'sets']]) {
  store.clear();
  E.freshWithPlayers(Array.from({ length: 13 }, (_, i) => `Gracz ${i}`));
  E.request('api/admin', { action: 'settings', setsToWin: sets, scoreMode: mode });
  s = playOut(E.request('api/admin', { action: 'draw' }));
  assert.equal(s.phase, 'done');
  const withSets = s.r1.filter((m) => Array.isArray(m.sets)).length;
  assert.equal(withSets > 0, sets > 1 && mode === 'points', `sets=${sets} mode=${mode}`);
}

// Bracket without Lucky Losers (llSpots 0) and explicit bracket sizes.
store.clear();
E.freshWithPlayers(Array.from({ length: 20 }, (_, i) => `Gracz ${i}`));
E.request('api/admin', { action: 'settings', koSize: 8, llSpots: 0 });
s = E.request('api/admin', { action: 'draw' });
assert.equal(s.K, 8); assert.equal(s.D, 8); assert.equal(s.L, 0);
s = playOut(s);
assert.equal(s.ll.length, 0);
assert.equal(s.ko.rounds[0].length, 4);
store.clear();
E.freshWithPlayers(Array.from({ length: 40 }, (_, i) => `Gracz ${i}`));
s = E.request('api/admin', { action: 'settings', koSize: 8, llSpots: 3 });
assert.deepEqual([s.K, s.D, s.L], [8, 5, 3]);
s = E.request('api/admin', { action: 'settings', llSpots: 'auto' });
assert.deepEqual([s.K, s.D, s.L], [8, 6, 2]);
assert.throws(() => E.request('api/admin', { action: 'settings', llSpots: 17 }));
assert.throws(() => E.request('api/admin', { action: 'settings', koSize: 12 }));

// The organizer picks how many random-opponent rounds are played: everyone plays that many matches.
for (const rounds of [1, 2, 3, 4, 6]) {
  for (const n of [5, 8, 13]) {
    store.clear();
    E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
    E.request('api/admin', { action: 'settings', rounds });
    s = playOut(E.request('api/admin', { action: 'draw' }));
    assert.equal(s.phase, 'done', `rounds=${rounds} n=${n}`);
    assert.ok(Math.min(...s.ranking.map((r) => r.played)) >= rounds, `rounds=${rounds} n=${n}: everyone plays ${rounds} matches`);
    assert.equal(s.ranking.length, n);
  }
}
// Rounds 3+ pair players with the same record and avoid rematches where possible.
store.clear();
E.freshWithPlayers(Array.from({ length: 16 }, (_, i) => `Gracz ${i}`));
E.request('api/admin', { action: 'settings', rounds: 4 });
s = E.request('api/admin', { action: 'draw' });
const seen = new Set();
for (let r = 1; r <= 4; r++) {
  assert.equal(s.phase, 'r' + r);
  for (const m of s['r' + r]) { const k = [m.a, m.b].sort().join('|'); assert.ok(!seen.has(k), `rematch in round ${r}`); seen.add(k); }
  s = E.simulateStage();
}
assert.notEqual(s.phase, 'r4');

// Group format: round-robin groups, top of every group plus the best of the rest fill the bracket.
for (const [n, size, adv] of [[6, 3, 2], [9, 3, 2], [12, 4, 2], [13, 4, 1], [20, 4, 2], [20, 5, 2], [26, 4, 2], [40, 5, 2], [64, 4, 2]]) {
  store.clear();
  E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
  E.request('api/admin', { action: 'settings', format: 'groups', groupSize: size, groupAdvance: adv });
  s = E.request('api/admin', { action: 'draw' });
  const tag = `n=${n} size=${size} adv=${adv}`;
  assert.equal(s.phase, 'groups', tag);
  assert.equal(s.groups.reduce((t, g) => t + g.ids.length, 0), n, tag);
  const sizes = s.groups.map((g) => g.ids.length);
  assert.ok(Math.max(...sizes) - Math.min(...sizes) <= 1, `${tag}: balanced groups`);
  for (const g of s.groups) assert.equal(s.gm.filter((m) => m.g === g.id).length, g.ids.length * (g.ids.length - 1) / 2, `${tag}: round robin`);
  const K0 = s.K;
  s = playOut(s);
  assert.equal(s.phase, 'done', tag);
  assert.equal(new Set(s.ko.rounds[0].flatMap((m) => [m.a, m.b])).size, K0, `${tag}: full bracket`);
  const grp = {}; s.groups.forEach((g) => g.ids.forEach((id) => (grp[id] = g.id)));
  const clashes = s.ko.rounds[0].filter((m) => grp[m.a] === grp[m.b]).length;
  assert.ok(clashes <= 1, `${tag}: avoid same-group first-round pairs (${clashes})`);
  // everyone plays everyone in their group
  assert.ok(s.ranking.every((r) => r.played === s.groups.find((g) => g.id === r.group).ids.length - 1), tag);
}
// Late player in the group stage joins the smallest group and plays all of it.
store.clear();
E.freshWithPlayers(Array.from({ length: 11 }, (_, i) => `Gracz ${i}`));
E.request('api/admin', { action: 'settings', format: 'groups', groupSize: 4, groupAdvance: 2 });
s = E.request('api/admin', { action: 'draw' });
const before = s.gm.length, small = Math.min(...s.groups.map((g) => g.ids.length));
s = E.request('api/admin', { action: 'late', name: 'Spozniony' });
assert.equal(s.gm.length, before + small);
s = playOut(s);
assert.equal(s.phase, 'done');
// Settings are locked after the draw.
assert.throws(() => E.request('api/admin', { action: 'settings', format: 'rounds' }));
assert.throws(() => E.request('api/admin', { action: 'settings', rounds: 3 }));

// The sample demo data loads and is a finished tournament.
store.clear();
s = E.loadState(JSON.parse(readFileSync(new URL('../docs/demo-data.json', import.meta.url), 'utf8')));
assert.equal(s.phase, 'done');
assert.equal(s.players.length, 26);

console.log(`OK: ${checked} full tournaments (3–40 players) and validation tests`);
