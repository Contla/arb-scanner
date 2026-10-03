"""SQLite schema and helpers. Callers own transactions (`with conn:`); datetimes are UTC-aware."""

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models import Arb, Event, Leg, Market

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_ok_at  UTCTIME,
    last_error  TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    sport       TEXT NOT NULL,
    league      TEXT,
    home        TEXT NOT NULL,
    away        TEXT NOT NULL,
    kickoff_utc UTCTIME NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_kickoff ON events (sport, kickoff_utc);
-- swapped: the book lists home and away the other way round from the canonical event.
CREATE TABLE IF NOT EXISTS book_events (
    book_id       INTEGER NOT NULL REFERENCES books (id),
    book_event_id TEXT NOT NULL,
    event_id      INTEGER NOT NULL REFERENCES events (id),
    raw_home      TEXT NOT NULL,
    raw_away      TEXT NOT NULL,
    url           TEXT,
    swapped       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, book_event_id)
);
-- Latest prices only: a book's rows are replaced on each successful fetch.
CREATE TABLE IF NOT EXISTS odds (
    event_id     INTEGER NOT NULL REFERENCES events (id),
    book_id      INTEGER NOT NULL REFERENCES books (id),
    market       TEXT NOT NULL,
    line         REAL,
    outcome      TEXT NOT NULL,
    decimal_odds REAL NOT NULL,
    fetched_at   UTCTIME NOT NULL
);
CREATE INDEX IF NOT EXISTS odds_by_book ON odds (book_id);
CREATE TABLE IF NOT EXISTS arbs (
    id              INTEGER PRIMARY KEY,
    event_id        INTEGER NOT NULL REFERENCES events (id),
    market          TEXT NOT NULL,
    line            REAL,
    legs_json       TEXT NOT NULL,
    roi             REAL NOT NULL,
    profit_mxn      REAL NOT NULL,
    first_seen      UTCTIME NOT NULL,
    last_seen       UTCTIME NOT NULL,
    closed_at       UTCTIME,
    telegram_msg_id INTEGER,
    misses          INTEGER NOT NULL DEFAULT 0
);
"""


def _adapt_datetime(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime {dt}: use UTC")
    return dt.astimezone(UTC).isoformat(timespec="seconds")


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("UTCTIME", lambda b: datetime.fromisoformat(b.decode()))


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def sync_books(conn: sqlite3.Connection, names: Iterable[str]) -> dict[str, int]:
    """Enable these books (adding new ones) and disable the rest. Returns name -> id."""
    conn.execute("UPDATE books SET enabled = 0")
    conn.executemany(
        "INSERT INTO books (name) VALUES (?) ON CONFLICT (name) DO UPDATE SET enabled = 1",
        [(name,) for name in names],
    )
    return {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM books WHERE enabled")}


def mark_ok(conn: sqlite3.Connection, book_id: int, at: datetime) -> None:
    conn.execute("UPDATE books SET last_ok_at = ?, last_error = NULL WHERE id = ?", (at, book_id))


def mark_error(conn: sqlite3.Connection, book_id: int, error: str) -> None:
    conn.execute("UPDATE books SET last_error = ? WHERE id = ?", (error, book_id))


def get_link(conn: sqlite3.Connection, book_id: int, book_event_id: str) -> tuple[int, bool] | None:
    """(event_id, swapped) if this listing was already linked to a canonical event."""
    row = conn.execute(
        "SELECT event_id, swapped FROM book_events WHERE book_id = ? AND book_event_id = ?",
        (book_id, book_event_id),
    ).fetchone()
    return (row["event_id"], bool(row["swapped"])) if row else None


def add_link(
    conn: sqlite3.Connection,
    book_id: int,
    book_event_id: str,
    event_id: int,
    raw_home: str,
    raw_away: str,
    url: str | None,
    swapped: bool,
) -> None:
    conn.execute(
        "INSERT INTO book_events (book_id, book_event_id, event_id, raw_home, raw_away, url, swapped)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book_id, book_event_id, event_id, raw_home, raw_away, url, swapped),
    )


def candidate_events(
    conn: sqlite3.Connection, sport: str, kickoff: datetime, tolerance: timedelta, book_id: int
) -> list[Event]:
    """Events of this sport near kickoff that the book is not linked to yet."""
    rows = conn.execute(
        """
        SELECT * FROM events e
        WHERE sport = ? AND kickoff_utc BETWEEN ? AND ?
          AND NOT EXISTS (SELECT 1 FROM book_events b WHERE b.event_id = e.id AND b.book_id = ?)
        """,
        (sport, kickoff - tolerance, kickoff + tolerance, book_id),
    )
    return [Event(**dict(row)) for row in rows]


def insert_event(conn: sqlite3.Connection, event: Event) -> int:
    cur = conn.execute(
        "INSERT INTO events (sport, league, home, away, kickoff_utc) VALUES (?, ?, ?, ?, ?)",
        (event.sport, event.league, event.home, event.away, event.kickoff_utc),
    )
    return cur.lastrowid


def get_event(conn: sqlite3.Connection, event_id: int) -> Event:
    return Event(**dict(conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()))


def replace_odds(
    conn: sqlite3.Connection,
    book_id: int,
    prices: Mapping[tuple[int, str, float | None, str], float],
    fetched_at: datetime,
) -> None:
    """Make prices, keyed by (event_id, market, line, outcome), the book's whole current list.

    Prices the book no longer offers are dropped, so they cannot keep a dead arb alive.
    """
    conn.execute("DELETE FROM odds WHERE book_id = ?", (book_id,))
    conn.executemany(
        "INSERT INTO odds (event_id, book_id, market, line, outcome, decimal_odds, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(event_id, book_id, market, line, outcome, odds, fetched_at)
         for (event_id, market, line, outcome), odds in prices.items()],
    )


def load_markets(conn: sqlite3.Connection, kickoff_after: datetime) -> list[Market]:
    """Enabled books' prices for events starting after kickoff_after, one Market per event + market + line."""
    markets: dict[tuple[int, str, float | None], Market] = {}
    rows = conn.execute(
        """
        SELECT o.event_id, o.market, o.line, o.outcome, b.name AS book, o.decimal_odds
        FROM odds o
        JOIN events e ON e.id = o.event_id
        JOIN books b ON b.id = o.book_id
        WHERE b.enabled AND e.kickoff_utc > ?
        """,
        (kickoff_after,),
    )
    for row in rows:
        key = (row["event_id"], row["market"], row["line"])
        market = markets.setdefault(key, Market(*key))
        market.odds.setdefault(row["outcome"], {})[row["book"]] = row["decimal_odds"]
    return list(markets.values())


