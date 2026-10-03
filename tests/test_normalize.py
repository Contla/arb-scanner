from datetime import UTC, datetime

import pytest

from app.models import RawOdds
from app.normalize import Quote, clean_name, swap_sides, to_decimal, to_quote

MARKETS = {"Resultado final": "1x2", "Total de goles": "totals", "Hándicap": "spread"}
OUTCOMES = {"1": "home", "X": "draw", "2": "away", "Más": "over", "Menos": "under"}


def raw(**overrides) -> RawOdds:
    fields = dict(
        book="book", book_event_id="1", sport="soccer", league="Liga MX", raw_home="Monterrey",
        raw_away="América", kickoff_utc=datetime(2026, 10, 10, 1, tzinfo=UTC), raw_market="Resultado final",
        raw_line=None, raw_outcome="1", odds_value="+150", odds_format="american",
    )
    return RawOdds(**(fields | overrides))


@pytest.mark.parametrize(
    "value, fmt, expected",
    [
        ("+150", "american", 2.50),
        ("-200", "american", 1.50),
        ("−200", "american", 1.50),  # Unicode minus sign
        (150, "american", 2.50),
        ("3/2", "fractional", 2.50),
        ("2.10", "decimal", 2.10),
        ("1.01", "decimal", 1.01),
        (1000, "decimal", 1000),
    ],
)
def test_to_decimal(value, fmt, expected):
    assert to_decimal(value, fmt) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value, fmt",
    [("1.005", "decimal"), ("1001", "decimal"), ("+50", "american"), ("abc", "decimal"),
     ("3/0", "fractional"), ("", "american")],
)
def test_to_decimal_rejects(value, fmt):
    assert to_decimal(value, fmt) is None


@pytest.mark.parametrize(
    "name, cleaned",
    [
        ("Club de Fútbol Monterrey", "monterrey"),
        ("Club América", "america"),
        ("Atlético de San Luis", "atletico san luis"),
        ("F.C. Juárez", "juarez"),
        ("Paris Saint-Germain", "paris saint germain"),
        ("  Querétaro   FC ", "queretaro"),
    ],
)
def test_clean_name(name, cleaned):
    assert clean_name(name) == cleaned


def test_to_quote_maps_labels_and_converts_odds():
    assert to_quote(raw(), MARKETS, OUTCOMES) == Quote("1x2", None, "home", 2.50)


def test_to_quote_outcome_named_after_team():
    assert to_quote(raw(raw_outcome="América"), MARKETS, OUTCOMES).outcome == "away"


def test_to_quote_totals_line():
    quote = to_quote(raw(raw_market="Total de goles", raw_line="2.5", raw_outcome="Más"), MARKETS, OUTCOMES)
    assert (quote.market, quote.line, quote.outcome) == ("totals", 2.5, "over")


def test_to_quote_spread_line_is_home_handicap():
    home = to_quote(raw(raw_market="Hándicap", raw_line="-1.5", raw_outcome="1"), MARKETS, OUTCOMES)
    away = to_quote(raw(raw_market="Hándicap", raw_line="+1.5", raw_outcome="2"), MARKETS, OUTCOMES)
    assert home.line == away.line == -1.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"raw_market": "Goleador"},  # unknown market: ignored, not guessed
        {"raw_outcome": "Otro"},  # unknown outcome
        {"raw_outcome": "Más"},  # not an outcome of 1x2
        {"raw_market": "Total de goles", "raw_outcome": "Más"},  # totals without a line
        {"odds_value": "+20"},  # invalid odds
    ],
)
def test_to_quote_skips(overrides):
    assert to_quote(raw(**overrides), MARKETS, OUTCOMES) is None


@pytest.mark.parametrize(
    "quote, swapped",
    [
        (Quote("1x2", None, "home", 2.5), Quote("1x2", None, "away", 2.5)),
        (Quote("1x2", None, "draw", 3.0), Quote("1x2", None, "draw", 3.0)),
        (Quote("totals", 2.5, "over", 1.9), Quote("totals", 2.5, "over", 1.9)),
        (Quote("spread", -1.5, "home", 1.9), Quote("spread", 1.5, "away", 1.9)),
    ],
)
def test_swap_sides(quote, swapped):
    assert swap_sides(quote) == swapped
