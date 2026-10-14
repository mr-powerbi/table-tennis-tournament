"""Symulacje pełnych turniejów na logice z server/server.py.

Uruchom: python3 -m unittest discover tests
"""
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TT_DATA", tempfile.mkdtemp())
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import server as S  # noqa: E402


def play(s, rng, late_in=None):
    """Wpisuje losowe wyniki, aż turniej dojdzie do podium.

    late_in: nazwa etapu (r1/r2), w którym dopisać spóźnionego gracza po pierwszym wyniku.
    """
    steps = 0
    late_done = False
    while s["phase"] != "done":
        steps += 1
        assert steps < 1000, "turniej się zapętlił"
        ph = s["phase"]
        if late_in == ph and not late_done and any(m["sa"] is not None for m in s[ph]):
            S.admin_action(s, {"action": "late", "name": "Spóźniony"})
            late_done = True
        if ph == "ko":
            ms = [(m, "ko") for r in s["ko"]["rounds"] for m in r]
            if s["ko"]["third"]:
                ms.append((s["ko"]["third"], "third"))
        else:
            ms = [(m, ph) for m in s[ph]]
        todo = [(m, st) for m, st in ms if m["a"] and m["b"] and m["sa"] is None]
        m, st = todo[0]
        lose = rng.randint(0, 9)
        pa, pb = (11, lose) if rng.random() < 0.5 else (lose, 11)
        S.set_score(s, st, m["id"], None, None, True, pa, pb)


def new_tournament(n, **settings):
    s = S.new_state()
    s.update(settings)
    for i in range(n):
        S.add_player(s, f"Gracz {i:02d}")
    S.draw(s)
    return s


class TournamentTest(unittest.TestCase):
    def test_full_tournaments_reach_podium(self):
        rng = random.Random(7)
        for n in range(3, 41):
            with self.subTest(players=n):
                s = new_tournament(n)
                play(s, rng)
                self.assertEqual(s["phase"], "done")
                played = [x["played"] for x in S.stats(s).values()]
                self.assertGreaterEqual(min(played), 2, "każdy gra co najmniej dwa mecze")

    def test_bracket_is_direct_qualifiers_plus_lucky_losers(self):
        s = new_tournament(26)
        play(s, random.Random(1))
        K, D, L = S.ll_plan(s)
        self.assertEqual((K, D, L), (16, 12, 4))
        first_round = {p for m in s["ko"]["rounds"][0] for p in (m["a"], m["b"])}
        ll_winners = {S.winner(m, 1) for m in s["ll"]}
        self.assertEqual(first_round, set(s["llDirect"]) | ll_winners)

    def test_bracket_seeding_first_vs_last(self):
        s = new_tournament(16, llSpots=2)
        play(s, random.Random(3))
        rank = [r["id"] for r in S.ranking(s)]
        ll_w = sorted((S.winner(m, 1) for m in s["ll"]), key=rank.index)
        order = s["llDirect"] + ll_w
        r0 = s["ko"]["rounds"][0]
        for i, m in enumerate(r0):
            self.assertEqual((m["a"], m["b"]), (order[i], order[-1 - i]))

    def test_late_players_still_play_twice(self):
        for late in ("r1", "r2"):
            with self.subTest(late=late):
                s = new_tournament(11)
                play(s, random.Random(5), late_in=late)
                self.assertEqual(len(s["players"]), 12)
                self.assertGreaterEqual(min(x["played"] for x in S.stats(s).values()), 2)

    def test_point_validation(self):
        s = new_tournament(4)
        m = s["r1"][0]
        for pa, pb in ((11, 10), (10, 8), (5, 5)):
            with self.assertRaises(S.Err):
                S.set_score(s, "r1", m["id"], None, None, True, pa, pb)
        S.set_score(s, "r1", m["id"], None, None, True, 12, 10)
        self.assertEqual((m["sa"], m["sb"]), (1, 0))

    def test_only_organizer_enters_scores(self):
        s = new_tournament(4)
        with self.assertRaises(S.Err):
            S.set_score(s, "r1", s["r1"][0]["id"], None, None, False, 11, 3)


if __name__ == "__main__":
    unittest.main()