def open_arbs(conn: sqlite3.Connection) -> list[Arb]:
    arbs = []
    for row in conn.execute("SELECT * FROM arbs WHERE closed_at IS NULL"):
        fields = dict(row)
        fields["legs"] = [Leg(**leg) for leg in json.loads(fields.pop("legs_json"))]
        arbs.append(Arb(**fields))
    return arbs


def insert_arb(conn: sqlite3.Connection, arb: Arb) -> int:
    cur = conn.execute(
        "INSERT INTO arbs (event_id, market, line, legs_json, roi, profit_mxn, first_seen, last_seen,"
        " closed_at, telegram_msg_id, misses) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (arb.event_id, arb.market, arb.line, _legs_json(arb), arb.roi, arb.profit_mxn, arb.first_seen,
         arb.last_seen, arb.closed_at, arb.telegram_msg_id, arb.misses),
    )
    return cur.lastrowid


def update_arb(conn: sqlite3.Connection, arb: Arb) -> None:
    conn.execute(
        "UPDATE arbs SET legs_json = ?, roi = ?, profit_mxn = ?, last_seen = ?, closed_at = ?,"
        " telegram_msg_id = ?, misses = ? WHERE id = ?",
        (_legs_json(arb), arb.roi, arb.profit_mxn, arb.last_seen, arb.closed_at, arb.telegram_msg_id,
         arb.misses, arb.id),
    )


def _legs_json(arb: Arb) -> str:
    return json.dumps([asdict(leg) for leg in arb.legs])
