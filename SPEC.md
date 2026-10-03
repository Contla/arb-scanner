# Arbitrage Scanner — Build Spec for Claude Code

|  |  |  |
| --- | --- | --- |
|  |  |  |
|  |  |  |

Oct 3, 2026 · @Luis Contla Ramirez

## How to use this doc with Claude Code

Export this doc as Markdown and save it in the empty repo as `SPEC.md`, then build in phases using the prompts in "Build phases" below, one prompt per session.

1. Create an empty folder, run `git init`, drop `SPEC.md` in it.
2. Paste the Phase 0 prompt. Claude Code reads the spec and scaffolds the project.
3. Commit after every phase that passes its "done when" check. Only then paste the next prompt.
4. If a scraper breaks later, open a new session and say: "Read SPEC.md section 7, the `<book>` scraper is broken, here is the error."

Phases are ordered so you can test the whole pipeline end to end with fake data before writing a single real scraper.

## Project overview and goals

A private tool that pulls sports odds from Mexican online sportsbooks every minute, finds arbitrage opportunities worth at least 2% and 50 MXN, and sends a Telegram alert.

Users: two people (me and one friend). Runs locally or on a cheap VPS. No public website, no logins, no payments.

What the tool must do:

- Collect odds from several books: start with 2–3 (e.g. Caliente, Codere, Stake), add more later (Bet365, Strendus, Playdoit, Betcris, etc.).
- Match the same event across books even when team names are written differently.
- Compute arbitrage for 2-way and 3-way markets and the exact stake per bet in pesos.
- Alert only when ROI ≥ 2% and profit ≥ 50 MXN at the configured bankroll.
- Track how long each opportunity lives. We care about arbs that last minutes to hours, not seconds.
- Never place bets automatically. A human verifies and places every bet.

Out of scope for v1: live (in-play) betting, auto-betting, a public UI, user accounts.

## Constraints and principles

Keep the code minimal and clean: small modules, no frameworks we do not need, no premature abstraction.

- **One scraper per book**, all sharing one base interface. Adding a book = adding one file + one config entry.
- **Polite polling**: each book is fetched at most once per 60 s, with 0–15 s random jitter. Never hammer a site.
- **Fail soft**: if one scraper breaks, log it and keep the others running. Alert on Telegram if a book returns zero events for 10 minutes.
- **Everything in decimal odds** internally. Convert American or fractional on the way in.
- **Pre-match only** for v1. Skip events starting in less than 10 minutes.
- **Secrets in `.env`**, settings in `config.yaml`. Nothing hard-coded.
- **Tests for the math and matching**, since those are the parts where a bug costs money.
- Prefer reading the site's internal JSON API (via network interception) over parsing HTML. It breaks far less often.

## Tech stack

Python 3.12 end to end, with SQLite as the only database.

| Piece | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 | Best scraping ecosystem |
| Browser automation | Playwright (async, Chromium headless) | Handles JS-rendered odds and lets us intercept XHR/JSON |
| Plain HTTP | httpx (async) | For books whose JSON API can be called directly |
| Database | SQLite via `sqlite3` or SQLModel | Zero setup, enough for 2 users |
| Fuzzy matching | rapidfuzz | Team-name matching across books |
| Scheduling | asyncio loop (or APScheduler) | One process, no cron needed |
| Alerts | Telegram Bot API via httpx | Free, instant, works on phone |
| Config | pydantic-settings + YAML | Typed settings, `.env` for secrets |
| Tests | pytest | Math and matching tests |
| Deploy (later) | Docker + a small VPS | Optional, runs fine on a laptop first |

Optional later: a tiny Flask or FastAPI page that lists current arbs from SQLite.

## Architecture and folder structure

The pipeline runs in a loop: scrape → normalize → match → detect → alert, with SQLite between every step.

