"""Entry point: one polling loop per book. Arbs are printed to the console until Telegram is added."""

import asyncio
import logging
import random
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

from app import arbitrage, db, matching, normalize, tracker
from app.models import Arb, RawOdds
from app.scrapers import fake
from app.scrapers.base import BaseScraper
from app.settings import ROOT, Settings

log = logging.getLogger(__name__)

DB_PATH = ROOT / "data" / "arb.db"
ALIASES_PATH = ROOT / "data" / "team_aliases.yaml"
LOG_DIR = ROOT / "logs"
FETCH_TIMEOUT_S = 30
JITTER_S = 15

# config.yaml book name -> the scrapers it runs
SCRAPERS: dict[str, Callable[[], list[BaseScraper]]] = {
    "fake": fake.make_scrapers,
}


def build_scrapers(settings: Settings) -> tuple[list[BaseScraper], dict[str, float]]:
    """Scrapers for the enabled books, and each scraper's fee_pct."""
    scrapers, fees = [], {}
    for name, book in settings.books.items():
        if not book.enabled:
            continue
        if name not in SCRAPERS:
            log.warning("%s is enabled but has no scraper yet, skipping it", name)
            continue
        for scraper in SCRAPERS[name]():
            scraper.interval_s = book.interval_s
            fees[scraper.name] = book.fee_pct
            scrapers.append(scraper)
    return scrapers, fees


def ingest(
    conn: sqlite3.Connection,
    settings: Settings,
    aliases: matching.Aliases,
    scraper: BaseScraper,
    book_id: int,
    raws: list[RawOdds],
    now: datetime,
) -> None:
    """Normalize one fetch, link its listings to canonical events, and store it as the book's odds."""
    soonest = now + timedelta(minutes=settings.min_minutes_to_kickoff)
    listings: dict[str, tuple[RawOdds, list[normalize.Quote]]] = {}
    skipped = 0
    for raw in raws:
        if raw.sport not in settings.sports or raw.kickoff_utc <= soonest:
            continue
        quote = normalize.to_quote(raw, scraper.markets, scraper.outcomes)
        if quote is None or quote.market not in settings.markets:
            skipped += 1
            continue
        listings.setdefault(raw.book_event_id, (raw, []))[1].append(quote)

    prices = {}
    for raw, quotes in listings.values():
        link = matching.link_listing(conn, book_id, raw, aliases, settings.match)
        if link is None:
            continue
        event_id, swapped = link
        for quote in quotes:
            if swapped:
                quote = normalize.swap_sides(quote)
            prices[(event_id, quote.market, quote.line, quote.outcome)] = quote.decimal_odds
    db.replace_odds(conn, book_id, prices, now)
    log.info("%s: %d prices on %d events, %d skipped", scraper.name, len(prices), len(listings), skipped)


def detect(conn: sqlite3.Connection, settings: Settings, fees: dict[str, float], now: datetime) -> list[Arb]:
    """Arbs worth alerting on, across every upcoming event."""
    found = []
    for market in db.load_markets(conn, now + timedelta(minutes=settings.min_minutes_to_kickoff)):
        result = arbitrage.find_arb(market, settings.bankroll_mxn, fees)
        if result and arbitrage.should_alert(result, settings.min_roi, settings.min_profit_mxn):
            found.append(Arb(event_id=market.event_id, market=market.market, line=market.line,
                             legs=result.legs, roi=result.roi, profit_mxn=result.profit_mxn,
                             first_seen=now, last_seen=now))
    return found


def print_change(conn: sqlite3.Connection, settings: Settings, kind: str, arb: Arb) -> None:
    event = db.get_event(conn, arb.event_id)
    market = arb.market if arb.line is None else f"{arb.market} {arb.line:g}"
    title = f"{event.home} vs {event.away} | {event.league} | {event.kickoff_utc:%a %d %b %H:%M} UTC | {market}"
    if kind == "closed":
        minutes = int((arb.last_seen - arb.first_seen).total_seconds() // 60)
        print(f"[CLOSED] arb #{arb.id} {title} | lasted {minutes} min", flush=True)
        return
    verify = "  VERIFICAR" if arbitrage.needs_verify(arb.roi, settings.verify_roi_above) else ""
    print(f"[{kind.upper()}] arb #{arb.id} {arb.roi:.2%} | +{arb.profit_mxn:.2f} MXN"
          f" (bankroll {settings.bankroll_mxn:,.0f}){verify}")
    print(f"  {title}")
    for leg in arb.legs:
        print(f"  {leg.outcome:<5} {leg.decimal_odds:6.2f} @ {leg.book:<10} -> stake {leg.stake_mxn}")
    sys.stdout.flush()


async def book_loop(
    conn: sqlite3.Connection,
    settings: Settings,
    aliases: matching.Aliases,
    fees: dict[str, float],
    scraper: BaseScraper,
    book_id: int,
) -> None:
    """fetch -> save odds -> match -> detect -> track -> print, then sleep. Failures never stop other books."""
    while True:
        try:
            raws = await asyncio.wait_for(scraper.fetch(), FETCH_TIMEOUT_S)
            now = datetime.now(UTC)
            with conn:
                ingest(conn, settings, aliases, scraper, book_id, raws, now)
                changes = tracker.update(conn, detect(conn, settings, fees, now), scraper.name, now)
                db.mark_ok(conn, book_id, now)
            for kind, arb in changes:
                print_change(conn, settings, kind, arb)
        except Exception as e:
            log.exception("%s: cycle failed", scraper.name)
            with conn:
                db.mark_error(conn, book_id, repr(e))
        await asyncio.sleep(scraper.interval_s + random.uniform(0, JITTER_S))


async def run(settings: Settings, scrapers: list[BaseScraper], fees: dict[str, float]) -> None:
    conn = db.connect(DB_PATH)
    with conn:
        book_ids = db.sync_books(conn, [scraper.name for scraper in scrapers])
    aliases = matching.load_aliases(ALIASES_PATH)
    log.info("running books: %s", ", ".join(book_ids))
    await asyncio.gather(*(
        book_loop(conn, settings, aliases, fees, scraper, book_ids[scraper.name]) for scraper in scrapers
    ))


def setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")  # an odd team name must not crash a print
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    LOG_DIR.mkdir(exist_ok=True)
    handler = TimedRotatingFileHandler(LOG_DIR / "unmatched.log", when="midnight", backupCount=14,
                                       encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    unmatched = logging.getLogger("unmatched")
    unmatched.addHandler(handler)
    unmatched.propagate = False


def main() -> None:
    setup_logging()
    settings = Settings()
    scrapers, fees = build_scrapers(settings)
    if not scrapers:
        log.info("no enabled book has a scraper yet, exiting")
        return
    try:
        asyncio.run(run(settings, scrapers, fees))
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
