"""Cross-book event matching. A wrong match creates a fake arb, so be strict and never guess."""

import logging
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from app import db
from app.models import Event, RawOdds
from app.normalize import clean_name
from app.settings import MatchConfig

log = logging.getLogger(__name__)
unmatched_log = logging.getLogger("unmatched")

Aliases = dict[str, str]  # cleaned name -> canonical name


@dataclass(frozen=True)
class Match:
    event: Event
    swapped: bool  # the listing has home and away reversed


class AmbiguousMatch(Exception):
    """More than one canonical event fits a listing."""


def build_aliases(raw: Mapping[str, list[str] | None]) -> Aliases:
    """From the team_aliases.yaml shape: {canonical: [other names]}."""
    aliases = {}
    for canonical, names in raw.items():
        for name in [canonical, *(names or [])]:
            aliases[clean_name(str(name))] = clean_name(str(canonical))
    return aliases


def load_aliases(path: Path) -> Aliases:
    return build_aliases(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def team_score(a: str, b: str, aliases: Aliases) -> float:
    """0-100 likeness of two team names. When both names are aliased, the aliases decide."""
    a, b = clean_name(a), clean_name(b)
    if a in aliases and b in aliases:
        return 100.0 if aliases[a] == aliases[b] else 0.0
    a, b = aliases.get(a, a), aliases.get(b, b)
    return fuzz.token_sort_ratio(a, b) if a and b else 0.0


def match_event(listing: Event, candidates: Iterable[Event], aliases: Aliases, cfg: MatchConfig) -> Match | None:
    """The one canonical event that is the same real match as listing, or None.

    Raises AmbiguousMatch when more than one fits.
    """
    tolerance = timedelta(minutes=cfg.kickoff_tolerance_min)

    def same(a: str, b: str) -> bool:
        return team_score(a, b, aliases) >= cfg.name_score_min

    matches = []
    for event in candidates:
        if event.sport != listing.sport or abs(event.kickoff_utc - listing.kickoff_utc) > tolerance:
            continue
        if listing.league and event.league and (
            fuzz.token_set_ratio(clean_name(listing.league), clean_name(event.league)) < cfg.name_score_min
        ):
            continue
        if same(listing.home, event.home) and same(listing.away, event.away):
            matches.append(Match(event, swapped=False))
        elif same(listing.home, event.away) and same(listing.away, event.home):
            matches.append(Match(event, swapped=True))
    if len(matches) > 1:
        raise AmbiguousMatch(f"{listing.home} vs {listing.away} fits events {[m.event.id for m in matches]}")
    return matches[0] if matches else None


def link_listing(
    conn: sqlite3.Connection, book_id: int, raw: RawOdds, aliases: Aliases, cfg: MatchConfig
) -> tuple[int, bool] | None:
    """(event_id, swapped) for a book's listing, or None if it is ambiguous and must be skipped.

    Links are stored, so each listing is matched once. A listing that matches nothing becomes
    a new canonical event.
    """
    if link := db.get_link(conn, book_id, raw.book_event_id):
        return link
    listing = Event(sport=raw.sport, league=raw.league, home=raw.raw_home, away=raw.raw_away,
                    kickoff_utc=raw.kickoff_utc)
    tolerance = timedelta(minutes=cfg.kickoff_tolerance_min)
    candidates = db.candidate_events(conn, raw.sport, raw.kickoff_utc, tolerance, book_id)
    try:
        match = match_event(listing, candidates, aliases, cfg)
    except AmbiguousMatch as e:
        log.warning("%s: skipped ambiguous listing %s", raw.book, e)
        return None

    if match:
        event_id, swapped = match.event.id, match.swapped
        if swapped:
            log.info("%s: %s vs %s matched event %d with home/away swapped",
                     raw.book, raw.raw_home, raw.raw_away, event_id)
    else:
        event_id, swapped = db.insert_event(conn, listing), False
        if candidates:  # another book has a match at this time: maybe a missing alias
            closest = max(candidates, key=lambda e: _names_score(listing, e, aliases))
            unmatched_log.info("%s | %s | %s vs %s | %s | closest: %s vs %s (%s)",
                               raw.book, raw.league, raw.raw_home, raw.raw_away, raw.kickoff_utc,
                               closest.home, closest.away, closest.league)
    db.add_link(conn, book_id, raw.book_event_id, event_id, raw.raw_home, raw.raw_away, raw.url, swapped)
    return event_id, swapped


def _names_score(listing: Event, event: Event, aliases: Aliases) -> float:
    def score(home: str, away: str) -> float:
        return min(team_score(home, event.home, aliases), team_score(away, event.away, aliases))

    return max(score(listing.home, listing.away), score(listing.away, listing.home))