1. **Scrapers** fetch raw odds from each book and return a list of `RawOdds`.
2. **Normalizer** converts odds to decimal, cleans team names, maps markets to our canonical names.
3. **Matcher** links each book's event to one canonical `Event` (same sport, teams, kickoff).
4. **Detector** finds the best price per outcome across books and checks for arbitrage.
5. **Tracker** records when an arb first appears, updates it, and closes it when it disappears.
6. **Notifier** sends a Telegram message for new arbs and an edit when the numbers change.

```
arb-scanner/
├── SPEC.md
├── config.yaml
├── .env.example
├── pyproject.toml
├── app/
│   ├── main.py            # entry point, starts the loop
│   ├── settings.py        # pydantic settings
│   ├── db.py              # SQLite schema + helpers
│   ├── models.py          # dataclasses: RawOdds, Event, Market, Arb
│   ├── scrapers/
│   │   ├── base.py        # BaseScraper interface
│   │   ├── fake.py        # fake book for testing
│   │   ├── caliente.py
│   │   ├── codere.py
│   │   └── stake.py
│   ├── normalize.py       # odds conversion, name cleaning, market mapping
│   ├── matching.py        # cross-book event matching
│   ├── arbitrage.py       # pure math, no I/O
│   ├── tracker.py         # arb lifecycle
│   └── notify.py          # Telegram
├── data/
│   └── team_aliases.yaml  # manual name overrides
└── tests/
    ├── test_arbitrage.py
    ├── test_matching.py
    └── test_normalize.py
```

## Data model

Five SQLite tables; only the latest odds per book/market/outcome are kept, plus a small history for debugging.

| Table | Key columns | Notes |
| --- | --- | --- |
| `books` | id, name, enabled, last\_ok\_at, last\_error | One row per sportsbook |
| `events` | id, sport, league, home, away, kickoff\_utc | Canonical event, one per real match |
| `book_events` | book\_id, book\_event\_id, event\_id, raw\_home, raw\_away, url | Links a book's listing to the canonical event |
| `odds` | event\_id, book\_id, market, line, outcome, decimal\_odds, fetched\_at | Upsert on (event, book, market, line, outcome) |
| `arbs` | id, event\_id, market, line, legs\_json, roi, first\_seen, last\_seen, closed\_at, telegram\_msg\_id | Lifecycle of each opportunity |

Canonical values (keep these exact strings):

- **sport**: `soccer`, `basketball`, `baseball`, `football`, `tennis`, `hockey`
- **market**: `1x2` (3-way), `moneyline` (2-way), `totals`, `spread`, `btts`
- **outcome**: `home`, `draw`, `away`, `over`, `under`, `yes`, `no`
- **line**: a float for totals/spreads (e.g. `2.5`, `-1.5`), `null` otherwise

`RawOdds` (what a scraper returns) has: book, book\_event\_id, sport, league, raw\_home, raw\_away, kickoff\_utc, raw\_market, raw\_line, raw\_outcome, odds\_value, odds\_format, url.

## Scraper design

Every scraper implements the same interface and tries the internal JSON API first, falling back to HTML parsing only if there is none.

```python
class BaseScraper(ABC):
    name: str                      # "caliente"
    interval_s: int = 60

    @abstractmethod
    async def fetch(self) -> list[RawOdds]: ...
```

**Step 1 — discovery (done once per book, by hand with Claude Code):**

1. Open the book's pre-match soccer page with Playwright in headed mode.
2. Log every network response whose content-type is JSON. Save them to `debug/<book>/`.
3. Find the response(s) that contain event names and odds. Note the URL pattern, query params and headers.
4. Decide: can we call that URL directly with httpx (fastest), or must we load the page and intercept it (more robust to tokens/cookies)?

**Step 2 — the scraper:**

