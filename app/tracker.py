"""Arb lifecycle: open when first seen, update while it lasts, close once it is gone."""

import sqlite3
from datetime import datetime
from typing import Literal

from app import db
from app.models import Arb

# Closed after this many detection passes without it, counting only passes run after one of
# its own books was fetched, so a single bad fetch does not close it.
CLOSE_AFTER_MISSES = 3

Change = tuple[Literal["opened", "updated", "closed"], Arb]


def update(conn: sqlite3.Connection, found: list[Arb], book: str, now: datetime) -> list[Change]:
    """Apply one detection pass, run right after `book` was fetched. Returns what changed."""
    open_arbs = {_key(arb): arb for arb in db.open_arbs(conn)}
    changes: list[Change] = []

    for arb in found:
        old = open_arbs.pop(_key(arb), None)
        if old is None:
            arb.id = db.insert_arb(conn, arb)
            changes.append(("opened", arb))
            continue
        moved = old.legs != arb.legs
        old.legs, old.roi, old.profit_mxn = arb.legs, arb.roi, arb.profit_mxn
        old.last_seen, old.misses = now, 0
        db.update_arb(conn, old)
        if moved:
            changes.append(("updated", old))

    for old in open_arbs.values():
        if book not in {leg.book for leg in old.legs}:
            continue
        old.misses += 1
        if old.misses >= CLOSE_AFTER_MISSES:
            old.closed_at = now
            changes.append(("closed", old))
        db.update_arb(conn, old)
    return changes


def _key(arb: Arb) -> tuple:
    """Same event + market + line + set of books is the same arb."""
    return arb.event_id, arb.market, arb.line, frozenset(leg.book for leg in arb.legs)
