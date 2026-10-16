"""Full-tournament simulations on the logic in server/server.py.

Run: python3 -m unittest discover tests
"""
import math
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TT_DATA", tempfile.mkdtemp())
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import server as S  # noqa: E402


def play(s, rng, late_in=None, until=None):
    """Enters random results until the tournament reaches the podium.

    late_in: stage (r1/r2) in which a late player is added after the first result.
    """
    steps = 0
    late_done = False
    while s["phase"] != "done":
        if until and until(s):
            return
        steps += 1
        assert steps < 1000, "the tournament got stuck in a loop"
        ph = s["phase"]
        if late_in == ph and not late_done and any(m["sa"] is not None for m in s[ph]):
            S.admin_action(s, {"action": "late", "name": "Late Player"})
            late_done = True
        if ph == "ko":
            ms = [(m, "pre") for m in (s["ko"].get("pre") or [])] + [(m, "ko") for r in s["ko"]["rounds"] for m in r]
            if s["ko"]["third"]:
                ms.append((s["ko"]["third"], "third"))
        elif ph == "groups":
            ms = [(m, "g") for m in s["gm"]]
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
                self.assertGreaterEqual(min(played), 2, "everyone plays at least two matches")

    def test_bracket_is_direct_qualifiers_plus_lucky_losers(self):
        s = new_tournament(26, koSize=16, llSpots=4)  # explicit power-of-two bracket (API only)
        play(s, random.Random(1))
        K, D, L = S.ll_plan(s)
        self.assertEqual((K, D, L), (16, 12, 4))
        first_round = self.entrants(s)
        ll_winners = {S.winner(m, 1) for m in s["ll"]}
        self.assertEqual(first_round, set(s["llDirect"]) | ll_winners)

    def test_bracket_seeding_first_vs_last(self):
        s = new_tournament(16, koSize=8, llSpots=2)
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


    def test_match_timing_start_to_score(self):
        s = new_tournament(4)
        m = s["r1"][0]
        S.start_match(s, "r1", m["id"])
        self.assertIn("t0", m)
        self.assertNotIn("t1", m)
        S.set_score(s, "r1", m["id"], None, None, True, 11, 7)
        self.assertIn("t1", m, "entering the result stops the timer")
        with self.assertRaises(S.Err):
            S.start_match(s, "r1", m["id"], cancel=True)
        S.set_score(s, "r1", m["id"], None, None, True)
        self.assertNotIn("t0", m)
        self.assertNotIn("t1", m)

    def test_untimed_match_has_no_duration(self):
        s = new_tournament(4)
        m = s["r1"][0]
        S.set_score(s, "r1", m["id"], None, None, True, 11, 7)
        self.assertNotIn("t0", m)
        self.assertNotIn("t1", m)


    def test_multi_set_match_from_set_points(self):
        s = new_tournament(4, setsToWin=2)
        m = s["r1"][0]
        S.set_score(s, "r1", m["id"], None, None, True, None, None, [[11, 9], [5, 11], [11, 8]])
        self.assertEqual((m["sa"], m["sb"]), (2, 1))
        self.assertEqual((m["pa"], m["pb"]), (27, 28), "points are the sum of all sets")
        self.assertEqual(m["sets"], [[11, 9], [5, 11], [11, 8]])
        S.set_score(s, "r1", m["id"], None, None, True, None, None, [[11, 3], [12, 10]])
        self.assertEqual((m["sa"], m["sb"]), (2, 0))
        S.set_score(s, "r1", m["id"], None, None, True)
        self.assertIsNone(m["sets"])
        self.assertIsNone(m["sa"])

    def test_multi_set_validation(self):
        s = new_tournament(4, setsToWin=2)
        m = s["r1"][0]
        for bad in ([[11, 9]],                      # nobody has won yet
                    [[11, 9], [11, 3], [11, 5]],      # a set after the match was decided
                    [[11, 10], [11, 3]],              # a set needs a two-point lead
                    [[11, 9], [9, 11], [11, 9], [11, 9]]):  # more sets than possible
            with self.subTest(sets=bad):
                with self.assertRaises(S.Err):
                    S.set_score(s, "r1", m["id"], None, None, True, None, None, bad)
        with self.assertRaises(S.Err):  # single-set points are refused when playing to two sets
            S.set_score(s, "r1", m["id"], None, None, True, 11, 3)

    def test_sets_only_result_still_works(self):
        s = new_tournament(4, setsToWin=2)
        m = s["r1"][0]
        S.set_score(s, "r1", m["id"], 2, 1, True)
        self.assertEqual((m["sa"], m["sb"]), (2, 1))
        self.assertIsNone(m["sets"])

    def test_full_tournament_with_set_points(self):
        rng = random.Random(11)
        for n in (5, 9, 16):
            with self.subTest(players=n):
                s = new_tournament(n, setsToWin=2)
                steps = 0
                while s["phase"] != "done":
                    steps += 1
                    assert steps < 1000
                    ph = s["phase"]
                    ms = ([(m, "pre") for m in (s["ko"].get("pre") or [])] + [(m, "ko") for r in s["ko"]["rounds"] for m in r] + ([(s["ko"]["third"], "third")] if s["ko"]["third"] else [])) if ph == "ko" else [(m, ph) for m in s[ph]]
                    m, st = [(m, st) for m, st in ms if m["a"] and m["b"] and m["sa"] is None][0]
                    sets = [[11, rng.randint(0, 9)] if rng.random() < .5 else [rng.randint(0, 9), 11] for _ in range(2)]
                    if sets[0][0] > sets[0][1] and sets[1][0] < sets[1][1] or sets[0][0] < sets[0][1] and sets[1][0] > sets[1][1]:
                        sets.append([11, 6] if rng.random() < .5 else [6, 11])
                    S.set_score(s, st, m["id"], None, None, True, None, None, sets)
                self.assertEqual(s["phase"], "done")

    def test_score_mode_setting(self):
        s = new_tournament(4)
        self.assertEqual(s["scoreMode"], "points")
        S.admin_action(s, {"action": "settings", "scoreMode": "sets"})
        self.assertEqual(s["scoreMode"], "sets")
        with self.assertRaises(S.Err):
            S.admin_action(s, {"action": "settings", "scoreMode": "nonsense"})


    def test_bracket_without_lucky_losers(self):
        rng = random.Random(5)
        s = new_tournament(20, koSize=8, llSpots=0)
        self.assertEqual(S.ll_plan(s), (8, 8, 0), "8 places, all direct, no Lucky Losers")
        play(s, rng)
        self.assertEqual(s["ll"], [])
        self.assertEqual(len(s["ko"]["rounds"][0]), 4)

    def test_lucky_losers_spots_and_bracket_size_settings(self):
        s = new_tournament(40)
        S.admin_action(s, {"action": "settings", "koSize": 32, "llSpots": 0})
        self.assertEqual(S.ll_plan(s)[0], 32)
        S.admin_action(s, {"action": "settings", "koSize": 8, "llSpots": 3})
        self.assertEqual(S.ll_plan(s), (8, 5, 3))
        S.admin_action(s, {"action": "settings", "llSpots": "auto"})
        self.assertEqual(S.ll_plan(s), (8, 6, 2))
        with self.assertRaises(S.Err):
            S.admin_action(s, {"action": "settings", "llSpots": 65})
        with self.assertRaises(S.Err):
            S.admin_action(s, {"action": "settings", "koSize": 12})


    def test_organizer_picks_number_of_rounds(self):
        rng = random.Random(3)
        for rounds in (1, 2, 3, 4, 6):
            for n in (5, 8, 13):
                with self.subTest(rounds=rounds, players=n):
                    s = new_tournament(n, rounds=rounds)
                    play(s, rng)
                    self.assertEqual(s["phase"], "done")
                    self.assertGreaterEqual(min(x["played"] for x in S.stats(s).values()), rounds)

    def test_later_rounds_avoid_rematches(self):
        rng = random.Random(9)
        s = new_tournament(16, rounds=4)
        seen = set()
        for r in range(1, 5):
            self.assertEqual(s["phase"], f"r{r}")
            for m in s[f"r{r}"]:
                k = tuple(sorted((m["a"], m["b"])))
                self.assertNotIn(k, seen, f"rematch in round {r}")
                seen.add(k)
            for m in s[f"r{r}"]:
                S.set_score(s, f"r{r}", m["id"], None, None, True, *( (11, rng.randint(0, 9)) if rng.random() < .5 else (rng.randint(0, 9), 11) ))
        self.assertNotEqual(s["phase"], "r4")

    def test_group_format(self):
        rng = random.Random(21)
        for n, size, adv in [(6, 3, 2), (9, 3, 2), (12, 4, 2), (13, 4, 1), (20, 4, 2), (20, 5, 2), (26, 4, 2), (40, 5, 2), (64, 4, 2)]:
            with self.subTest(players=n, size=size, adv=adv):
                s = new_tournament(n, format="groups", groupSize=size, groupAdvance=adv)
                self.assertEqual(s["phase"], "groups")
                sizes = [len(g["ids"]) for g in s["groups"]]
                self.assertEqual(sum(sizes), n)
                self.assertLessEqual(max(sizes) - min(sizes), 1, "balanced groups")
                for g in s["groups"]:
                    self.assertEqual(len([m for m in s["gm"] if m["g"] == g["id"]]), len(g["ids"]) * (len(g["ids"]) - 1) // 2)
                K0 = S.ko_size(s)
                play(s, rng, until=lambda x: x["phase"] == "ko")  # the bracket has just been built
                grp = {i: g["id"] for g in s["groups"] for i in g["ids"]}
                known = [m for m in (s["ko"].get("pre") or []) + s["ko"]["rounds"][0] if m["a"] and m["b"]]
                self.assertLessEqual(sum(1 for m in known if grp[m["a"]] == grp[m["b"]]), 1,
                                     "same-group pairs known at the start are avoided")
                play(s, rng)
                self.assertEqual(s["phase"], "done")
                self.assertEqual(len(self.entrants(s)), K0, "full bracket")
                for r in S.ranking(s):
                    self.assertEqual(r["played"], len(next(g for g in s["groups"] if g["id"] == r["group"])["ids"]) - 1)

    def test_group_late_player_and_locked_settings(self):
        s = new_tournament(11, format="groups", groupSize=4, groupAdvance=2)
        before, small = len(s["gm"]), min(len(g["ids"]) for g in s["groups"])
        S.admin_action(s, {"action": "late", "name": "Late Player"})
        self.assertEqual(len(s["gm"]), before + small)
        play(s, random.Random(2))
        self.assertEqual(s["phase"], "done")
        with self.assertRaises(S.Err):
            S.admin_action(s, {"action": "settings", "format": "rounds"})
        with self.assertRaises(S.Err):
            S.admin_action(s, {"action": "settings", "rounds": 3})


    # ---- knockout stage: share of players advancing, Lucky Losers yes/no and how they are decided ----

    @staticmethod
    def entrants(s):
        ko = s["ko"]
        ids = {i for m in (ko.get("pre") or []) for i in (m["a"], m["b"])}
        ids |= {i for m in ko["rounds"][0] for i in (m["a"], m["b"])}
        ids.discard(None)
        return ids

    def test_share_of_players_advancing_is_even_and_rounded_up(self):
        rng = random.Random(14)
        for n in (3, 5, 8, 11, 16, 20, 21, 33, 40):
            for pct in (70, 60, 50, 45, 40, 35, 30, 25, 20):
                with self.subTest(players=n, pct=pct):
                    s = new_tournament(n, advancePct=pct, koSize=None)
                    raw = math.ceil(n * pct / 100 - 1e-9)
                    want = max(2, min(raw + raw % 2, n - n % 2))
                    pl = S.knockout_plan(s)
                    self.assertEqual((pl["D"], pl["L"], pl["M"]), (want, 0, want))
                    self.assertEqual(want % 2, 0)
                    play(s, rng)
                    self.assertEqual(s["phase"], "done")
                    ko = s["ko"]
                    matches = len(ko.get("pre") or []) + sum(len(r) for r in ko["rounds"])
                    self.assertEqual(matches, want - 1, "single elimination plays M-1 matches")
                    self.assertEqual(len(self.entrants(s)), want)
                    if ko.get("pre"):
                        order = [r["id"] for r in S.ranking(s)]
                        pre_players = {i for m in ko["pre"] for i in (m["a"], m["b"])}
                        byes = self.entrants(s) - pre_players
                        self.assertLess(max(order.index(i) for i in byes), min(order.index(i) for i in pre_players),
                                        "the best of the table get the byes")

    def test_recommendation_is_closest_to_half(self):
        self.assertEqual(S.recommended_pct(20), 50)
        self.assertEqual(S.knockout_plan(new_tournament(20))["D"], 10)
        self.assertEqual(S.recommended_l(16, "table"), 8)
        self.assertEqual(S.recommended_l(16, "playoff"), 4)

    def test_lucky_losers_yes_no_and_mode(self):
        rng = random.Random(8)
        for n in (12, 20, 26, 33):
            for mode in ("playoff", "table"):
                with self.subTest(players=n, mode=mode):
                    s = new_tournament(n, advancePct=40, koSize=None, luckyLosers=True, llMode=mode)
                    pl = S.knockout_plan(s)
                    self.assertGreaterEqual(pl["L"], 1)
                    self.assertEqual((pl["D"] + pl["L"]) % 2, 0)
                    self.assertEqual(pl["pool"], n - pl["D"])
                    L, D = pl["L"], pl["D"]
                    play(s, rng)
                    self.assertEqual(s["phase"], "done")
                    self.assertEqual(len(self.entrants(s)), D + L)
                    self.assertEqual(len(s["ll"]), 0 if mode == "table" else L)
                    # chosen number, and off means none
                    s2 = new_tournament(n, advancePct=40, koSize=None, luckyLosers=True, llMode=mode, llSpots=2)
                    self.assertEqual(S.knockout_plan(s2)["L"], 2)
                    s2["luckyLosers"] = False
                    self.assertEqual(S.knockout_plan(s2)["L"], 0)
        s = new_tournament(10)
        for bad in ({"advancePct": 3}, {"llMode": "nonsense"}, {"llSpots": 65}):
            with self.assertRaises(S.Err):
                S.admin_action(s, dict(bad, action="settings"))

    def test_groups_lucky_losers_come_from_the_third_places(self):
        for n, size, adv in ((20, 4, 2), (24, 4, 2), (26, 4, 2)):
            with self.subTest(players=n):
                s = new_tournament(n, format="groups", groupSize=size, groupAdvance=adv, koSize=None, luckyLosers=True, llMode="table")
                G = len(s["groups"])
                pl = S.knockout_plan(s)
                self.assertEqual(pl["D"], G * adv)
                self.assertEqual(pl["pool"], G)
                self.assertTrue(1 <= pl["L"] <= G and (pl["D"] + pl["L"]) % 2 == 0)
                play(s, random.Random(n))
                self.assertEqual(s["phase"], "done")

    def test_groups_with_an_odd_number_of_qualifiers_get_the_best_of_the_rest(self):
        s = new_tournament(15, format="groups", groupSize=5, groupAdvance=1, koSize=None)
        pl = S.knockout_plan(s)
        self.assertEqual(pl["D"], 3)
        self.assertEqual((pl["M"], pl["L"], pl["mode"]), (4, 1, "table"))
        play(s, random.Random(1))
        self.assertEqual(s["phase"], "done")


if __name__ == "__main__":
    unittest.main()