- Reuse one Playwright browser per process; one context per book; close pages after each fetch.
- Realistic user agent, `es-MX` locale, `America/Mexico_City` timezone.
- Block images, fonts and media to save bandwidth.
- Timeout 30 s per fetch. On failure: exponential backoff (1, 2, 4, 8 min), then keep trying at 8 min.
- Start with one sport (soccer) and three markets: `1x2`, `totals`, `moneyline` where available.
- Save one raw JSON sample per book in `tests/fixtures/` so parsing can be tested offline.

**Step 3 — fake book:** `fake.py` returns hard-coded odds that contain a known 3% arb, so the whole pipeline can be tested without touching any real site.

**Respectful limits:** never more than one request burst per book per minute, no parallel page loads to the same book, and stop that book if it returns 403/429 three times in a row.

## Normalization and event matching

A wrong match creates a fake arb, so matching must be strict: same sport, kickoff within 15 minutes, and both team names scoring ≥ 88 on rapidfuzz.

**Odds conversion** (into decimal):

- American +150 → 1 + 150/100 = 2.50; American −200 → 1 + 100/200 = 1.50
- Fractional 3/2 → 1 + 3/2 = 2.50
- Reject anything below 1.01 or above 1000.

**Name cleaning** (before fuzzy matching): lowercase, strip accents, remove words like `fc`, `cf`, `club`, `deportivo`, `cd`, `sc`, `de`, `the`, remove punctuation, collapse spaces. Example: "Club de Fútbol Monterrey" → "monterrey".

**Alias file** `data/team_aliases.yaml` overrides fuzzy matching for known cases (e.g. `"america": ["club america", "aguilas del america"]`). Check aliases first, fuzzy second.

**Matching rules:**

1. Same sport (and same league when both books give one).
2. |kickoff A − kickoff B| ≤ 15 min (all times stored in UTC).
3. Home and away both match. Also try swapped home/away and flag it, since some books list them reversed.
4. Ambiguous match (two candidates pass) → skip and log it, never guess.
5. Unmatched events are logged to `unmatched.log` daily so we can add aliases.

**Market mapping:** each scraper maps its own market labels to canonical ones (e.g. "Resultado final" → `1x2`, "Total de goles" → `totals`). Unknown markets are ignored, not guessed. Totals and spreads only compare when the `line` is identical (2.5 vs 2.5, never 2.5 vs 2.25).

## Arbitrage math

An arb exists when the sum of 1/odds of the best price for every outcome is below 1; alert only if ROI ≥ 2% and the guaranteed profit at our bankroll is ≥ 50 MXN after rounding stakes to whole pesos.

For each event + market + line, take the best decimal odds per outcome across all books (each outcome may come from a different book; skip if any outcome is missing).

```latex
S = \sum_{i} \frac{1}{o_i} \qquad \text{arb if } S < 1 \qquad ROI = \frac{1}{S} - 1
```

```latex
\text{stake}_i = B \cdot \frac{1/o_i}{S} \qquad \text{profit} = \min_i(\text{stake}_i \cdot o_i) - \sum_i \text{stake}_i
```

B = total bankroll for that arb (from `config.yaml`, e.g. 2,000 MXN).

**Worked example (1x2, B = 2,000 MXN):**

| Outcome | Book | Odds | 1/odds | Stake (MXN) | Payout (MXN) |
| --- | --- | --- | --- | --- | --- |
| Home | Caliente | 2.60 | 0.3846 | 804 | 2,090.40 |
| Draw | Codere | 3.60 | 0.2778 | 581 | 2,091.60 |
| Away | Stake | 3.40 | 0.2941 | 615 | 2,091.00 |
| **Total** |  |  | **0.9565** | **2,000** | min **2,090.40** |

S = 0.9565 → ROI ≈ 4.55%, guaranteed profit ≈ 90 MXN. This passes both filters.

**Rules for `arbitrage.py`:**

