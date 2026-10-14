#!/usr/bin/env python3
"""Turniej tenisa stołowego: mały serwer HTTP (tylko biblioteka standardowa), stan w jednym pliku JSON.

Uruchomienie:  python3 server/server.py
Zmienne środowiskowe: TT_DATA (katalog danych), TT_STATIC (katalog z frontendem, domyślnie ../docs),
TT_BIND (adres, domyślnie 127.0.0.1), TT_PORT (port, domyślnie 8187).
Przy starcie serwer wypisuje link organizatora z tokenem w części #admin-…
"""
import hashlib, hmac, json, os, random, secrets, threading, datetime
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
    return {"title": DEFAULT_TITLE, "edition": "Edycja 2026", "prize": "", "setsToWin": 1,
            "koSize": None, "llSpots": None, "deadline": None, "phase": "signup", "players": [],
            "r1": [], "r2": [], "ll": [], "llDirect": [], "sel": None, "ko": None, "rev": 0, "updated": now()}


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


# ---------- logika turnieju ----------

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


def pair(ids, rng, everyone, pool=None):
    ids = list(ids)
    rng.shuffle(ids)
    out = []
    while len(ids) >= 2:
        out.append({"id": mid(), "a": ids.pop(), "b": ids.pop(), "sa": None, "sb": None, "half": False, "pool": pool})
    if ids:
        x = ids.pop()
        others = [p for p in everyone if p != x]
        if others:
            opp = rng.choice(others)
            out.append({"id": mid(), "a": x, "b": opp, "sa": None, "sb": None, "half": True, "pool": pool})
    return out


def stats(s):
    T = s["setsToWin"]
    st = {p["id"]: {"w": 0, "l": 0, "sw": 0, "sl": 0, "pw": 0, "pl": 0, "played": 0} for p in s["players"]}
    for m in s["r1"] + s["r2"]:
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


def ranking(s):
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


