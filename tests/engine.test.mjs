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

// Przykładowe dane z demo dają się wczytać i są zakończonym turniejem.
store.clear();
s = E.loadState(JSON.parse(readFileSync(new URL('../docs/demo-data.json', import.meta.url), 'utf8')));
assert.equal(s.phase, 'done');
assert.equal(s.players.length, 26);

console.log(`OK: ${checked} pełnych turniejów (3–40 graczy) i testy walidacji`);
