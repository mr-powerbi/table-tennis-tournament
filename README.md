# 🏓 Table Tennis Tournament

A web app for running an office table-tennis tournament: sign-ups via a shared link, two qualifying rounds, a Lucky Losers round, a seeded knockout bracket, a podium with laurels, an overall table and stats.

**[▶ Open the demo](https://mr-powerbi-data.pl/table-tennis-tournament/)**: it runs in your browser, you are the organizer and you can play a whole tournament from sign-ups to the podium.

![Podium and bracket](docs/screenshots/podium-desktop.png)

> **Vibecoded.** The first working version was live about 20 minutes after the first prompt, built in a conversation with AI (Claude), and then went through a few quick iterations of fixes. It was made on the spot for a company team-building event, where close to 30 people signed up for a just-for-fun tournament. There was no design and no spec, only prompts in a chat. I dictated most of them by voice from my phone, and some features were added while the games were already being played. The process is described below.

The app's interface is in Polish, because that's who it was built for. The documentation and code structure are described here in English.

---

## Features

**For participants (regular link)**
- Sign yourself up, or several people from one phone. Your own entries can be withdrawn until the draw.
- Live view: the page refreshes itself every few seconds.
- Current stage, one combined standings table, bracket, podium, all results and a collapsible stats panel (right under the bracket).
- Organizer panel opens as a pop-up from a button at the top, so there is no scrolling to the bottom.
- Match timer: the organizer taps *Start* on a match, entering the result ends it. Stats then show average, longest and shortest match time and how deuce games affect length.
- Matches of the people signed up from your phone are highlighted.

**For the organizer (link with a secret token)**
- Score entry in points (e.g. 11:7, 12:10), validated against the rules: first to 11, win by two.
- Manual draw, or an automatic draw at a set time.
- Adding late players during rounds 1 and 2, so they still get their two matches.
- Rolling the tournament back to an earlier stage without losing earlier results.
- Settings: name, edition, prize, match format (1–3 sets), bracket size, number of Lucky Losers spots.

**Tournament format**
1. **Round 1.** Random pairs, everyone plays one match.
2. **Round 2.** Winners play winners, losers play losers. After this round everyone has played at least twice.
3. **Ranking.** Wins, then point difference, then points scored.
4. **Direct qualification** for the top of the ranking. Already during round 2 the page shows who is guaranteed a place and should stay around.
5. **Lucky Losers.** The best players outside direct qualification play one match (higher-ranked vs lower-ranked), and the winners fill the remaining bracket spots.
6. **Bracket.** Seeded 1st vs last, 2nd vs second-to-last and so on, plus a third-place match and the podium.

With an odd number of players, the player without a pair gets an extra match against a random opponent that only counts for them.

| Round 2 on a phone (dark mode) | Entering a score |
|---|---|
| ![Round 2 with the list of guaranteed qualifiers](docs/screenshots/round2-mobile-dark.png) | ![Score dialog](docs/screenshots/score-dialog-mobile.png) |

---

## How it was built

### Timeline (12 October 2026, from the chat log)

| Time | What happened |
|---|---|
| 20:03 | First prompt: bracket and groups, number of players and groups set in the app, brand colours. |
| 20:04–20:07 | Concept changed on the fly: no group stage, everyone plays two matches, losers get a second chance. A screenshot of a website as colour reference, and the decision to host the page on my own server under an address known only to participants. |
| 20:07–20:20 | More requirements: everyone signs up on their own, a podium with laurels at the end, only the organizer enters results. |
| **~20:24** | **First working version online**, about 20 minutes after the first prompt. |
| 20:25–20:33 | Hotfixes to keep the production version fully working and ready to share with participants: link preview and icon, several sign-ups from one phone, score entry in points. |
| 20:47 | The tournament is running and late players arrive, so adding players mid-round is added. |
| 21:10 | A list of players who are already guaranteed a bracket spot and should not leave. |
| 21:20–21:36 | Lucky Losers rules reworked during round 2, without losing any entered results. |
| 21:51–21:53 | Bracket seeding 1st vs last, a table of all results and an overall standings table. |
| 22:32 | Tournament stats at the end. |

**In short: about 20 minutes to the first version online, then a few iterations of fixes**, mostly during the tournament, once it was clear what was missing.

### Process
- **A conversation instead of a spec.** There was no design and no backlog. Requirements came in consecutive, often dictated messages and changed as we saw how the tournament was going.
- **AI wrote the code, I decided the rules.** For every change to the format (Lucky Losers, seeding, permissions) the decision was mine, and the implementation, deployment and testing were done by the AI.
- **Production changes during the event.** Before every fix the tournament state was backed up. New logic was first checked by simulating a full tournament on a copy of the real data and only then deployed. No entered result was lost.
- **Simple on purpose.** No frameworks, no database, no build step. One HTML file, one Python file and one JSON file with the state. For a few dozen people and one organizer that is plenty.

### What I learned
- For ad-hoc tools, time to the first working version matters most. Everything else gets added once people start using it.
- The tournament rules are the hardest part. Adapting the code is easy, deciding what you actually want while players are waiting is not.
- Simulation tests (a full tournament with random results for 3–40 players) gave confidence when changing things live.

---

## Architecture

```
docs/            frontend + GitHub Pages
  index.html     the whole interface (HTML, CSS and JS in one file)
  engine.js      in-browser tournament engine, used in demo mode
  demo-data.json a real tournament with anonymised names
server/
  server.py      HTTP server and tournament logic (Python, standard library only)
tests/           full-tournament simulations for both engines
```

- **Server mode** (how it ran at the event): `server.py` keeps the state in a single JSON file and exposes a small API (`api/state`, `api/join`, `api/score`, `api/admin`). Organizer rights come from a token in the `#admin-…` part of the link, which never reaches server logs or the Referer header.
- **Demo mode** (GitHub Pages): the same interface, but instead of the server it uses `engine.js`, a 1:1 port of the logic in `server.py`. The state is stored in the browser's `localStorage`.
- The interface picks the mode by itself: on `github.io`, from a local file or with `?demo` it runs the demo. Otherwise it connects to the server, and if no server API answers (e.g. GitHub Pages on a custom domain), it falls back to the demo.

## Running it yourself

```bash
python3 server/server.py
# Turniej działa: http://localhost:8187/
# Link organizatora (nie udostępniaj): http://localhost:8187/#admin-...
```

Only Python 3.10+ is required. Environment variables: `TT_PORT`, `TT_BIND`, `TT_DATA` (directory for the state and token) and `TT_STATIC`.

To put a tournament online, run the server behind a reverse proxy (e.g. nginx with HTTPS) under a hard-to-guess path. Give participants the regular link and keep the `#admin-…` link to yourself. If you want a nice link preview in messaging apps, change `og:url` and `og:image` in `docs/index.html` to your own address.

## Tests

```bash
python3 -m unittest discover tests   # server logic
node tests/engine.test.mjs            # browser engine
```

Both suites play full tournaments with random results for 3–40 players. They check that everyone plays at least twice, the bracket is full, the seeding is right and scores are validated.

## Privacy

The repository contains no real names, no company name and no address of the server the tournament ran on. The demo data is the real course of the tournament with made-up names.

## License

[MIT](LICENSE)
