#!/usr/bin/env python3
"""Table tennis tournament: a small HTTP server (standard library only), state kept in one JSON file.

Run:  python3 server/server.py
Environment variables: TT_DATA (data directory), TT_STATIC (frontend directory, default ../docs),
TT_BIND (address, default 127.0.0.1), TT_PORT (port, default 8187).
On start the server prints the organizer link with the token in the #admin-... part.

Error messages raised with Err(...) are user-facing text kept in Polish, the original language of the
interface. The browser translates them for the English UI (see ERR_EN in docs/index.html).
"""
import hashlib, hmac, json, math, os, random, secrets, threading, datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("TT_DATA", os.path.join(HERE, "data"))
STATIC = os.environ.get("TT_STATIC", os.path.join(HERE, "..", "docs"))
BIND = os.environ.get("TT_BIND", "127.0.0.1")
PORT = int(os.environ.get("TT_PORT", "8187"))
STATE_F = os.path.join(DATA, "state.json")
ADMIN_F = os.path.join(DATA, "admin.token")
LOCK = threading.Lock()
MAX_PLAYERS = 64
DEFAULT_TITLE = "Firmowy Turniej Tenisa Stołowego"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def new_state():
    return {"title": DEFAULT_TITLE, "edition": "Edycja 2026", "prize": "", "setsToWin": 1, "scoreMode": "points",
            "rounds": 2, "format": "rounds", "groupSize": 4, "groupAdvance": 2, "groups": [], "gm": [],
            "koSize": None, "llSpots": None, "advancePct": None, "luckyLosers": False, "llMode": "playoff", "llTable": [], "deadline": None, "phase": "signup", "players": [],
            "r1": [], "r2": [], "r3": [], "r4": [], "r5": [], "r6": [], "ll": [], "llDirect": [], "sel": None, "ko": None, "rev": 0, "updated": now()}


