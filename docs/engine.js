/*
 * In-browser tournament engine (demo mode on GitHub Pages).
 *
 * A faithful port of the logic in server/server.py to JavaScript.
 * The frontend talks to it the same way it talks to the server: TTEngine.request(path, body)
 * takes the same paths (api/state, api/join, api/leave, api/score, api/start, api/admin)
 * and returns the same public state. The state lives in localStorage and the visitor
 * is always the organizer.
 * Err(...) messages are user-facing text kept in Polish (the original interface language);
 * the page translates them for the English UI (ERR_EN in index.html).
 */
(function (global) {
  'use strict';

  const STORE = 'tt_demo_state_v1';
  const MAX_PLAYERS = 64;
  const DEFAULT_TITLE = 'Firmowy Turniej Tenisa Stołowego';

  class Err extends Error {}

  const now = () => new Date().toISOString();
  const hex = (n) => Array.from({ length: n }, () => Math.floor(Math.random() * 16).toString(16)).join('');
  const mid = () => hex(8);
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const isInt = (v) => typeof v === 'number' && Number.isInteger(v);

  function shuffle(a) {
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }
  const choice = (a) => a[Math.floor(Math.random() * a.length)];

  function newState() {
    return {
      title: DEFAULT_TITLE, edition: 'Edycja 2026', prize: '', setsToWin: 1, scoreMode: 'points', format: 'rounds', groupSize: 4, groupAdvance: 2, groups: [], gm: [],
      koSize: null, llSpots: null, deadline: null, phase: 'signup', players: [],
      r1: [], r2: [], r3: [], r4: [], r5: [], r6: [], rounds: 2, ll: [], llDirect: [], sel: null, ko: null, rev: 0, updated: now(),
    };
  }

  // ---------- tournament logic (1:1 with server.py) ----------

  // the random-opponent rounds r1..r6 (the organizer picks how many are played, default 2)
  const ROUND_KEYS = ['r1', 'r2', 'r3', 'r4', 'r5', 'r6'];
  const allRoundMatches = (s) => ROUND_KEYS.flatMap((k) => s[k] || []);

  const valid = (sa, sb, T) => isInt(sa) && isInt(sb) && sa >= 0 && sb >= 0 && Math.max(sa, sb) === T && Math.min(sa, sb) < T;

  function winner(m, T) {
    if (m.a && m.b && valid(m.sa, m.sb, T)) return m.sa > m.sb ? m.a : m.b;
    return null;
  }
  function loser(m, T) {
    const w = winner(m, T);
    if (!w || !m.a || !m.b) return null;
    return w === m.a ? m.b : m.a;
  }

  function pair(ids, everyone, pool = null, played = null) {
    const pk = (a, b) => (a < b ? a + '|' + b : b + '|' + a);
    let best = null, bestScore = Infinity;
    for (let t = 0, tries = played ? 60 : 1; t < tries && bestScore > 0; t++) {
      const xs = shuffle([...ids]);
      const out = [];
      while (xs.length >= 2) out.push({ id: mid(), a: xs.pop(), b: xs.pop(), sa: null, sb: null, half: false, pool });
      if (xs.length) {
        const x = xs.pop();
        const others = everyone.filter((p) => p !== x);
        const fresh = played ? others.filter((o) => !played.has(pk(x, o))) : others;
        if (others.length) out.push({ id: mid(), a: x, b: choice(fresh.length ? fresh : others), sa: null, sb: null, half: true, pool });
      }
      const score = played ? out.filter((m) => played.has(pk(m.a, m.b))).length : 0;
      if (score < bestScore) { bestScore = score; best = out; }
    }
    return best;
  }

  function stats(s) {
    const T = s.setsToWin;
    const st = {};
    s.players.forEach((p) => (st[p.id] = { w: 0, l: 0, sw: 0, sl: 0, pw: 0, pl: 0, played: 0 }));
    for (const m of [...allRoundMatches(s), ...(s.gm || [])]) {
      if (!(m.a && m.b && valid(m.sa, m.sb, T))) continue;
      const pa = m.pa || 0, pb = m.pb || 0;
      const sides = [['a', m.sa, m.sb, pa, pb]];
      if (!m.half) sides.push(['b', m.sb, m.sa, pb, pa]);
      for (const [me, mine, theirs, pm, pt] of sides) {
        const x = st[m[me]];
        if (!x) continue;
        x.played++; x.sw += mine; x.sl += theirs; x.pw += pm; x.pl += pt;
        if (mine > theirs) x.w++; else x.l++;
      }
    }
    return st;
  }

  const key = (x) => [x.w, x.sw - x.sl, x.pw - x.pl, x.pw, x.sw];
  const sameKey = (a, b) => key(a).every((v, i) => v === key(b)[i]);

  // ---------- group format: round-robin groups instead of rounds 1-2 ----------
  const groupSizes = (N, size) => {
    const G = Math.max(1, Math.floor(N / size + 0.5));
    const base = Math.floor(N / G), extra = N % G;
    return Array.from({ length: G }, (_, g) => base + (g < extra ? 1 : 0));
  };
  function makeGroups(ids, size) {
    const sizes = groupSizes(ids.length, size), order = shuffle([...ids]);
    let pos = 0;
    return sizes.map((n, g) => { const out = { id: String.fromCharCode(65 + g), ids: order.slice(pos, pos + n) }; pos += n; return out; });
  }
  // circle method: every player plays everyone in the group once, in rounds
  function roundRobin(ids) {
    const xs = [...ids];
    if (xs.length % 2) xs.push(null);
    const n = xs.length, rounds = [];
    for (let r = 0; r < n - 1; r++) {
      const ps = [];
      for (let i = 0; i < n / 2; i++) { const a = xs[i], b = xs[n - 1 - i]; if (a && b) ps.push([a, b]); }
      rounds.push(ps);
      xs.splice(1, 0, xs.pop());
    }
    return rounds;
  }
  function groupMatches(groups) {
    const out = [];
    groups.forEach((g) => roundRobin(g.ids).forEach((ps, r) => ps.forEach(([a, b]) =>
      out.push({ id: mid(), a, b, sa: null, sb: null, pa: null, pb: null, sets: null, half: false, g: g.id, rd: r + 1 }))));
    return out.sort((x, y) => x.rd - y.rd || (x.g < y.g ? -1 : x.g > y.g ? 1 : 0));
  }
  // players who go straight to the bracket from their group (top `groupAdvance` of every group)
  const groupDirect = (s) => {
    const adv = s.groupAdvance || 2;
    const sizes = (s.groups && s.groups.length) ? s.groups.map((g) => g.ids.length) : groupSizes(s.players.length, s.groupSize || 4);
    return sizes.reduce((n, z) => n + Math.min(adv, z), 0);
  };
  function groupTables(s) {
    const st = stats(s), names = {};
    s.players.forEach((p) => (names[p.id] = p.name));
    const cmp = (i, j) => {
      const ki = key(st[i]), kj = key(st[j]);
      for (let n = 0; n < ki.length; n++) if (ki[n] !== kj[n]) return kj[n] - ki[n];
      const a = names[i].toLowerCase(), b = names[j].toLowerCase();
      return a < b ? -1 : a > b ? 1 : 0;
    };
    return (s.groups || []).map((g) => {
      const ms = (s.gm || []).filter((m) => m.g === g.id);
      const rows = [...g.ids].sort(cmp).map((id, i) => Object.assign({ id, name: names[id], rank: i + 1 }, st[id]));
      return { id: g.id, done: ms.length > 0 && ms.every((m) => m.a && m.b && valid(m.sa, m.sb, s.setsToWin)), rows };
    });
  }
  // Cross-group order: group qualifiers first (by group position, then per-match averages so that
  // groups of different size are comparable), then everyone else the same way.
  function groupRanking(s) {
    const adv = s.groupAdvance || 2;
    const avg = (r) => { const n = Math.max(1, r.played); return [r.w / n, (r.sw - r.sl) / n, (r.pw - r.pl) / n, r.pw / n]; };
    const cmp = (a, b) => {
      const x = avg(a), y = avg(b);
      for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return y[i] - x[i];
      const p = a.name.toLowerCase(), q = b.name.toLowerCase();
      return p < q ? -1 : p > q ? 1 : 0;
    };
    const rows = groupTables(s).flatMap((g) => g.rows.map((r) => { const o = Object.assign({}, r, { group: g.id, grank: r.rank }); delete o.rank; return o; }));
    const byRank = (a, b) => a.grank - b.grank || cmp(a, b);
    return [...rows.filter((r) => r.grank <= adv).sort(byRank), ...rows.filter((r) => r.grank > adv).sort(byRank)];
  }
  // first-round bracket pairs (seed i vs seed K-1-i) should not be two players from the same group
  function avoidGroupClashes(s, ordered) {
    if (s.format !== 'groups') return ordered;
    const grp = {};
    (s.groups || []).forEach((g) => g.ids.forEach((id) => (grp[id] = g.id)));
    const K = ordered.length, out = [...ordered], half = K / 2;
    for (let i = 0; i < half; i++) {
      const ib = K - 1 - i;
      if (!grp[out[i]] || grp[out[i]] !== grp[out[ib]]) continue;
      for (let j = 0; j < half; j++) {
        if (j === i) continue;
        const jb = K - 1 - j;
        if (grp[out[i]] !== grp[out[jb]] && grp[out[j]] !== grp[out[ib]]) { [out[ib], out[jb]] = [out[jb], out[ib]]; break; }
      }
    }
    return out;
  }

  function ranking(s) {
    if (s.format === 'groups') return groupRanking(s);
    const st = stats(s);
    const names = {};
    s.players.forEach((p) => (names[p.id] = p.name));
    const ids = Object.keys(st).sort((i, j) => {
      const ki = key(st[i]), kj = key(st[j]);
      for (let n = 0; n < ki.length; n++) if (ki[n] !== kj[n]) return kj[n] - ki[n];
      const a = names[i].toLowerCase(), b = names[j].toLowerCase();
      return a < b ? -1 : a > b ? 1 : 0;
    });
    return ids.map((i) => Object.assign({ id: i, name: names[i] }, st[i]));
  }

  function pow2Floor(n) {
    let k = 1;
    while (k * 2 <= n) k *= 2;
    return Math.max(k, 2);
  }
  function defaultK(n) {
    let k = 2;
    while (k * 2 <= Math.max(2, Math.floor(n * 0.75))) k *= 2;
    return Math.min(k, pow2Floor(n));
  }
  function koSize(s) {
    const N = s.players.length;
    let k = s.koSize;
    if (!k) {
      if (s.format === 'groups') { k = 2; const d = groupDirect(s); while (k < d) k *= 2; }
      else k = defaultK(N);
    }
    return Math.min(k, pow2Floor(N));
  }

  function boundaryTie(rank, K) {
    if (rank.length <= K) return [];
    if (!sameKey(rank[K], rank[K - 1])) return [];
    return rank.filter((r) => sameKey(r, rank[K - 1])).map((r) => r.id);
  }

  function buildKo(s, ordered) {
    ordered = avoidGroupClashes(s, ordered);
    const K = ordered.length;
    const r0 = [];
    for (let i = 0; i < K / 2; i++) r0.push({ id: mid(), a: ordered[i], b: ordered[K - 1 - i], sa: null, sb: null });
    const rounds = [r0];
    let n = K / 2;
    while (n > 1) {
      n /= 2;
      rounds.push(Array.from({ length: n }, () => ({ id: mid(), a: null, b: null, sa: null, sb: null })));
    }
    const third = K >= 4 ? { id: mid(), a: null, b: null, sa: null, sb: null } : null;
    s.ko = { rounds, third };
    s.phase = 'ko';
    propagate(s);
  }

  function setp(m, a, b) {
    if (m.a !== a || m.b !== b) {
      Object.assign(m, { a, b, sa: null, sb: null, pa: null, pb: null, sets: null });
      delete m.t0; delete m.t1;
    }
  }

  function propagate(s) {
    const T = s.setsToWin, ko = s.ko, R = ko.rounds;
    for (let r = 1; r < R.length; r++)
      R[r].forEach((m, i) => setp(m, winner(R[r - 1][2 * i], T), winner(R[r - 1][2 * i + 1], T)));
    if (ko.third && R.length >= 2) {
      const semis = R[R.length - 2];
      setp(ko.third, loser(semis[0], T), loser(semis[1], T));
    }
    const done = winner(R[R.length - 1][0], T) && (!ko.third || winner(ko.third, T));
    s.phase = done ? 'done' : 'ko';
  }

  const allDone = (ms, T) => ms.every((m) => m.a && m.b && valid(m.sa, m.sb, T));

  // wins of every player in the rounds played so far (an extra "half" match only counts for its first player)
  function winsSoFar(s) {
    const T = s.setsToWin, w = {};
    s.players.forEach((p) => (w[p.id] = 0));
    for (const m of allRoundMatches(s)) {
      const win = winner(m, T);
      if (win && (win === m.a || !m.half)) w[win] = (w[win] || 0) + 1;
    }
    return w;
  }

  // Round k >= 2: players with the same number of wins play each other (round 2 = winners vs winners,
  // losers vs losers). From round 3 on, rematches are avoided where possible.
  function nextRound(s, k) {
    const ids = s.players.map((p) => p.id), w = winsSoFar(s), played = new Set();
    for (const m of allRoundMatches(s)) if (m.a && m.b) played.add(m.a < m.b ? m.a + '|' + m.b : m.b + '|' + m.a);
    const byWins = {};
    ids.forEach((i) => (byWins[w[i]] = byWins[w[i]] || []).push(i));
    const out = [];
    Object.keys(byWins).map(Number).sort((a, b) => b - a).forEach((wins) => {
      const grp = byWins[wins];
      out.push(...pair(grp, grp.length > 1 ? grp : ids, k === 2 ? (wins > 0 ? 'win' : 'lose') : 'w' + wins, k > 2 ? played : null));
    });
    return out;
  }

  function llPlan(s) {
    const N = s.players.length, K = koSize(s);
    let L;
    if (s.format === 'groups') {
      // the best of the rest go straight in by default; Lucky Losers only fill spots beyond the group qualifiers
      L = s.llSpots == null ? 0 : s.llSpots;
      L = Math.max(0, Math.min(L, K - Math.min(groupDirect(s), K), N - K));
    } else {
      L = s.llSpots == null ? Math.max(1, Math.floor(K / 4)) : s.llSpots; // 0 = no Lucky Losers
      L = Math.max(0, Math.min(L, K, N - K));
    }
    return [K, K - L, L];
  }

  function startLl(s) {
    const rank = ranking(s).map((r) => r.id);
    const [K, D, L] = llPlan(s);
    s.llDirect = rank.slice(0, D);
    const pool = rank.slice(D, D + 2 * L);
    s.ll = [];
    for (let i = 0; i < L; i++) s.ll.push({ id: mid(), a: pool[i], b: pool[2 * L - 1 - i], sa: null, sb: null, half: false, pool: 'll' });
    s.phase = 'll';
    if (!s.ll.length) buildKo(s, rank.slice(0, K));
  }

  function advance(s) {
    const T = s.setsToWin;
    const R = s.rounds || 2;
    for (let k = 1; k <= R; k++) {
      const key = 'r' + k;
      if (s.phase === key && (s[key] || []).length && allDone(s[key], T)) {
        if (k < R) { s['r' + (k + 1)] = nextRound(s, k + 1); s.phase = 'r' + (k + 1); }
        else startLl(s);
      }
    }
    if (s.phase === 'groups' && (s.gm || []).length && allDone(s.gm, T)) startLl(s);
    if (s.phase === 'll' && allDone(s.ll || [], T)) {
      const rank = ranking(s).map((r) => r.id);
      const winners = s.ll.map((m) => winner(m, T)).sort((a, b) => rank.indexOf(a) - rank.indexOf(b));
      buildKo(s, [...(s.llDirect || []), ...winners]);
    }
  }

  function draw(s) {
    if (s.players.length < 3) throw new Err('Do losowania potrzeba co najmniej 3 zawodników.');
    const ids = s.players.map((p) => p.id);
    if (s.format === 'groups') {
      s.groups = makeGroups(ids, s.groupSize || 4);
      s.gm = groupMatches(s.groups);
      Object.assign(s, { r1: [], r2: [], r3: [], r4: [], r5: [], r6: [], sel: null, ko: null, ll: [], llDirect: [], phase: 'groups' });
    } else {
      s.r1 = pair(ids, ids);
      Object.assign(s, { r2: [], r3: [], r4: [], r5: [], r6: [], sel: null, ko: null, ll: [], llDirect: [], phase: 'r1', groups: [], gm: [] });
    }
    advance(s);
  }

  function checkDeadline(s) {
    if (s.phase === 'signup' && s.deadline && s.players.length >= 3 && Date.now() >= Date.parse(s.deadline)) {
      draw(s);
      return true;
    }
    return false;
  }

  function findMatch(s, stage, id) {
    let pool = [];
    if ([...ROUND_KEYS, 'll'].includes(stage)) pool = s[stage] || [];
    else if (stage === 'g') pool = s.gm || [];
    else if (stage === 'ko' && s.ko) pool = s.ko.rounds.flat();
    else if (stage === 'third' && s.ko && s.ko.third) pool = [s.ko.third];
    const m = pool.find((x) => x.id === id);
    if (!m) throw new Err('Nie znaleziono meczu.');
    return m;
  }

  const STAGE_PHASE = { r1: ['r1'], r2: ['r2'], r3: ['r3'], r4: ['r4'], r5: ['r5'], r6: ['r6'], g: ['groups'], ll: ['ll'], ko: ['ko', 'done'], third: ['ko', 'done'] };
  const pointsOk = (pa, pb) => [pa, pb].every((v) => isInt(v) && v >= 0 && v <= 99) && Math.max(pa, pb) >= 11 && Math.abs(pa - pb) >= 2;

  // Match to several sets: the points of every set are entered, the match score (e.g. 2:1) is derived from them.
  function scoreSets(sets, T) {
    const tooMany = () => new Err(`Za dużo setów: mecz kończy się, gdy ktoś wygra ${T} sety.`);
    if (sets.length > 2 * T - 1) throw tooMany();
    let wa = 0, wb = 0, pa = 0, pb = 0; const out = [];
    for (const st of sets) {
      if (!Array.isArray(st) || st.length !== 2 || !pointsOk(st[0], st[1])) throw new Err('Wpisz punkty każdego seta: zwycięzca ma co najmniej 11 i 2 punkty przewagi (np. 11:9, 12:10).');
      if (wa === T || wb === T) throw tooMany();
      if (st[0] > st[1]) wa++; else wb++;
      pa += st[0]; pb += st[1]; out.push([st[0], st[1]]);
    }
    if (wa !== T && wb !== T) throw new Err(`Mecz nie jest rozstrzygnięty: zwycięzca musi wygrać ${T} sety.`);
    return { sa: wa, sb: wb, pa, pb, sets: out };
  }

  function setScore(s, stage, id, sa, sb, pa = null, pb = null, sets = null) {
    const T = s.setsToWin;
    let setList = null;
    if (Array.isArray(sets) && sets.length) {
      if (T === 1) {
        if (sets.length !== 1) throw new Err('Mecz do jednego seta ma jeden wynik.');
        [pa, pb] = sets[0];
      } else {
        const r = scoreSets(sets, T);
        ({ sa, sb, pa, pb } = r); setList = r.sets;
      }
    }
    if (setList === null) {
      if (pa !== null && pa !== undefined || pb !== null && pb !== undefined) {
        if (!pointsOk(pa, pb)) throw new Err('Wpisz punkty seta: zwycięzca ma co najmniej 11 i 2 punkty przewagi (np. 11:7, 12:10).');
        if (T !== 1) throw new Err('Przy meczu do kilku setów wpisz punkty każdego seta.');
        [sa, sb] = pa > pb ? [1, 0] : [0, 1];
      } else { pa = null; pb = null; }
    }
    if (!STAGE_PHASE[stage]) throw new Err('Nieznany etap.');
    const m = findMatch(s, stage, id);
    if (!(m.a && m.b)) throw new Err('W tym meczu nie ma jeszcze obu zawodników.');
    const clearing = (sa === null || sa === undefined) && (sb === null || sb === undefined);
    if (!clearing && !valid(sa, sb, T)) throw new Err(`Mecz gramy do ${T} wygranych setów. Sprawdź wynik.`);
    if ([...ROUND_KEYS, 'g', 'll'].includes(stage) && !STAGE_PHASE[stage].includes(s.phase)) {
      const oldW = winner(m, T);
      const newW = clearing ? null : sa > sb ? m.a : m.b;
      if (oldW !== newW) throw new Err('Ta zmiana zmienia zwycięzcę zamkniętej rundy. Najpierw cofnij turniej do tej rundy w panelu organizatora.');
    }
    Object.assign(m, clearing ? { sa: null, sb: null, pa: null, pb: null, sets: null } : { sa, sb, pa, pb, sets: setList });
    // Match time: the organizer records the start, entering the result is the end.
    if (clearing) { delete m.t0; delete m.t1; } else if (m.t0 && !m.t1) m.t1 = now();
    if (stage === 'ko' || stage === 'third') propagate(s); else advance(s);
  }

  function startMatch(s, stage, id, cancel) {
    if (!STAGE_PHASE[stage]) throw new Err('Nieznany etap.');
    const m = findMatch(s, stage, id);
    if (!(m.a && m.b)) throw new Err('W tym meczu nie ma jeszcze obu zawodników.');
    if (m.sa !== null && m.sa !== undefined) throw new Err('Ten mecz ma już wynik.');
    if (cancel) { delete m.t0; delete m.t1; } else { m.t0 = now(); delete m.t1; }
  }

  function cleanName(n) {
    n = String(n || '').split(/\s+/).filter(Boolean).join(' ').slice(0, 28);
    if (n.length < 2) throw new Err('Wpisz imię (co najmniej 2 znaki).');
    return n;
  }

  function addPlayer(s, name) {
    if (s.phase !== 'signup') throw new Err('Zapisy są już zamknięte.');
    if (s.players.length >= MAX_PLAYERS) throw new Err('Lista jest pełna.');
    const nm = cleanName(name);
    if (s.players.some((p) => p.name.toLowerCase() === nm.toLowerCase()))
      throw new Err('Ktoś już jest zapisany pod tym imieniem. Dodaj nazwisko lub inicjał.');
    const tok = hex(32);
    const p = { id: hex(8), name: nm, tok, at: now() };
    s.players.push(p);
    return [p, tok];
  }

  function emptyKey(v) { return v === null || v === undefined || v === '' || v === 0 || v === 'auto'; }

  function adminAction(s, body) {
    const act = body.action;
    if (act === 'settings') {
      if ('title' in body) s.title = String(body.title).trim().slice(0, 120) || DEFAULT_TITLE;
      if ('edition' in body) s.edition = String(body.edition).trim().slice(0, 60);
      if ('prize' in body) s.prize = String(body.prize || '').trim().slice(0, 80);
      if ('setsToWin' in body) {
        const v = parseInt(body.setsToWin, 10);
        if (![1, 2, 3].includes(v)) throw new Err('Mecz może być do 1, 2 albo 3 wygranych setów.');
        if (s.phase !== 'signup' && v !== s.setsToWin) throw new Err('Format meczu można zmienić tylko przed losowaniem.');
        s.setsToWin = v;
      }
      if ('koSize' in body) {
        const v = emptyKey(body.koSize) ? null : parseInt(body.koSize, 10);
        if (v !== null && ![2, 4, 8, 16, 32, 64].includes(v)) throw new Err('Drabinka może mieć 2, 4, 8, 16, 32 lub 64 miejsca.');
        if (['ko', 'done'].includes(s.phase) && v !== (s.koSize || null)) throw new Err('Drabinka już ruszyła. Cofnij turniej, aby zmienić jej rozmiar.');
        s.koSize = v;
      }
      if ('llSpots' in body) {
        const v = [null, undefined, '', 'auto'].includes(body.llSpots) ? null : parseInt(body.llSpots, 10);
        if (v !== null && !(v >= 0 && v <= 16)) throw new Err('Miejsc dla Lucky Losers może być od 0 do 16.');
        if (v !== (s.llSpots ?? null)) {
          if (['ko', 'done'].includes(s.phase)) throw new Err('Drabinka już ruszyła. Cofnij do Lucky Losers lub rundy 2, aby to zmienić.');
          if (s.phase === 'll' && (s.ll || []).some((m) => m.sa !== null)) throw new Err('W Lucky Losers są już wyniki. Cofnij do rundy 2, aby zmienić liczbę miejsc.');
          s.llSpots = v;
          if (s.phase === 'll') startLl(s);
        }
      }
      if ('rounds' in body) {
        const v = parseInt(body.rounds, 10);
        if (!(v >= 1 && v <= 6)) throw new Err('Liczba rund może wynosić od 1 do 6.');
        if (s.phase !== 'signup' && v !== (s.rounds || 2)) throw new Err('Liczbę rund można zmienić tylko przed losowaniem.');
        s.rounds = v;
      }
      if ('format' in body) {
        if (!['rounds', 'groups'].includes(body.format)) throw new Err('Nieznany format turnieju.');
        if (s.phase !== 'signup' && body.format !== (s.format || 'rounds')) throw new Err('Format turnieju można zmienić tylko przed losowaniem.');
        s.format = body.format;
      }
      if ('groupSize' in body) {
        const v = parseInt(body.groupSize, 10);
        if (!(v >= 3 && v <= 6)) throw new Err('Grupa może mieć od 3 do 6 graczy.');
        if (s.phase !== 'signup' && v !== (s.groupSize || 4)) throw new Err('Wielkość grup można zmienić tylko przed losowaniem.');
        s.groupSize = v;
      }
      if ('groupAdvance' in body) {
        const v = parseInt(body.groupAdvance, 10);
        if (![1, 2].includes(v)) throw new Err('Z grupy może awansować 1 lub 2 graczy.');
        if (s.phase !== 'signup' && v !== (s.groupAdvance || 2)) throw new Err('Liczbę awansujących z grupy można zmienić tylko przed losowaniem.');
        s.groupAdvance = v;
      }
      if ('scoreMode' in body) {
        if (!['points', 'sets'].includes(body.scoreMode)) throw new Err('Nieznany tryb wpisywania wyników.');
        s.scoreMode = body.scoreMode;
      }
      if ('deadline' in body) {
        if (body.deadline && isNaN(Date.parse(body.deadline))) throw new Err('Błędna data.');
        s.deadline = body.deadline || null;
      }
    } else if (act === 'rename') {
      const p = s.players.find((x) => x.id === body.id);
      if (!p) throw new Err('Nie ma takiego zawodnika.');
      const nm = cleanName(body.name);
      if (s.players.some((q) => q.name.toLowerCase() === nm.toLowerCase() && q.id !== p.id)) throw new Err('Ktoś już gra pod tym imieniem.');
      p.name = nm;
    } else if (act === 'remove') {
      if (s.phase !== 'signup') throw new Err('Zawodnika można usunąć tylko w trakcie zapisów.');
      s.players = s.players.filter((p) => p.id !== body.id);
    } else if (act === 'add') {
      addPlayer(s, body.name);
    } else if (act === 'late') {
      if (!['groups', ...ROUND_KEYS].includes(s.phase)) throw new Err('Spóźnionego gracza można dopisać w trakcie rund lub grup.');
      const others = s.players.map((p) => p.id);
      const phase = s.phase;
      s.phase = 'signup';
      let p;
      try { [p] = addPlayer(s, body.name); } finally { s.phase = phase; }
      if (phase === 'groups') {
        // the new player joins the smallest group and plays everyone in it
        const g = [...s.groups].sort((x, y) => x.ids.length - y.ids.length || (x.id < y.id ? -1 : 1))[0];
        let rd = Math.max(0, ...s.gm.filter((m) => m.g === g.id).map((m) => m.rd || 0));
        g.ids.forEach((o) => s.gm.push({ id: mid(), a: p.id, b: o, sa: null, sb: null, pa: null, pb: null, sets: null, half: false, g: g.id, rd: ++rd }));
        g.ids.push(p.id);
      } else if (phase === 'r1') {
        const spare = s.r1.find((m) => m.half && m.sa === null);
        if (spare) Object.assign(spare, { b: p.id, half: false });
        else s.r1.push({ id: mid(), a: p.id, b: choice(others), sa: null, sb: null, half: true, pool: null });
      } else {
        // joining in round k: k matches against random opponents, counted only for the late player
        const k = +phase.slice(1);
        for (const opp of shuffle([...others]).slice(0, Math.min(k, others.length)))
          s[phase].push({ id: mid(), a: p.id, b: opp, sa: null, sb: null, half: true, pool: k === 2 ? 'lose' : 'w0' });
      }
    } else if (act === 'draw') {
      if (s.phase !== 'signup') throw new Err('Losowanie już się odbyło.');
      draw(s);
    } else if (act === 'reopen') {
      Object.assign(s, { phase: 'signup', r1: [], r2: [], r3: [], r4: [], r5: [], r6: [], groups: [], gm: [], sel: null, ko: null, ll: [], llDirect: [], deadline: null });
    } else if (act === 'rollback') {
      const to = body.to;
      const rk = ROUND_KEYS.indexOf(to);
      if (rk >= 0 && (s[to] || []).length) {
        const later = {};
        ROUND_KEYS.slice(rk + 1).forEach((q) => (later[q] = []));
        Object.assign(s, later, { phase: to, sel: null, ko: null, ll: [], llDirect: [] });
        if (rk > 0) advance(s);
      }
      else if (to === 'groups' && (s.gm || []).length) { Object.assign(s, { phase: 'groups', sel: null, ko: null, ll: [], llDirect: [] }); advance(s); }
      else if (to === 'll' && (s.ll || []).length) { Object.assign(s, { phase: 'll', ko: null }); advance(s); }
      else throw new Err('Nie można cofnąć do tego etapu.');
    } else if (act === 'reset') {
      const ns = newState();
      for (const k of ['title', 'edition', 'prize', 'setsToWin', 'scoreMode', 'rounds', 'format', 'groupSize', 'groupAdvance', 'koSize', 'llSpots']) ns[k] = s[k] ?? ns[k];
      if (body.keepPlayers) ns.players = s.players;
      ns.rev = s.rev || 0;
      Object.keys(s).forEach((k) => delete s[k]);
      Object.assign(s, ns);
    } else {
      throw new Err('Nieznana akcja.');
    }
  }

  function publicState(s) {
    const out = clone(s);
    out.players = s.players.map((p) => ({ id: p.id, name: p.name }));
    const rank = (s.r1.length || (s.gm || []).length) ? ranking(s) : [];
    out.ranking = rank;
    const K = s.players.length >= 2 ? koSize(s) : 0;
    out.K = K;
    out.ll = out.ll || [];
    out.llDirect = out.llDirect || [];
    if (s.players.length >= 2) [, out.D, out.L] = llPlan(s);
    out.tie = s.phase === 'qual' ? boundaryTie(rank, K) : [];
    out.groupTables = s.format === 'groups' ? groupTables(s) : [];
    out.D0 = s.format === 'groups' && s.players.length >= 2 ? Math.min(groupDirect(s), out.K) : 0;
    out.admin = true;
    out.demo = true;
    out.serverTime = now();
    return out;
  }

  // ---------- storage and the "server" ----------

  let state = null;
  function load() {
    if (state) return state;
    try { state = JSON.parse(localStorage.getItem(STORE)); } catch (e) { state = null; }
    return state;
  }
  function save(s) {
    s.rev = (s.rev || 0) + 1;
    s.updated = now();
    state = s;
    try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) { /* private mode: the state stays in memory */ }
  }

  function request(path, body) {
    path = String(path).replace(/^\/?/, '');
    const s = clone(load() || newState());
    try {
      if (path === 'api/state') {
        if (checkDeadline(s)) save(s);
        return publicState(s);
      }
      body = body || {};
      const extra = {};
      checkDeadline(s);
      if (path === 'api/join') {
        const [p, tok] = addPlayer(s, body.name);
        Object.assign(extra, { me: p.id, token: tok });
      } else if (path === 'api/leave') {
        if (s.phase !== 'signup') throw new Err('Zapisy są zamknięte, wypisać może tylko organizator.');
        const p = s.players.find((x) => x.id === body.id);
        if (!p || p.tok !== String(body.token || '')) throw new Err('Nie możesz wypisać tej osoby.');
        s.players = s.players.filter((x) => x !== p);
      } else if (path === 'api/score') {
        setScore(s, body.stage, body.id, body.sa, body.sb, body.pa, body.pb, body.sets);
      } else if (path === 'api/start') {
        startMatch(s, body.stage, body.id, !!body.cancel);
      } else if (path === 'api/admin') {
        adminAction(s, body);
      } else {
        throw new Err('Nie znaleziono.');
      }
      save(s);
      return Object.assign(publicState(s), extra);
    } catch (e) {
      // The operation works on a copy, so an error halfway does not leave a broken state.
      throw e instanceof Err ? new Error(e.message) : e;
    }
  }

  // ---------- demo-only helpers ----------

  function loadState(s) { const c = clone(s); c.rev = (state && state.rev) || 0; save(c); return publicState(c); }

  function freshWithPlayers(names) {
    const s = newState();
    s.prize = 'Puchar i wieczna chwała';
    names.forEach((n) => addPlayer(s, n));
    save(s);
    return publicState(s);
  }

  // Plays all ready matches of the current stage at random.
  function simulateStage() {
    if (!load()) return null;
    const s = clone(load());
    const T = s.setsToWin;
    const pointsMode = (s.scoreMode || 'points') === 'points';
    const stage = { r1: 'r1', r2: 'r2', r3: 'r3', r4: 'r4', r5: 'r5', r6: 'r6', groups: 'g', ll: 'll', ko: 'ko' }[s.phase];
    if (!stage) throw new Error('Na tym etapie nie ma meczów do rozegrania.');
    const pool = stage === 'ko' ? [...s.ko.rounds.flat().map((m) => ['ko', m]), ...(s.ko.third ? [['third', s.ko.third]] : [])] : stage === 'g' ? (s.gm || []).map((m) => ['g', m]) : s[stage].map((m) => [stage, m]);
    const todo = pool.filter(([, m]) => m.a && m.b && m.sa === null);
    if (!todo.length) throw new Error('Wszystkie gotowe mecze tego etapu mają już wyniki.');
    // In the bracket we play one round at a time so the winners can be seen advancing.
    const firstRound = stage === 'ko' ? s.ko.rounds.findIndex((r) => r.some((m) => m.a && m.b && m.sa === null)) : -1;
    for (const [st, m] of todo) {
      if (stage === 'ko' && st === 'ko' && !s.ko.rounds[firstRound].includes(m)) continue;
      if (T === 1) {
        // ~15% of matches reach deuce (10:10); every further exchange extends the game with ~45% chance,
        // so 12:10 is common while 15:13 or 18:16 are rare but do happen.
        let lose;
        if (Math.random() < 0.15) { lose = 10; while (lose < 40 && Math.random() < 0.45) lose++; } else lose = Math.floor(Math.random() * 10);
        const [pa, pb] = Math.random() < 0.5 ? [lose >= 10 ? lose + 2 : 11, lose] : [lose, lose >= 10 ? lose + 2 : 11];
        m.t0 = new Date(Date.now() - (5 + (pa + pb) * 0.5 + Math.random() * 6) * 60000).toISOString();
        if (pointsMode) setScore(s, st, m.id, null, null, pa, pb);
        else setScore(s, st, m.id, pa > pb ? 1 : 0, pa > pb ? 0 : 1);
      } else {
        const l = Math.floor(Math.random() * T);
        const aWins = Math.random() < 0.5;
        // set order: the match winner takes the last set, the loser has l sets before it
        const order = Array(T - 1).fill(true).concat(Array(l).fill(false));
        for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
        order.push(true);
        const sets = order.map((winWins) => {
          let lose = Math.random() < 0.15 ? 10 : Math.floor(Math.random() * 10);
          if (lose === 10) while (lose < 40 && Math.random() < 0.45) lose++;
          const win = lose >= 10 ? lose + 2 : 11;
          return (winWins === aWins) ? [win, lose] : [lose, win];
        });
        m.t0 = new Date(Date.now() - (8 + sets.length * 6 + Math.random() * 10) * 60000).toISOString();
        if (pointsMode) setScore(s, st, m.id, null, null, null, null, sets);
        else setScore(s, st, m.id, aWins ? T : l, aWins ? l : T);
      }
    }
    save(s);
    return publicState(s);
  }

  global.TTEngine = { request, loadState, freshWithPlayers, simulateStage, hasState: () => !!load(), _internals: { ranking, llPlan, koSize, valid } };
})(typeof window !== 'undefined' ? window : globalThis);
