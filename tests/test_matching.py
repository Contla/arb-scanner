from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.matching import AmbiguousMatch, Match, build_aliases, link_listing, match_event, team_score
from app.models import Event, RawOdds
from app.settings import MatchConfig

CFG = MatchConfig(kickoff_tolerance_min=15, name_score_min=88)
KICKOFF = datetime(2026, 10, 10, 1, tzinfo=UTC)
ALIASES = build_aliases({"america": ["club america", "aguilas del america"]})


def event(home, away, id=1, kickoff=KICKOFF, sport="soccer", league="Liga MX") -> Event:
    return Event(id=id, sport=sport, league=league, home=home, away=away, kickoff_utc=kickoff)


def listing(home, away, **kwargs) -> Event:
    return event(home, away, id=None, **kwargs)


def test_accented_names_match():
    ev = event("Club de Fútbol Monterrey", "Atlético de San Luis")
    assert match_event(listing("Monterrey", "Atletico San Luis"), [ev], {}, CFG) == Match(ev, swapped=False)


def test_alias_matches_what_fuzzy_cannot():
    assert team_score("Águilas del América", "Club America", {}) < 88
    assert team_score("Águilas del América", "Club America", ALIASES) == 100


def test_aliases_override_fuzzy():
    # Fuzzy matching thinks a B team is the first team; aliases say they differ.
    aliases = build_aliases({"real sociedad": [], "real sociedad b": ["real sociedad ii"]})
    assert team_score("Real Sociedad", "Real Sociedad B", {}) >= 88
    assert team_score("Real Sociedad", "Real Sociedad B", aliases) == 0


def test_swapped_home_away_is_flagged():
    ev = event("Tigres UANL", "Pumas UNAM")
    assert match_event(listing("Pumas UNAM", "Tigres UANL"), [ev], ALIASES, CFG) == Match(ev, swapped=True)


def test_league_missing_on_one_side_still_matches():
    ev = event("Monterrey", "América", league=None)
    assert match_event(listing("Monterrey", "America"), [ev], ALIASES, CFG) == Match(ev, swapped=False)


@pytest.mark.parametrize(
    "candidate",
    [
        event("Monterrey", "América", kickoff=KICKOFF + timedelta(minutes=16)),
        event("Monterrey", "América", sport="basketball"),
        event("Monterrey", "América", league="Premier League"),
        event("Monterrey", "Tigres UANL"),  # only one team matches
    ],
)
def test_no_match(candidate):
    assert match_event(listing("Monterrey", "América"), [candidate], ALIASES, CFG) is None


def test_ambiguous_match_raises():
    events = [
        event("Monterrey", "América", id=1),
        event("CF Monterrey", "Club América", id=2, kickoff=KICKOFF + timedelta(minutes=10)),
    ]
    with pytest.raises(AmbiguousMatch):
        match_event(listing("Monterrey", "America", kickoff=KICKOFF + timedelta(minutes=5)), events, ALIASES, CFG)


def raw(book_event_id, home, away, kickoff=KICKOFF) -> RawOdds:
    return RawOdds(
        book="book", book_event_id=book_event_id, sport="soccer", league="Liga MX", raw_home=home,
        raw_away=away, kickoff_utc=kickoff, raw_market="Resultado final", raw_line=None, raw_outcome="1",
        odds_value=2.0, odds_format="decimal",
    )


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    yield conn
    conn.close()


def test_link_listing_creates_event_then_links_swapped(conn):
    books = db.sync_books(conn, ["a", "b"])
    event_id, swapped = link_listing(conn, books["a"], raw("a-1", "Tigres UANL", "Pumas UNAM"), ALIASES, CFG)
    assert not swapped
    linked = link_listing(conn, books["b"], raw("b-1", "Pumas UNAM", "Tigres UANL"), ALIASES, CFG)
    assert linked == (event_id, True)
    assert db.get_link(conn, books["b"], "b-1") == (event_id, True)


def test_ambiguous_listing_is_skipped(conn):
    books = db.sync_books(conn, ["a", "b"])
    link_listing(conn, books["a"], raw("a-1", "Monterrey", "América"), ALIASES, CFG)
    link_listing(conn, books["a"], raw("a-2", "Monterrey", "América", KICKOFF + timedelta(minutes=10)), ALIASES, CFG)

    b_listing = raw("b-1", "CF Monterrey", "Club América", KICKOFF + timedelta(minutes=5))
    assert link_listing(conn, books["b"], b_listing, ALIASES, CFG) is None
    assert db.get_link(conn, books["b"], "b-1") is None
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2  # no new event guessed either
