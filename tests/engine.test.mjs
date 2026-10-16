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

const entrants = (s) => new Set([...(s.ko.pre || []).flatMap((m) => [m.a, m.b]), ...s.ko.rounds[0].flatMap((m) => [m.a, m.b])].filter(Boolean));
let checked = 0;
for (let n = 3; n <= 40; n++) {
  store.clear();
  E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
  let s = E.request('api/admin', { action: 'draw' });
  s = playOut(s);
  const played = s.ranking.map((r) => r.played);
  assert.ok(Math.min(...played) >= 2, `n=${n}: everyone plays at least two matches`);
  assert.equal(entrants(s).size, s.K, `n=${n}: full bracket`);
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
assert.throws(() => E.request('api/admin', { action: 'settings', llSpots: 65 }));
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
  const K0 = s.K, grp = {};
  s.groups.forEach((g) => g.ids.forEach((id) => (grp[id] = g.id)));
  s = E.simulateStage(); // the whole group stage
  while (s.phase === 'll') s = E.simulateStage();
  // the bracket has just been built: only the pairs known now count (play-in winners are not known yet)
  const clashes = [...(s.ko.pre || []), ...s.ko.rounds[0]].filter((m) => m.a && m.b && grp[m.a] === grp[m.b]).length;
  assert.ok(clashes <= 1, `${tag}: avoid same-group first-round pairs (${clashes})`);
  s = playOut(s);
  assert.equal(s.phase, 'done', tag);
  assert.equal(entrants(s).size, K0, `${tag}: full bracket`);
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

// Knockout stage: the organizer picks the share of players that advance, always an even number rounded up.
{
  const pcts = [70, 60, 50, 45, 40, 35, 30, 25, 20];
  for (const n of [3, 5, 8, 11, 16, 20, 21, 33, 40]) {
    for (const pct of pcts) {
      store.clear();
      E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
      E.request('api/admin', { action: 'settings', advancePct: pct, koSize: null });
      s = E.request('api/admin', { action: 'draw' });
      const want = Math.max(2, Math.min(Math.ceil(n * pct / 100 - 1e-9) + (Math.ceil(n * pct / 100 - 1e-9) % 2), n - (n % 2)));
      assert.equal(s.D, want, `n=${n} ${pct}%: direct qualifiers`);
      assert.equal(s.D % 2, 0); assert.equal(s.L, 0); assert.equal(s.K, s.D);
      s = playOut(s);
      assert.equal(s.phase, 'done', `n=${n} ${pct}%`);
      // single elimination: M-1 matches, the best seeds get the byes, nobody is missing
      const M = s.K, koMatches = (s.ko.pre || []).length + s.ko.rounds.flat().length;
      assert.equal(koMatches, M - 1, `n=${n} ${pct}%: M-1 matches (M=${M})`);
      assert.equal(entrants(s).size, M);
      if (s.ko.pre) {
        const order = s.ranking.map((r) => r.id), rank = (id) => order.indexOf(id);
        const prePlayers = new Set(s.ko.pre.flatMap((m) => [m.a, m.b]));
        const byes = [...entrants(s)].filter((id) => !prePlayers.has(id));
        assert.ok(Math.max(...byes.map(rank)) < Math.min(...[...prePlayers].map(rank)), `n=${n} ${pct}%: byes go to the best of the table`);
      }
    }
  }
}
// Every even number of qualifiers works as a bracket (6, 10, 12, 14, ...), including the third-place match.
for (const [n, pct] of [[12, 50], [20, 50], [24, 60], [30, 50], [40, 70]]) {
  store.clear();
  E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
  E.request('api/admin', { action: 'settings', advancePct: pct, koSize: null });
  s = playOut(E.request('api/admin', { action: 'draw' }));
  assert.ok(s.ko.rounds[s.ko.rounds.length - 1][0].sa !== null, `n=${n}: final played`);
  if (s.K >= 4) assert.ok(s.ko.third && s.ko.third.sa !== null, `n=${n}: third-place match played`);
}

// Lucky Losers: yes/no, decided by a play-off match or simply by the table, number chosen by the organizer.
for (const n of [12, 20, 26, 33]) {
  for (const mode of ['playoff', 'table']) {
    store.clear();
    E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
    E.request('api/admin', { action: 'settings', advancePct: 40, koSize: null, luckyLosers: true, llMode: mode });
    let st = E.request('api/admin', { action: 'draw' });
    const D = st.D, pool = n - D, tag = `n=${n} ${mode}`;
    assert.ok(st.L >= 1 && (D + st.L) % 2 === 0, `${tag}: even bracket, L=${st.L}`);
    assert.equal(st.llMode, mode); assert.equal(st.llPool, pool); assert.equal(st.K, D + st.L);
    const L = st.L;
    st = playOut(st);
    assert.equal(st.phase, 'done', tag);
    assert.equal(entrants(st).size, D + L, `${tag}: bracket size`);
    if (mode === 'table') assert.equal(st.ll.length, 0, `${tag}: no Lucky Losers matches`);
    else assert.equal(st.ll.length, L, `${tag}: one match per Lucky Loser place`);
    // explicit number, capped by the pool (and halved in playoff mode)
    store.clear();
    E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
    E.request('api/admin', { action: 'settings', advancePct: 40, koSize: null, luckyLosers: true, llMode: mode, llSpots: 2 });
    st = E.request('api/admin', { action: 'draw' });
    assert.equal(st.L, 2, `${tag}: chosen number of Lucky Losers`);
    // Lucky Losers off ignores the number
    E.request('api/admin', { action: 'settings', luckyLosers: false });
    assert.equal(E.request('api/state').L, 0);
  }
}
assert.throws(() => E.request('api/admin', { action: 'settings', advancePct: 0 }));
assert.throws(() => E.request('api/admin', { action: 'settings', llMode: 'nonsense' }));

// Groups: the Lucky Losers come from the players right behind the qualifiers (the third places).
for (const [n, size, adv] of [[20, 4, 2], [24, 4, 2], [26, 4, 2]]) {
  store.clear();
  E.freshWithPlayers(Array.from({ length: n }, (_, i) => `Gracz ${i}`));
  E.request('api/admin', { action: 'settings', format: 'groups', groupSize: size, groupAdvance: adv, koSize: null, luckyLosers: true, llMode: 'table' });
  s = E.request('api/admin', { action: 'draw' });
  const G = s.groups.length;
  assert.equal(s.D, G * adv, `n=${n}: direct qualifiers from the groups`);
  assert.equal(s.llPool, G, `n=${n}: one third place per group`);
  assert.ok(s.L >= 1 && s.L <= G && (s.D + s.L) % 2 === 0);
  s = playOut(s);
  assert.equal(s.phase, 'done');
}
// recommendation: closest to half of the players
store.clear();
E.freshWithPlayers(Array.from({ length: 20 }, (_, i) => `Gracz ${i}`));
assert.equal(E.request('api/state').recPct, 50);
assert.equal(E.request('api/state').D, 10);

// The sample demo data loads and is a finished tournament.
store.clear();
s = E.loadState(JSON.parse(readFileSync(new URL('../docs/demo-data.json', import.meta.url), 'utf8')));
assert.equal(s.phase, 'done');
assert.equal(s.players.length, 26);

console.log(`OK: ${checked} full tournaments (3–40 players) and validation tests`);

// ---- tables, table assignment and simulated match times ----
{
  store.clear();
  E.freshWithPlayers(Array.from({ length: 8 }, (_, i) => 'P' + (i + 1)));
  E.request('api/admin', { action: 'settings', tables: 3 });
  assert.equal(E.request('api/state').tables, 3);
  assert.throws(() => E.request('api/admin', { action: 'settings', tables: 0 }));
  assert.throws(() => E.request('api/admin', { action: 'settings', tables: 17 }));
  E.request('api/admin', { action: 'draw' });
  const ms = E.request('api/state').r1;
  const [m1, m2, m3] = ms;
  for (const m of [m1, m2, m3]) E.request('api/start', { stage: 'r1', id: m.id });
  let r1 = E.request('api/state').r1;
  assert.deepEqual([m1, m2, m3].map((m) => r1.find((x) => x.id === m.id).tbl), [1, 2, 3]);
  E.request('api/score', { stage: 'r1', id: m2.id, pa: 11, pb: 5 });
  E.request('api/start', { stage: 'r1', id: ms[3].id });
  r1 = E.request('api/state').r1;
  assert.equal(r1.find((x) => x.id === ms[3].id).tbl, 2, 'the freed table is reused');
  assert.ok(r1.find((x) => x.id === m2.id).fin, 'finish time recorded');
}
{
  store.clear();
  E.freshWithPlayers(Array.from({ length: 16 }, (_, i) => 'P' + (i + 1)));
  E.request('api/admin', { action: 'draw' });
  E.simulateStage();
  const s = E.request('api/state');
  const durs = s.r1.map((m) => Date.parse(m.t1) - Date.parse(m.t0)).map((d) => d / 60000);
  const avg = durs.reduce((a, b) => a + b, 0) / durs.length;
  assert.ok(avg > 3 && avg < 7, 'simulated matches last about 5 minutes, got ' + avg);
  assert.ok(Date.parse(s.r1[s.r1.length - 1].fin) <= Date.now() + 1000, 'no times in the future');
}
