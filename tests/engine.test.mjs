// Symulacje pełnych turniejów na silniku przeglądarkowym (docs/engine.js).
// Uruchom: node tests/engine.test.mjs
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
    assert.ok(guard++ < 200, 'turniej się zapętlił');
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
  assert.ok(Math.min(...played) >= 2, `n=${n}: każdy gra co najmniej dwa mecze`);
  const first = new Set(s.ko.rounds[0].flatMap((m) => [m.a, m.b]));
  assert.equal(first.size, s.K, `n=${n}: pełna drabinka`);
  checked++;
}

// Wynik punktowy jest walidowany tak jak na serwerze.
store.clear();
E.freshWithPlayers(['A1', 'B2', 'C3', 'D4']);
let s = E.request('api/admin', { action: 'draw' });
assert.throws(() => E.request('api/score', { stage: 'r1', id: s.r1[0].id, pa: 11, pb: 10 }));
s = E.request('api/score', { stage: 'r1', id: s.r1[0].id, pa: 12, pb: 10 });
assert.deepEqual([s.r1[0].sa, s.r1[0].sb], [1, 0]);

// Mecz do dwóch setów: wynik meczu liczy się z punktów każdego seta.
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
s = E.request('api/score', { stage: 'r1', id: mid, sa: 2, sb: 0 }); // sam wynik w setach nadal działa
assert.equal(s.r1[0].sets, null);
assert.throws(() => E.request('api/admin', { action: 'settings', scoreMode: 'nonsense' }));
// Losowe turnieje do 2 i 3 setów (w obu trybach wpisywania) dochodzą do podium.
for (const [sets, mode] of [[2, 'points'], [3, 'points'], [2, 'sets'], [1, 'sets']]) {
  store.clear();
  E.freshWithPlayers(Array.from({ length: 13 }, (_, i) => `Gracz ${i}`));
  E.request('api/admin', { action: 'settings', setsToWin: sets, scoreMode: mode });
  s = playOut(E.request('api/admin', { action: 'draw' }));
  assert.equal(s.phase, 'done');
  const withSets = s.r1.filter((m) => Array.isArray(m.sets)).length;
  assert.equal(withSets > 0, sets > 1 && mode === 'points', `sets=${sets} mode=${mode}`);
}

// Przykładowe dane z demo dają się wczytać i są zakończonym turniejem.
store.clear();
s = E.loadState(JSON.parse(readFileSync(new URL('../docs/demo-data.json', import.meta.url), 'utf8')));
assert.equal(s.phase, 'done');
assert.equal(s.players.length, 26);

console.log(`OK: ${checked} pełnych turniejów (3–40 graczy) i testy walidacji`);