- Pure functions only, no database or network calls. Easy to test.
- Round stakes to whole pesos, then recompute the real profit with rounded stakes. Filter on that number.
- Note for the user: at exactly 2% ROI you need 2,500 MXN total stake to make 50 MXN. If bankroll is smaller, the 50 MXN filter will hide most 2% arbs.
- No upper ROI cap: 10%, 25% or more still alerts. Above 15%, tag the alert ⚠️ VERIFICAR, since big arbs are more often a wrong match or a palpable price, and also log it as `suspicious`.
- Support 2-way (moneyline, totals, btts, spread) and 3-way (1x2). Never mix lines.
- Casino commission: each book has an optional `fee_pct` in config.yaml (cut taken from winnings). Use net odds = 1 + (o − 1) × (1 − fee\_pct) in every calculation, so the 2% minimum and the 50 MXN profit are checked after commission, not before.

**Tests that must pass:** the worked example above; a 2-way 2.10 / 2.10 case (ROI 5%); a no-arb case (S = 1.05); missing outcome → no arb; rounding that drops profit below 50 → no alert.

## Notifications

One Telegram message per arb: sent when it appears, edited in place when odds change, and marked closed when it disappears — no spam.

Setup: create a bot with @BotFather, put `TELEGRAM_BOT_TOKEN` in `.env`, add both users to one private group, put its `TELEGRAM_CHAT_ID` in `.env`.

Message format (plain text, Spanish is fine):

```
🟢 ARB 4.55% · +90 MXN (bankroll 2,000)
⚽ Liga MX · Monterrey vs América · sáb 19:00
Mercado: 1X2

Local   2.60 @ Caliente → apostar 804
Empate  3.60 @ Codere   → apostar 581
Visita  3.40 @ Stake    → apostar 615

Visto hace 12 min · actualizado 19:02
```

Rules:

- Include a direct link to the event on each book when the scraper has it.
- Dedupe key = event + market + line + set of books. Same key → edit the existing message.
- Re-alert as a new message only if ROI rises by ≥ 1 point.
- When the arb closes, edit the message to show ❌ CERRADA and how long it lasted.
- Health alerts: a book failing for 10 min, or zero arbs found in 24 h (sanity check).
- Quiet hours optional in config (e.g. 01:00–07:00, queue and send a digest).

## Scheduler and config

A single async process runs every scraper on its own 60 s timer with jitter, and runs detection right after each scrape finishes.

Loop per book: `fetch()` → save odds → run normalize + match + detect for affected events → tracker → notify → sleep `interval_s + random(0, 15)`.

An arb is closed when it has not been seen for 3 consecutive cycles (avoids flicker when one fetch fails).

`config.yaml`:

```yaml
bankroll_mxn: 2000
min_roi: 0.02
min_profit_mxn: 50
verify_roi_above: 0.15   # still alerts, tagged VERIFICAR
min_minutes_to_kickoff: 10
match:
  kickoff_tolerance_min: 15
  name_score_min: 88
sports: [soccer]
markets: [1x2, moneyline, totals]
books:   # optional per book: fee_pct: 0.0 (commission on winnings)
  fake:     { enabled: false, interval_s: 60 }
  caliente: { enabled: true,  interval_s: 60 }
  codere:   { enabled: true,  interval_s: 60 }
  stake:    { enabled: true,  interval_s: 60 }
quiet_hours: null   # e.g. ["01:00", "07:00"]
```