def ko_size(s):
    n = len(s["players"])
    k = s.get("koSize") or default_k(n)
    return min(k, pow2_floor(n))


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
    K = len(ordered)
    r0 = []
    for i in range(K // 2):
        r0.append({"id": mid(), "a": ordered[i], "b": ordered[K - 1 - i], "sa": None, "sb": None})
    rounds = [r0]
    n = K // 2
    while n > 1:
        n //= 2
        rounds.append([{"id": mid(), "a": None, "b": None, "sa": None, "sb": None} for _ in range(n)])
    third = {"id": mid(), "a": None, "b": None, "sa": None, "sb": None} if K >= 4 else None
    s["ko"] = {"rounds": rounds, "third": third}
    s["phase"] = "ko"
    propagate(s)


def setp(m, a, b):
    if m["a"] != a or m["b"] != b:
        m["a"], m["b"], m["sa"], m["sb"], m["pa"], m["pb"] = a, b, None, None, None, None


def propagate(s):
    T = s["setsToWin"]
    ko = s["ko"]
    R = ko["rounds"]
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


def r1_result(s, pid):
    T = s["setsToWin"]
    for m in s["r1"]:
        if m["a"] == pid or (m["b"] == pid and not m["half"]):
            return winner(m, T) == pid
    return False


def advance(s):
    T = s["setsToWin"]
    if s["phase"] == "r1" and s["r1"] and all_done(s["r1"], T):
        rng = random.Random()
        ids = [p["id"] for p in s["players"]]
        win = [i for i in ids if r1_result(s, i)]
        lose = [i for i in ids if not r1_result(s, i)]
        s["r2"] = pair(win, rng, win if len(win) > 1 else ids, "win") + pair(lose, rng, lose if len(lose) > 1 else ids, "lose")
        s["phase"] = "r2"
    if s["phase"] == "r2" and all_done(s["r2"], T):
        start_ll(s)
    if s["phase"] == "ll" and all_done(s.get("ll", []), T):
        rank = [r["id"] for r in ranking(s)]
        winners = sorted((winner(m, T) for m in s["ll"]), key=rank.index)
        build_ko(s, list(s.get("llDirect", [])) + winners)


def ll_plan(s):
    N = len(s["players"])
    K = ko_size(s)
    L = s.get("llSpots") or max(1, K // 4)
    L = max(0, min(L, K, N - K))
    return K, K - L, L


def start_ll(s):
    rank = [r["id"] for r in ranking(s)]
    K, D, L = ll_plan(s)
    s["llDirect"] = rank[:D]
    pool = rank[D:D + 2 * L]
    s["ll"] = [{"id": mid(), "a": pool[i], "b": pool[2 * L - 1 - i], "sa": None, "sb": None, "half": False, "pool": "ll"} for i in range(L)]
    s["phase"] = "ll"
    if not s["ll"]:
        build_ko(s, rank[:K])


def draw(s):
    if len(s["players"]) < 3:
        raise Err("Do losowania potrzeba co najmniej 3 zawodników.")
    ids = [p["id"] for p in s["players"]]
    s["r1"] = pair(ids, random.Random(), ids)
    s["r2"], s["sel"], s["ko"], s["ll"], s["llDirect"] = [], None, None, [], []
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
    if stage in ("r1", "r2", "ll"):
        pool = s.get(stage, [])
    elif stage == "ko" and s["ko"]:
        pool = [m for r in s["ko"]["rounds"] for m in r]
    elif stage == "third" and s["ko"] and s["ko"]["third"]:
        pool = [s["ko"]["third"]]
    else:
        pool = []
    for m in pool:
        if m["id"] == m_id:
            return m
    raise Err("Nie znaleziono meczu.")


STAGE_PHASE = {"r1": ("r1",), "r2": ("r2",), "ll": ("ll",), "ko": ("ko", "done"), "third": ("ko", "done")}


def points_ok(pa, pb):
    if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 99 for v in (pa, pb)):
        return False
    return max(pa, pb) >= 11 and abs(pa - pb) >= 2


def set_score(s, stage, m_id, sa, sb, admin, pa=None, pb=None):
    T = s["setsToWin"]
    if pa is not None or pb is not None:
        if not points_ok(pa, pb):
            raise Err("Wpisz punkty seta: zwycięzca ma co najmniej 11 i 2 punkty przewagi (np. 11:7, 12:10).")
        if T != 1:
            raise Err("Punkty wpisuje się przy meczu do jednego seta.")
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
    if stage in ("r1", "r2", "ll") and s["phase"] not in STAGE_PHASE[stage]:
        old_w = winner(m, T)
        new_w = None if clearing else (m["a"] if sa > sb else m["b"])
        if old_w != new_w:
            raise Err("Ta zmiana zmienia zwycięzcę zamkniętej rundy. Najpierw cofnij turniej do tej rundy w panelu organizatora.")
    m["sa"], m["sb"] = (None, None) if clearing else (sa, sb)
    m["pa"], m["pb"] = (None, None) if clearing else (pa, pb)
    if stage in ("ko", "third"):
        propagate(s)
    else:
        advance(s)


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
        if "koSize" in body:
            v = body["koSize"]
            if v in (None, "", 0, "auto"):
                if s["phase"] in ("ko", "done") and s.get("koSize"):
                    raise Err("Drabinka już ruszyła. Cofnij turniej, aby zmienić jej rozmiar.")
                s["koSize"] = None
            else:
                v = int(v)
                if v not in (2, 4, 8, 16, 32):
                    raise Err("Drabinka może mieć 2, 4, 8, 16 lub 32 miejsca.")
                if s["phase"] in ("ko", "done") and v != s.get("koSize"):
                    raise Err("Drabinka już ruszyła. Cofnij turniej, aby zmienić jej rozmiar.")
                s["koSize"] = v
        if "llSpots" in body:
            v = body["llSpots"]
            v = None if v in (None, "", 0, "auto") else int(v)
            if v is not None and not (1 <= v <= 16):
                raise Err("Miejsc dla Lucky Losers może być od 1 do 16.")
            if s["phase"] in ("ko", "done"):
                raise Err("Drabinka już ruszyła. Cofnij do Lucky Losers lub rundy 2, aby to zmienić.")
            if s["phase"] == "ll" and any(m["sa"] is not None for m in s.get("ll", [])):
                raise Err("W Lucky Losers są już wyniki. Cofnij do rundy 2, aby zmienić liczbę miejsc.")
            s["llSpots"] = v
            if s["phase"] == "ll":
                start_ll(s)
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
        if s["phase"] not in ("r1", "r2"):
            raise Err("Spóźnionego gracza można dopisać w rundzie 1 lub 2.")
        others = [p["id"] for p in s["players"]]
        phase = s["phase"]
        s["phase"] = "signup"
        try:
            p, _ = add_player(s, body.get("name"))
        finally:
            s["phase"] = phase
        rng = random.Random()
        if phase == "r1":
            spare = next((m for m in s["r1"] if m["half"] and m["sa"] is None), None)
            if spare:
                spare["b"], spare["half"] = p["id"], False
            else:
                s["r1"].append({"id": mid(), "a": p["id"], "b": rng.choice(others), "sa": None, "sb": None, "half": True, "pool": None})
        else:
            for opp in rng.sample(others, 2):
                s["r2"].append({"id": mid(), "a": p["id"], "b": opp, "sa": None, "sb": None, "half": True, "pool": "lose"})
    elif act == "draw":
        if s["phase"] != "signup":
            raise Err("Losowanie już się odbyło.")
        draw(s)
    elif act == "reopen":
        s["phase"], s["r1"], s["r2"], s["sel"], s["ko"], s["ll"], s["llDirect"] = "signup", [], [], None, None, [], []
        s["deadline"] = None
    elif act == "rollback":
        to = body.get("to")
        if to == "r1" and s["r1"]:
            s["phase"], s["r2"], s["sel"], s["ko"], s["ll"], s["llDirect"] = "r1", [], None, None, [], []
        elif to == "r2" and s["r2"]:
            s["phase"], s["sel"], s["ko"], s["ll"], s["llDirect"] = "r2", None, None, [], []
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
        for k in ("title", "edition", "prize", "setsToWin", "koSize", "llSpots"):
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
    rank = ranking(s) if s["r1"] else []
    out["ranking"] = rank
    K = ko_size(s) if len(s["players"]) >= 2 else 0
    out["K"] = K
    out.setdefault("ll", [])
    out.setdefault("llDirect", [])
    if len(s["players"]) >= 2:
        _, out["D"], out["L"] = ll_plan(s)
    out["tie"] = boundary_tie(s, rank, K) if s["phase"] == "qual" else []
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
                    set_score(s, body.get("stage"), body.get("id"), sa, sb, admin, body.get("pa"), body.get("pb"))
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
    print(f"Turniej działa: http://{host}:{PORT}/")
    print(f"Link organizatora (nie udostępniaj): http://{host}:{PORT}/#admin-{tok}", flush=True)
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
