"""Odds conversion, team-name cleaning, and mapping book labels to canonical markets."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace

from app.models import MARKET_OUTCOMES, MarketType, OddsFormat, Outcome, RawOdds

MIN_ODDS, MAX_ODDS = 1.01, 1000.0
LINE_MARKETS = {"totals", "spread"}
# Filler words dropped before fuzzy matching: "Club de Fútbol Monterrey" -> "monterrey".
NAME_STOPWORDS = {"fc", "cf", "club", "deportivo", "cd", "sc", "de", "the", "futbol"}


@dataclass(frozen=True)
class Quote:
    """One canonical price. A spread line is the home team's handicap."""

    market: MarketType
    line: float | None
    outcome: Outcome
    decimal_odds: float


def to_decimal(value: str | float, fmt: OddsFormat) -> float | None:
    """Decimal odds, or None if unparseable or outside 1.01-1000."""
    try:
        if fmt == "decimal":
            odds = _number(value)
        elif fmt == "american":
            american = _number(value)
            if american >= 100:
                odds = 1 + american / 100
            elif american <= -100:
                odds = 1 + 100 / -american
            else:
                return None
        else:
            numerator, denominator = str(value).split("/")
            odds = 1 + _number(numerator) / _number(denominator)
    except (ValueError, ZeroDivisionError):
        return None
    return odds if MIN_ODDS <= odds <= MAX_ODDS else None


def clean_name(name: str) -> str:
    """Lowercase, no accents, punctuation or filler words, single spaces."""
    text = unicodedata.normalize("NFKD", name.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[.'’]", "", text)  # F.C. -> fc, Newell's -> newells
    text = re.sub(r"[^\w\s]", " ", text)  # Saint-Germain -> saint germain
    return " ".join(word for word in text.split() if word not in NAME_STOPWORDS)


def to_quote(raw: RawOdds, markets: Mapping[str, MarketType], outcomes: Mapping[str, Outcome]) -> Quote | None:
    """Map a raw price with the scraper's label tables; None if anything is unknown or invalid.

    For spreads, raw_line is the handicap of the outcome's own team.
    """
    market = markets.get(raw.raw_market.strip())
    if market is None:
        return None
    label = raw.raw_outcome.strip()
    outcome = outcomes.get(label) or {raw.raw_home.strip(): "home", raw.raw_away.strip(): "away"}.get(label)
    if outcome not in MARKET_OUTCOMES[market]:
        return None
    line = None
    if market in LINE_MARKETS:
        try:
            line = _number(raw.raw_line)
        except ValueError:  # missing or unreadable line
            return None
        if market == "spread" and outcome == "away":
            line = -line
    odds = to_decimal(raw.odds_value, raw.odds_format)
    if odds is None:
        return None
    return Quote(market, line, outcome, odds)


def swap_sides(quote: Quote) -> Quote:
    """The same price, for a listing whose home and away are reversed from the canonical event."""
    outcome = {"home": "away", "away": "home"}.get(quote.outcome, quote.outcome)
    line = -quote.line if quote.market == "spread" else quote.line
    return replace(quote, outcome=outcome, line=line)


def _number(value: str | float) -> float:
    return float(str(value).replace("−", "-"))  # some books use a Unicode minus sign