`.env.example`:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
PROXY_URL=          # optional, only if a book blocks the home IP
```

## Build phases with ready-to-paste prompts

Eight phases; paste one prompt per Claude Code session, and do not move on until its "done when" check passes.

**Phase 0 — Scaffold**

```
Read SPEC.md fully. Create the project skeleton from section "Architecture and folder structure": pyproject.toml (Python 3.12, playwright, httpx, rapidfuzz, pydantic-settings, pyyaml, pytest), config.yaml and .env.example from section "Scheduler and config", settings.py that loads both, and models.py with the dataclasses from "Data model". Keep it minimal. No scraping yet. Ask me before adding any dependency not listed.
```

Done when: `pip install -e .` works and `python -m app.main` starts and exits cleanly.

**Phase 1 — Arbitrage math + tests**

```
Implement app/arbitrage.py exactly as described in SPEC.md section "Arbitrage math". Pure functions only. Write tests/test_arbitrage.py covering every case listed under "Tests that must pass". Run pytest and fix until green.
```

Done when: all arbitrage tests pass.

**Phase 2 — Database + fake book + pipeline**

```
Implement db.py (schema from "Data model"), scrapers/base.py, scrapers/fake.py (two fake books whose odds contain the worked-example arb), normalize.py, matching.py, tracker.py, and the loop in main.py. Print detected arbs to the console instead of Telegram. Write tests for normalize and matching, including accented names, swapped home/away, and an ambiguous match that must be skipped.
```

Done when: running with only `fake` enabled prints the 4.55% arb once, and it closes when you change the fake odds.

**Phase 3 — Telegram**

```
Implement notify.py following SPEC.md "Notifications": send, edit-in-place on changes, close message, health alerts. Wire it into the tracker. Use the fake book to test. Add a --dry-run flag that prints instead of sending.
```

Done when: the fake arb arrives in the Telegram group and is edited when odds change.

**Phase 4 — First real book (discovery)**

```
We are adding Caliente. Follow SPEC.md "Scraper design" step 1: write a throwaway script tools/discover.py that opens the pre-match soccer page in headed Playwright, logs every JSON response URL with a short preview, and saves the bodies to debug/caliente/. Then help me identify which response holds events and odds.
```

Then:

```
Using the response we found, implement scrapers/caliente.py per "Scraper design" step 2. Save one real response to tests/fixtures/caliente.json and write a parser test against it. Map Caliente market labels to canonical markets.
```

Done when: the parser test passes and a live run stores Caliente odds in SQLite.

**Phase 5 — Second and third books**

Repeat Phase 4 for Codere, then Stake. Then:

```
Run all three real books for 30 minutes. Show me: events per book, how many matched across books, the top 20 lines of unmatched.log, and any arbs (including ones below threshold). Suggest aliases for team_aliases.yaml.
```

Done when: most Liga MX and top European league matches are matched across all three books.

**Phase 6 — Hardening**

```
Add: per-book backoff and the 403/429 stop rule, the "book silent for 10 min" health alert, the verify_roi_above tag (still alerts) with a suspicious log, per-book fee_pct applied as net odds, structured logging to logs/app.log with daily rotation, and a Dockerfile. Keep changes small.
```

Done when: killing the network for 5 minutes recovers without a restart.

**Phase 7 (optional) — Small dashboard**

```
Add a minimal read-only web page (FastAPI or Flask, one file) on localhost that lists open arbs, closed arbs from the last 24 h with their duration, and the health of each book. No auth, bind to 127.0.0.1 only.
```

## Practical risks before placing bets

The math is guaranteed only if every leg gets placed at the alerted price; most real losses come from the gaps below, not from the code.

- **Price moves while you bet.** Place the leg with the shortest-lived or highest odds first, re-check the others, and skip if the arb is gone.
- **Max stake limits.** A book may cap you below the stake the tool suggests, leaving you unhedged. Check the limit before placing the first leg.
- **Palpable errors / voided bets.** Books can void bets at obviously wrong prices, leaving the other legs exposed. That is why arbs above 15% are tagged to verify before betting.
- **Rule differences.** Check that each book settles the market the same way (extra time, abandoned matches, player not starting).
- **Account limiting.** Books often restrict accounts that only bet arbs. Spreading bets and rounding stakes to normal-looking amounts helps a bit.
- **Balances.** You need money pre-loaded on every book; withdrawals can take days.

Start with a small bankroll for the first few weeks and log every real bet (placed odds, stake, result) to see how often arbs actually survive until you place them.