def load():
    try:
        with open(STATE_F, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return new_state()


def save(s):
    s["rev"] = s.get("rev", 0) + 1
    s["updated"] = now()
    tmp = STATE_F + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(tmp, STATE_F)


def admin_token():
    if not os.path.exists(ADMIN_F):
        with open(ADMIN_F, "w") as f:
            f.write(secrets.token_urlsafe(18))
        os.chmod(ADMIN_F, 0o600)
    return open(ADMIN_F).read().strip()


def h(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


class Err(Exception):
    pass


# ---------- tournament logic ----------

def valid(sa, sb, T):
    if not isinstance(sa, int) or not isinstance(sb, int) or isinstance(sa, bool) or isinstance(sb, bool):
        return False
    return sa >= 0 and sb >= 0 and max(sa, sb) == T and min(sa, sb) < T


def winner(m, T):
    a, b = m.get("a"), m.get("b")
    if a and b and valid(m.get("sa"), m.get("sb"), T):
        return a if m["sa"] > m["sb"] else b
    return None


def loser(m, T):
    w = winner(m, T)
    if not w or not m.get("a") or not m.get("b"):
        return None
    return m["b"] if w == m["a"] else m["a"]


def mid():
    return secrets.token_hex(4)


# the random-opponent rounds r1..r6 (the organizer picks how many are played, default 2)
ROUND_KEYS = ("r1", "r2", "r3", "r4", "r5", "r6")


def all_round_matches(s):
    return [m for k in ROUND_KEYS for m in s.get(k, [])]


def pair(ids, rng, everyone, pool=None, played=None):
    def pk(a, b):
        return (a, b) if a < b else (b, a)

    best, best_score = None, float("inf")
    for _ in range(60 if played else 1):
        if best_score <= 0:
            break
        xs = list(ids)
        rng.shuffle(xs)
        out = []
        while len(xs) >= 2:
            out.append({"id": mid(), "a": xs.pop(), "b": xs.pop(), "sa": None, "sb": None, "half": False, "pool": pool})
        if xs:
            x = xs.pop()
            others = [p for p in everyone if p != x]
            fresh = [o for o in others if pk(x, o) not in played] if played else others
            if others:
                opp = rng.choice(fresh or others)
                out.append({"id": mid(), "a": x, "b": opp, "sa": None, "sb": None, "half": True, "pool": pool})
        score = sum(1 for m in out if played and pk(m["a"], m["b"]) in played)
        if score < best_score:
            best, best_score = out, score
    return best


def stats(s):
    T = s["setsToWin"]
    st = {p["id"]: {"w": 0, "l": 0, "sw": 0, "sl": 0, "pw": 0, "pl": 0, "played": 0} for p in s["players"]}
    for m in all_round_matches(s) + s.get("gm", []):
        if not (m["a"] and m["b"] and valid(m["sa"], m["sb"], T)):
            continue
        pa, pb = m.get("pa") or 0, m.get("pb") or 0
        sides = [("a", m["sa"], m["sb"], pa, pb)]
        if not m["half"]:
            sides.append(("b", m["sb"], m["sa"], pb, pa))
        for me, mine, theirs, pm, pt in sides:
            pid = m[me]
            if pid not in st:
                continue
            x = st[pid]
            x["played"] += 1
            x["sw"] += mine
            x["sl"] += theirs
            x["pw"] += pm
            x["pl"] += pt
            if mine > theirs:
                x["w"] += 1
            else:
                x["l"] += 1
    return st


def key(x):
    return (x["w"], x["sw"] - x["sl"], x["pw"] - x["pl"], x["pw"], x["sw"])


# ---------- group format: round-robin groups instead of rounds ----------

def group_sizes(n, size):
    g = max(1, int(n / size + 0.5))
    base, extra = divmod(n, g)
    return [base + (1 if i < extra else 0) for i in range(g)]


def make_groups(ids, size):
    order = list(ids)
    random.Random().shuffle(order)
    out, pos = [], 0
    for i, n in enumerate(group_sizes(len(order), size)):
        out.append({"id": chr(65 + i), "ids": order[pos:pos + n]})
        pos += n
    return out


def round_robin(ids):
    """Circle method: every player plays everyone in the group once, in rounds."""
    xs = list(ids)
    if len(xs) % 2:
        xs.append(None)
    n = len(xs)
    rounds = []
    for _ in range(n - 1):
        ps = []
        for i in range(n // 2):
            a, b = xs[i], xs[n - 1 - i]
            if a and b:
                ps.append((a, b))
        rounds.append(ps)
        xs.insert(1, xs.pop())
    return rounds


def group_matches(groups):
    out = []
    for g in groups:
        for r, ps in enumerate(round_robin(g["ids"])):
            for a, b in ps:
                out.append({"id": mid(), "a": a, "b": b, "sa": None, "sb": None, "pa": None, "pb": None,
                            "sets": None, "half": False, "g": g["id"], "rd": r + 1})
    out.sort(key=lambda m: (m["rd"], m["g"]))
    return out


def group_direct(s):
    """Players who go straight to the bracket from their group (top groupAdvance of every group)."""
    adv = s.get("groupAdvance") or 2
    groups = s.get("groups") or []
    sizes = [len(g["ids"]) for g in groups] if groups else group_sizes(len(s["players"]), s.get("groupSize") or 4)
    return sum(min(adv, z) for z in sizes)


def group_tables(s):
    st = stats(s)
    names = {p["id"]: p["name"] for p in s["players"]}
    out = []
    for g in s.get("groups") or []:
        ms = [m for m in s.get("gm", []) if m["g"] == g["id"]]
        ids = sorted(g["ids"], key=lambda i: tuple(-v for v in key(st[i])) + (names[i].lower(),))
        rows = [dict(id=i, name=names[i], rank=n + 1, **st[i]) for n, i in enumerate(ids)]
        done = bool(ms) and all(m["a"] and m["b"] and valid(m["sa"], m["sb"], s["setsToWin"]) for m in ms)
        out.append({"id": g["id"], "done": done, "rows": rows})
    return out


def group_ranking(s):
    """Cross-group order: group qualifiers first (group position, then per-match averages so that groups of
    different size are comparable), then everyone else the same way."""
    adv = s.get("groupAdvance") or 2

    def avg(r):
        n = max(1, r["played"])
        return (r["w"] / n, (r["sw"] - r["sl"]) / n, (r["pw"] - r["pl"]) / n, r["pw"] / n)

    rows = []
    for g in group_tables(s):
        for r in g["rows"]:
            o = dict(r, group=g["id"], grank=r["rank"])
            del o["rank"]
            rows.append(o)

    def sk(r):
        return (r["grank"],) + tuple(-v for v in avg(r)) + (r["name"].lower(),)

    return sorted((r for r in rows if r["grank"] <= adv), key=sk) + sorted((r for r in rows if r["grank"] > adv), key=sk)


def avoid_group_clashes(s, ordered):
    """The first matches of the bracket should not pair two players from the same group. Only players of the same
    kind swap places (play-in with play-in, bye with bye), so nobody gets or loses a bye through this. A simple
    local search: swap two players while that reduces the number of same-group pairs."""
    if s.get("format") != "groups":
        return ordered
    grp = {i: g["id"] for g in s.get("groups") or [] for i in g["ids"]}
    M = len(ordered)
    out = list(ordered)
    P = 1
    while P * 2 <= M:
        P *= 2
    P = max(P, 2)
    R = M - P
    play_in = [(P - R + j, M - 1 - j) for j in range(R)]
    byes = [(i, P - 1 - i) for i in range(P // 2) if i < P - R and P - 1 - i < P - R]

    def clashes(pairs):
        return sum(1 for a, b in pairs if grp.get(out[a]) and grp.get(out[a]) == grp.get(out[b]))

    def optimise(pairs, positions):
        best = clashes(pairs)
        improved = True
        while best > 0 and improved:
            improved = False
            for i in range(len(positions)):
                for j in range(i + 1, len(positions)):
                    if best == 0:
                        return
                    x, y = positions[i], positions[j]
                    if grp.get(out[x]) == grp.get(out[y]):
                        continue
                    out[x], out[y] = out[y], out[x]
                    c = clashes(pairs)
                    if c < best:
                        best, improved = c, True
                    else:
                        out[x], out[y] = out[y], out[x]

    optimise(play_in, [pos for pair in play_in for pos in pair])  # any two play-in players may swap
    optimise(byes, [hi for _, hi in byes])  # only the weaker side of a bye pair moves
    return out


def ranking(s):
    if s.get("format") == "groups":
        return group_ranking(s)
    st = stats(s)
    names = {p["id"]: p["name"] for p in s["players"]}
    ids = sorted(st, key=lambda i: tuple(-v for v in key(st[i])) + (names[i].lower(),))
    return [dict(id=i, name=names[i], **st[i]) for i in ids]


def pow2_floor(n):
    k = 1
    while k * 2 <= n:
        k *= 2
    return max(k, 2)


def default_k(n):
    k = 2
    while k * 2 <= max(2, int(n * 0.75)):
        k *= 2
    return min(k, pow2_floor(n))


# ---------- knockout stage: who goes through ----------
# The organizer picks how many players advance directly (a percentage of the field, always an even number),
# optionally adds Lucky Losers (decided by a play-off match or simply by the table) on top of them.
ADV_PCTS = (70, 65, 60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10)


def ceil_even(x):
    c = math.ceil(x - 1e-9)
    return c + 1 if c % 2 else c


def direct_for(n, pct):
    return max(2, min(ceil_even(n * pct / 100), n - n % 2))


def recommended_pct(n):
    """The percentage whose direct qualifiers come closest to half of the players."""
    best, best_dist = 50, float("inf")
    for p_ in ADV_PCTS:
        d = abs(direct_for(n, p_) - n / 2)
        if d < best_dist - 1e-9:
            best, best_dist = p_, d
    return best


def recommended_l(pool, mode):
    """About half of the pool for "by the table", half of the play-off candidates otherwise."""
    cap = pool // 2 if mode == "playoff" else pool
    share = 0.25 if mode == "playoff" else 0.5
    n = pool * share / 2
    l = int(math.floor(n + 0.5)) * 2  # round half up, like Math.round
    return max(0, min(max(l, 2), cap))


def ll_pool_groups(s):
    """Groups: the Lucky Losers come from the players placed right behind the qualifiers (the third places)."""
    adv = s.get("groupAdvance") or 2
    groups = s.get("groups") or []
    sizes = [len(g["ids"]) for g in groups] if groups else group_sizes(len(s["players"]), s.get("groupSize") or 4)
    return sum(1 for z in sizes if z > adv)


def knockout_plan(s):
    """M players in the knockout stage, D direct qualifiers, L Lucky Losers, mode 'playoff' | 'table', pool."""
    n = len(s["players"])
    groups = s.get("format") == "groups"
    if s.get("koSize") and s.get("advancePct") is None:  # explicit power-of-two bracket (API only)
        k = min(s["koSize"], pow2_floor(n))
        l = s.get("llSpots")
        if groups:
            l = 0 if l is None else l
            l = max(0, min(l, k - min(group_direct(s), k), n - k))
        else:
            if l is None:
                l = max(1, k // 4)
            l = max(0, min(l, k, n - k))
        return {"M": k, "D": k - l, "L": l, "mode": "playoff", "pool": n - (k - l), "legacy": True}
    pct = s.get("advancePct")
    d = min(group_direct(s), n) if groups else direct_for(n, recommended_pct(n) if pct is None else pct)
    pool = ll_pool_groups(s) if groups else n - d
    mode = "table" if s.get("llMode") == "table" else "playoff"
    l = 0
    if s.get("luckyLosers") is True and pool >= (2 if mode == "playoff" else 1):
        cap = pool // 2 if mode == "playoff" else pool
        want = s.get("llSpots")
        l = max(0, min(recommended_l(pool, mode) if want is None else want, cap))
        if (d + l) % 2:  # keep the number of players even
            l = l - 1 if l > 1 else (l + 1 if l + 1 <= cap else 0)
    if (d + l) % 2:  # odd number of group qualifiers: the best of the rest joins
        l += 1
        mode = "table"
    return {"M": d + l, "D": d, "L": l, "mode": mode, "pool": pool, "legacy": False}


def ko_size(s):
    return knockout_plan(s)["M"]


def boundary_tie(s, rank, K):
    if len(rank) <= K:
        return []
    kk = key(rank[K - 1])
    if key(rank[K]) != kk:
        return []
    return [r["id"] for r in rank if key(r) == kk]


def seed_order(n):
    arr = [1, 2]
    while len(arr) < n:
        m = len(arr) * 2
        arr = [x for s_ in arr for x in (s_, m + 1 - s_)]
    return arr


def build_ko(s, ordered):
    """Any number of players M: P is the largest power of two <= M. The 2R = 2(M-P) lowest seeds play a play-in
    round, the best P-R seeds have a bye. Their winners fill the empty places of a normal bracket of P."""
    ordered = avoid_group_clashes(s, ordered)
    M = len(ordered)
    P = 1
    while P * 2 <= M:
        P *= 2
    P = max(P, 2)
    R = M - P

    def fresh():
        return {"id": mid(), "a": None, "b": None, "sa": None, "sb": None}

    pre = []
    for j in range(R):
        m = fresh()
        m["a"], m["b"] = ordered[P - R + j], ordered[M - 1 - j]
        pre.append(m)
    pre_map, r0 = [], []
    for i in range(P // 2):
        m = fresh()
        for side, slot in (("a", i), ("b", P - 1 - i)):
            if slot < P - R:
                m[side] = ordered[slot]
            else:
                pre_map.append({"i": i, "side": side, "j": slot - (P - R)})
        r0.append(m)
    rounds = [r0]
    n = P // 2
    while n > 1:
        n //= 2
        rounds.append([fresh() for _ in range(n)])
    s["ko"] = {"pre": pre if R else None, "preMap": pre_map, "rounds": rounds, "third": fresh() if P >= 4 else None}
    s["phase"] = "ko"
    propagate(s)


def setp(m, a, b):
    if m["a"] != a or m["b"] != b:
        m["a"], m["b"], m["sa"], m["sb"], m["pa"], m["pb"] = a, b, None, None, None, None
        m["sets"] = None
        m.pop("t0", None)
        m.pop("t1", None)


def set_side(m, side, pid):
    if m[side] != pid:
        m[side] = pid
        m["sa"], m["sb"], m["pa"], m["pb"], m["sets"] = None, None, None, None, None
        m.pop("t0", None)
        m.pop("t1", None)


def propagate(s):
    T = s["setsToWin"]
    ko = s["ko"]
    R = ko["rounds"]
    for pm in ko.get("preMap") or []:
        set_side(R[0][pm["i"]], pm["side"], winner(ko["pre"][pm["j"]], T))
    for r in range(1, len(R)):
        for i, m in enumerate(R[r]):
            setp(m, winner(R[r - 1][2 * i], T), winner(R[r - 1][2 * i + 1], T))
    if ko["third"] is not None and len(R) >= 2:
        semis = R[-2]
        setp(ko["third"], loser(semis[0], T), loser(semis[1], T))
    final = R[-1][0]
    done = winner(final, T) and (ko["third"] is None or winner(ko["third"], T))
    s["phase"] = "done" if done else "ko"


def all_done(ms, T):
    return all(m["a"] and m["b"] and valid(m["sa"], m["sb"], T) for m in ms)


def wins_so_far(s):
    """Wins of every player in the rounds played so far (an extra "half" match only counts for its first player)."""
    T = s["setsToWin"]
    w = {p["id"]: 0 for p in s["players"]}
    for m in all_round_matches(s):
        win = winner(m, T)
        if win and (win == m["a"] or not m["half"]):
            w[win] = w.get(win, 0) + 1
    return w


def next_round(s, k):
    """Round k >= 2: players with the same number of wins play each other (round 2 = winners vs winners,
    losers vs losers). From round 3 on, rematches are avoided where possible."""
    rng = random.Random()
    ids = [p["id"] for p in s["players"]]
    w = wins_so_far(s)
    played = {(m["a"], m["b"]) if m["a"] < m["b"] else (m["b"], m["a"]) for m in all_round_matches(s) if m["a"] and m["b"]}
    by_wins = {}
    for i in ids:
        by_wins.setdefault(w[i], []).append(i)
    out = []
    for wins in sorted(by_wins, reverse=True):
        grp = by_wins[wins]
        pool = ("win" if wins > 0 else "lose") if k == 2 else f"w{wins}"
        out += pair(grp, rng, grp if len(grp) > 1 else ids, pool, played if k > 2 else None)
    return out


def advance(s):
    T = s["setsToWin"]
    R = s.get("rounds") or 2
    for k in range(1, R + 1):
        key_ = f"r{k}"
        if s["phase"] == key_ and s.get(key_) and all_done(s[key_], T):
            if k < R:
                s[f"r{k + 1}"] = next_round(s, k + 1)
                s["phase"] = f"r{k + 1}"
            else:
                start_ll(s)
    if s["phase"] == "groups" and s.get("gm") and all_done(s["gm"], T):
        start_ll(s)
    if s["phase"] == "ll" and all_done(s.get("ll", []), T):
        rank = [r["id"] for r in ranking(s)]
        winners = sorted((winner(m, T) for m in s["ll"]), key=rank.index)
        build_ko(s, list(s.get("llDirect", [])) + winners)


def ll_plan(s):
    pl = knockout_plan(s)
    return pl["M"], pl["D"], pl["L"]


def start_ll(s):
    rank = [r["id"] for r in ranking(s)]
    plan = knockout_plan(s)
    D, L = plan["D"], plan["L"]
    s["llDirect"] = rank[:D]
    s["ll"] = []
    s["llTable"] = []
    s["phase"] = "ll"
    if plan["mode"] == "table" or not L:  # Lucky Losers by the table: no extra matches
        s["llTable"] = rank[D:D + L]
        build_ko(s, rank[:D + L])
        return
    pool = rank[D:D + 2 * L]
    s["ll"] = [{"id": mid(), "a": pool[i], "b": pool[2 * L - 1 - i], "sa": None, "sb": None, "half": False, "pool": "ll"} for i in range(L)]


def draw(s):
    if len(s["players"]) < 3:
        raise Err("Do losowania potrzeba co najmniej 3 zawodników.")
    ids = [p["id"] for p in s["players"]]
    for k in ROUND_KEYS:
        s[k] = []
    s["sel"], s["ko"], s["ll"], s["llDirect"] = None, None, [], []
    if s.get("format") == "groups":
        s["groups"] = make_groups(ids, s.get("groupSize") or 4)
        s["gm"] = group_matches(s["groups"])
        s["phase"] = "groups"
    else:
        s["groups"], s["gm"] = [], []
        s["r1"] = pair(ids, random.Random(), ids)
        s["phase"] = "r1"
    advance(s)


def check_deadline(s):
    if s["phase"] == "signup" and s.get("deadline") and len(s["players"]) >= 3:
        try:
            dl = datetime.datetime.fromisoformat(s["deadline"].replace("Z", "+00:00"))
        except ValueError:
            return False
        if datetime.datetime.now(datetime.timezone.utc) >= dl:
            draw(s)
            return True
    return False


def find_match(s, stage, m_id):
    if stage in ROUND_KEYS + ("ll",):
        pool = s.get(stage, [])
    elif stage == "g":
        pool = s.get("gm", [])
    elif stage == "ko" and s["ko"]:
        pool = [m for r in s["ko"]["rounds"] for m in r]
    elif stage == "third" and s["ko"] and s["ko"]["third"]:
        pool = [s["ko"]["third"]]
    elif stage == "pre" and s["ko"] and s["ko"].get("pre"):
        pool = s["ko"]["pre"]
    else:
        pool = []
    for m in pool:
        if m["id"] == m_id:
            return m
    raise Err("Nie znaleziono meczu.")


STAGE_PHASE = {"r1": ("r1",), "r2": ("r2",), "r3": ("r3",), "r4": ("r4",), "r5": ("r5",), "r6": ("r6",), "g": ("groups",), "ll": ("ll",), "ko": ("ko", "done"), "pre": ("ko", "done"), "third": ("ko", "done")}


def points_ok(pa, pb):
    if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 99 for v in (pa, pb)):
        return False
    return max(pa, pb) >= 11 and abs(pa - pb) >= 2


def score_sets(sets, T):
    """Match to several sets: the points of every set are entered, the match score (e.g. 2:1) is derived from them."""
    too_many = f"Za dużo setów: mecz kończy się, gdy ktoś wygra {T} sety."
    if len(sets) > 2 * T - 1:
        raise Err(too_many)
    wa = wb = pa = pb = 0
    out = []
    for st in sets:
        if not (isinstance(st, list) and len(st) == 2 and points_ok(st[0], st[1])):
            raise Err("Wpisz punkty każdego seta: zwycięzca ma co najmniej 11 i 2 punkty przewagi (np. 11:9, 12:10).")
        if wa == T or wb == T:
            raise Err(too_many)
        if st[0] > st[1]:
            wa += 1
        else:
            wb += 1
        pa += st[0]
        pb += st[1]
        out.append([st[0], st[1]])
    if wa != T and wb != T:
        raise Err(f"Mecz nie jest rozstrzygnięty: zwycięzca musi wygrać {T} sety.")
    return wa, wb, pa, pb, out


def set_score(s, stage, m_id, sa, sb, admin, pa=None, pb=None, sets=None):
    T = s["setsToWin"]
    set_list = None
    if isinstance(sets, list) and sets:
        if T == 1:
            if len(sets) != 1 or not (isinstance(sets[0], list) and len(sets[0]) == 2):
                raise Err("Mecz do jednego seta ma jeden wynik.")
            pa, pb = sets[0]
        else:
            sa, sb, pa, pb, set_list = score_sets(sets, T)
    if set_list is None:
        if pa is not None or pb is not None:
            if not points_ok(pa, pb):
                raise Err("Wpisz punkty seta: zwycięzca ma co najmniej 11 i 2 punkty przewagi (np. 11:7, 12:10).")
            if T != 1:
                raise Err("Przy meczu do kilku setów wpisz punkty każdego seta.")
            sa, sb = (1, 0) if pa > pb else (0, 1)
    if not admin:
        raise Err("Wyniki wpisuje tylko organizator.")
    if stage not in STAGE_PHASE:
        raise Err("Nieznany etap.")
    m = find_match(s, stage, m_id)
    if s["phase"] not in STAGE_PHASE[stage] and not admin:
        raise Err("Ten etap jest już zamknięty. Poproś organizatora o poprawkę.")
    if not (m["a"] and m["b"]):
        raise Err("W tym meczu nie ma jeszcze obu zawodników.")
    clearing = sa is None and sb is None
    if not clearing and not valid(sa, sb, T):
        raise Err(f"Mecz gramy do {T} wygranych setów. Sprawdź wynik.")
    if not admin and m["sa"] is not None:
        raise Err("Wynik tego meczu jest już wpisany. Poprawić go może organizator.")
    if not admin and clearing:
        raise Err("Brak uprawnień.")
    if stage in ROUND_KEYS + ("g", "ll") and s["phase"] not in STAGE_PHASE[stage]:
        old_w = winner(m, T)
        new_w = None if clearing else (m["a"] if sa > sb else m["b"])
        if old_w != new_w:
            raise Err("Ta zmiana zmienia zwycięzcę zamkniętej rundy. Najpierw cofnij turniej do tej rundy w panelu organizatora.")
    m["sa"], m["sb"] = (None, None) if clearing else (sa, sb)
    m["pa"], m["pb"] = (None, None) if clearing else (pa, pb)
    m["sets"] = None if clearing else set_list
    # Match time: the organizer records the start, entering the result is the end.
    if clearing:
        m.pop("t0", None)
        m.pop("t1", None)
    elif m.get("t0") and not m.get("t1"):
        m["t1"] = now()
    if stage in ("ko", "third", "pre"):
        propagate(s)
    else:
        advance(s)


def start_match(s, stage, m_id, cancel=False):
    if stage not in STAGE_PHASE:
        raise Err("Nieznany etap.")
    m = find_match(s, stage, m_id)
    if not (m["a"] and m["b"]):
        raise Err("W tym meczu nie ma jeszcze obu zawodników.")
    if m["sa"] is not None:
        raise Err("Ten mecz ma już wynik.")
    m.pop("t1", None)
    if cancel:
        m.pop("t0", None)
    else:
        m["t0"] = now()


def admin_action(s, body):
    act = body.get("action")
    if act == "settings":
        if "title" in body:
            t = str(body["title"]).strip()[:120]
            s["title"] = t or DEFAULT_TITLE
        if "edition" in body:
            s["edition"] = str(body["edition"]).strip()[:60]
        if "prize" in body:
            s["prize"] = str(body["prize"] or "").strip()[:80]
        if "setsToWin" in body:
            v = int(body["setsToWin"])
            if v not in (1, 2, 3):
                raise Err("Mecz może być do 1, 2 albo 3 wygranych setów.")
            if s["phase"] != "signup" and v != s["setsToWin"]:
                raise Err("Format meczu można zmienić tylko przed losowaniem.")
            s["setsToWin"] = v
        # knockout stage: how many advance, Lucky Losers yes/no, how they are decided, how many
        ko_changed = []

        def ko_key(key, v):
            if s.get(key) == v:
                return
            if s["phase"] in ("ko", "done"):
                raise Err("Drabinka już ruszyła. Cofnij turniej, aby zmienić jej ustawienia.")
            if s["phase"] == "ll" and any(m["sa"] is not None for m in s.get("ll", [])):
                raise Err("W Lucky Losers są już wyniki. Cofnij turniej, aby zmienić ustawienia drabinki.")
            s[key] = v
            ko_changed.append(key)

        if "koSize" in body:
            v = body["koSize"]
            if v in (None, "", 0, "auto"):
                ko_key("koSize", None)
            else:
                v = int(v)
                if v not in (2, 4, 8, 16, 32, 64):
                    raise Err("Drabinka może mieć 2, 4, 8, 16, 32 lub 64 miejsca.")
                ko_key("koSize", v)
        if "advancePct" in body:
            v = body["advancePct"]
            v = None if v in (None, "", "auto") else int(v)
            if v is not None and not (5 <= v <= 95):
                raise Err("Odsetek awansujących musi mieścić się między 5% a 95%.")
            ko_key("advancePct", v)
        if "luckyLosers" in body:
            ko_key("luckyLosers", body["luckyLosers"] is True or body["luckyLosers"] == "true")
        if "llMode" in body:
            if body["llMode"] not in ("playoff", "table"):
                raise Err("Lucky Losers mogą się dogrywać albo awansować z tabeli.")
            ko_key("llMode", body["llMode"])
        if "llSpots" in body:
            v = body["llSpots"]
            v = None if v in (None, "", "auto") else int(v)
            if v is not None and not (0 <= v <= 64):
                raise Err("Miejsc dla Lucky Losers może być od 0 do 64.")
            ko_key("llSpots", v)
        if ko_changed and s["phase"] == "ll":
            start_ll(s)
        if "rounds" in body:
            v = int(body["rounds"])
            if not 1 <= v <= 6:
                raise Err("Liczba rund może wynosić od 1 do 6.")
            if s["phase"] != "signup" and v != (s.get("rounds") or 2):
                raise Err("Liczbę rund można zmienić tylko przed losowaniem.")
            s["rounds"] = v
        if "format" in body:
            if body["format"] not in ("rounds", "groups"):
                raise Err("Nieznany format turnieju.")
            if s["phase"] != "signup" and body["format"] != s.get("format", "rounds"):
                raise Err("Format turnieju można zmienić tylko przed losowaniem.")
            s["format"] = body["format"]
        if "groupSize" in body:
            v = int(body["groupSize"])
            if not 3 <= v <= 6:
                raise Err("Grupa może mieć od 3 do 6 graczy.")
            if s["phase"] != "signup" and v != (s.get("groupSize") or 4):
                raise Err("Wielkość grup można zmienić tylko przed losowaniem.")
            s["groupSize"] = v
        if "groupAdvance" in body:
            v = int(body["groupAdvance"])
            if v not in (1, 2):
                raise Err("Z grupy może awansować 1 lub 2 graczy.")
            if s["phase"] != "signup" and v != (s.get("groupAdvance") or 2):
                raise Err("Liczbę awansujących z grupy można zmienić tylko przed losowaniem.")
            s["groupAdvance"] = v
        if "scoreMode" in body:
            if body["scoreMode"] not in ("points", "sets"):
                raise Err("Nieznany tryb wpisywania wyników.")
            s["scoreMode"] = body["scoreMode"]
        if "deadline" in body:
            d = body["deadline"]
            if d:
                datetime.datetime.fromisoformat(str(d).replace("Z", "+00:00"))
            s["deadline"] = d or None
    elif act == "rename":
        p = next((p for p in s["players"] if p["id"] == body.get("id")), None)
        if not p:
            raise Err("Nie ma takiego zawodnika.")
        nm = clean_name(body.get("name"))
        if any(q["name"].lower() == nm.lower() and q["id"] != p["id"] for q in s["players"]):
            raise Err("Ktoś już gra pod tym imieniem.")
        p["name"] = nm
    elif act == "remove":
        if s["phase"] != "signup":
            raise Err("Zawodnika można usunąć tylko w trakcie zapisów.")
        s["players"] = [p for p in s["players"] if p["id"] != body.get("id")]
    elif act == "add":
        add_player(s, body.get("name"))
    elif act == "late":
        if s["phase"] not in ("groups",) + ROUND_KEYS:
            raise Err("Spóźnionego gracza można dopisać w trakcie rund lub grup.")
        others = [p["id"] for p in s["players"]]
        phase = s["phase"]
        s["phase"] = "signup"
        try:
            p, _ = add_player(s, body.get("name"))
        finally:
            s["phase"] = phase
        rng = random.Random()
        if phase == "groups":
            # the new player joins the smallest group and plays everyone in it
            g = sorted(s["groups"], key=lambda x: (len(x["ids"]), x["id"]))[0]
            rd_ = max([m.get("rd", 0) for m in s["gm"] if m["g"] == g["id"]] + [0])
            for o in g["ids"]:
                rd_ += 1
                s["gm"].append({"id": mid(), "a": p["id"], "b": o, "sa": None, "sb": None, "pa": None, "pb": None,
                                "sets": None, "half": False, "g": g["id"], "rd": rd_})
            g["ids"].append(p["id"])
        elif phase == "r1":
            spare = next((m for m in s["r1"] if m["half"] and m["sa"] is None), None)
            if spare:
                spare["b"], spare["half"] = p["id"], False
            else:
                s["r1"].append({"id": mid(), "a": p["id"], "b": rng.choice(others), "sa": None, "sb": None, "half": True, "pool": None})
        else:
            # joining in round k: k matches against random opponents, counted only for the late player
            k = int(phase[1:])
            for opp in rng.sample(others, min(k, len(others))):
                s[phase].append({"id": mid(), "a": p["id"], "b": opp, "sa": None, "sb": None, "half": True, "pool": "lose" if k == 2 else "w0"})
    elif act == "draw":
        if s["phase"] != "signup":
            raise Err("Losowanie już się odbyło.")
        draw(s)
    elif act == "reopen":
        s["phase"], s["sel"], s["ko"], s["ll"], s["llDirect"] = "signup", None, None, [], []
        for k in ROUND_KEYS:
            s[k] = []
        s["groups"], s["gm"] = [], []
        s["deadline"] = None
    elif act == "rollback":
        to = body.get("to")
        if to in ROUND_KEYS and s.get(to):
            idx = ROUND_KEYS.index(to)
            for k in ROUND_KEYS[idx + 1:]:
                s[k] = []
            s["phase"], s["sel"], s["ko"], s["ll"], s["llDirect"] = to, None, None, [], []
            if idx > 0:
                advance(s)
        elif to == "groups" and s.get("gm"):
            s["phase"], s["sel"], s["ko"], s["ll"], s["llDirect"] = "groups", None, None, [], []
            advance(s)
        elif to == "ll" and s.get("ll"):
            s["phase"], s["ko"] = "ll", None
            advance(s)
        else:
            raise Err("Nie można cofnąć do tego etapu.")
    elif act == "select":
        if s["phase"] != "qual":
            raise Err("Wybór awansujących jest możliwy po rundzie 2.")
        sel = body.get("sel") or []
        ids = {p["id"] for p in s["players"]}
        if len(set(sel)) != len(sel) or not set(sel) <= ids:
            raise Err("Błędna lista zawodników.")
        s["sel"] = sel
    elif act == "startko":
        if s["phase"] != "qual":
            raise Err("Drabinkę uruchamia się po rozstrzygnięciu awansu.")
        K = ko_size(s)
        sel = s.get("sel") or []
        if len(sel) != K:
            raise Err(f"Wybierz dokładnie {K} zawodników.")
        rank = [r["id"] for r in ranking(s)]
        build_ko(s, sorted(sel, key=rank.index))
    elif act == "reset":
        keep = body.get("keepPlayers")
        ns = new_state()
        for k in ("title", "edition", "prize", "setsToWin", "scoreMode", "rounds", "format", "groupSize", "groupAdvance", "koSize", "llSpots", "advancePct", "luckyLosers", "llMode"):
            ns[k] = s.get(k, ns[k])
        if keep:
            ns["players"] = s["players"]
        ns["rev"] = s.get("rev", 0)
        s.clear()
        s.update(ns)
    else:
        raise Err("Nieznana akcja.")


def clean_name(n):
    n = " ".join(str(n or "").split())[:28]
    if len(n) < 2:
        raise Err("Wpisz imię (co najmniej 2 znaki).")
    return n


def add_player(s, name):
    if s["phase"] != "signup":
        raise Err("Zapisy są już zamknięte.")
    if len(s["players"]) >= MAX_PLAYERS:
        raise Err("Lista jest pełna.")
    nm = clean_name(name)
    if any(p["name"].lower() == nm.lower() for p in s["players"]):
        raise Err("Ktoś już jest zapisany pod tym imieniem. Dodaj nazwisko lub inicjał.")
    tok = secrets.token_urlsafe(16)
    p = {"id": secrets.token_hex(4), "name": nm, "tok": h(tok), "at": now()}
    s["players"].append(p)
    return p, tok


def public(s, admin=False):
    out = {k: v for k, v in s.items() if k != "players"}
    out["players"] = [{"id": p["id"], "name": p["name"]} for p in s["players"]]
    rank = ranking(s) if (s["r1"] or s.get("gm")) else []
    out["ranking"] = rank
    K = ko_size(s) if len(s["players"]) >= 2 else 0
    out["K"] = K
    out.setdefault("ll", [])
    out.setdefault("llDirect", [])
    if len(s["players"]) >= 2:
        kp = knockout_plan(s)
        out["D"], out["L"], out["llMode"], out["llPool"] = kp["D"], kp["L"], kp["mode"], kp["pool"]
        out["recPct"] = recommended_pct(len(s["players"]))
    out["tie"] = boundary_tie(s, rank, K) if s["phase"] == "qual" else []
    out["groupTables"] = group_tables(s) if s.get("format") == "groups" else []
    out["D0"] = min(group_direct(s), K) if s.get("format") == "groups" and len(s["players"]) >= 2 else 0
    out["admin"] = admin
    out["serverTime"] = now()
    return out


# ---------- HTTP ----------

TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
         ".ico": "image/x-icon", ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8"}


class H(BaseHTTPRequestHandler):
    server_version = "tt"
    sys_version = ""

    def log_message(self, fmt, *a):
        pass

    def is_admin(self):
        t = self.headers.get("X-Admin", "")
        return bool(t) and hmac.compare_digest(t, admin_token())

    def send(self, code, obj=None, raw=None, ctype="application/json; charset=utf-8", cache="no-store", head=False):
        body = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self, head=False):
        path = self.path.split("?")[0]
        if path == "/api/state":
            with LOCK:
                s = load()
                if check_deadline(s):
                    save(s)
                return self.send(200, public(s, self.is_admin()), head=head)
        name = {"/": "index.html", "/index.html": "index.html", "/engine.js": "engine.js",
                "/demo-data.json": "demo-data.json", "/og.jpg": "og.jpg",
                "/apple-touch-icon.png": "apple-touch-icon.png", "/icon-512.png": "icon-512.png",
                "/favicon-32.png": "favicon-32.png"}.get(path)
        if not name:
            return self.send(404, {"error": "Nie znaleziono."}, head=head)
        fp = os.path.join(STATIC, name)
        with open(fp, "rb") as f:
            data = f.read()
        ext = os.path.splitext(name)[1]
        self.send(200, raw=data, ctype=TYPES.get(ext, "application/octet-stream"),
                  cache="no-cache" if ext == ".html" else "public, max-age=3600", head=head)

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > 8192:
            return self.send(413, {"error": "Za duże zapytanie."})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send(400, {"error": "Błędne dane."})
        admin = self.is_admin()
        try:
            with LOCK:
                s = load()
                check_deadline(s)
                extra = {}
                if path == "/api/join":
                    p, tok = add_player(s, body.get("name"))
                    extra = {"me": p["id"], "token": tok}
                elif path == "/api/leave":
                    if s["phase"] != "signup":
                        raise Err("Zapisy są zamknięte, wypisać może tylko organizator.")
                    p = next((p for p in s["players"] if p["id"] == body.get("id")), None)
                    if not p or not hmac.compare_digest(p["tok"], h(str(body.get("token", "")))):
                        raise Err("Nie możesz wypisać tej osoby.")
                    s["players"].remove(p)
                elif path == "/api/score":
                    sa, sb = body.get("sa"), body.get("sb")
                    set_score(s, body.get("stage"), body.get("id"), sa, sb, admin, body.get("pa"), body.get("pb"), body.get("sets"))
                elif path == "/api/start":
                    if not admin:
                        return self.send(403, {"error": "Brak uprawnień organizatora."})
                    start_match(s, body.get("stage"), body.get("id"), bool(body.get("cancel")))
                elif path == "/api/admin":
                    if not admin:
                        return self.send(403, {"error": "Brak uprawnień organizatora."})
                    admin_action(s, body)
                else:
                    return self.send(404, {"error": "Nie znaleziono."})
                save(s)
                out = public(s, admin)
                out.update(extra)
                return self.send(200, out)
        except Err as e:
            return self.send(400, {"error": str(e)})
        except (ValueError, TypeError):
            return self.send(400, {"error": "Błędne dane."})


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    tok = admin_token()
    if not os.path.exists(STATE_F):
        save(new_state())
    host = "localhost" if BIND in ("127.0.0.1", "0.0.0.0", "::") else BIND
    print(f"Tournament is running: http://{host}:{PORT}/")
    print(f"Organizer link (do not share): http://{host}:{PORT}/#admin-{tok}", flush=True)
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
