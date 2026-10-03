from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# Canonical values: keep these exact strings (SPEC.md "Data model").
Sport = Literal["soccer", "basketball", "baseball", "football", "tennis", "hockey"]
MarketType = Literal["1x2", "moneyline", "totals", "spread", "btts"]
Outcome = Literal["home", "draw", "away", "over", "under", "yes", "no"]
OddsFormat = Literal["decimal", "american", "fractional"]

# Outcomes that must all be priced for a market to be checked for arbitrage.
MARKET_OUTCOMES: dict[MarketType, tuple[Outcome, ...]] = {
    "1x2": ("home", "draw", "away"),
    "moneyline": ("home", "away"),
    "totals": ("over", "under"),
    "spread": ("home", "away"),
    "btts": ("yes", "no"),
}


@dataclass(frozen=True, kw_only=True)
class RawOdds:
    """One price exactly as a scraper read it, before normalization."""

    book: str
    book_event_id: str
    sport: Sport
    league: str | None
    raw_home: str
    raw_away: str
    kickoff_utc: datetime
    raw_market: str
    raw_line: str | float | None
    raw_outcome: str
    odds_value: str | float
    odds_format: OddsFormat
    url: str | None = None


@dataclass(kw_only=True)
class Event:
    """Canonical event, one per real match."""

    id: int | None = None
    sport: Sport
    league: str | None
    home: str
    away: str
    kickoff_utc: datetime


@dataclass
class Market:
    """All books' decimal odds for one event + market + line."""

    event_id: int
    market: MarketType
    line: float | None
    odds: dict[Outcome, dict[str, float]] = field(default_factory=dict)  # outcome -> book -> odds


@dataclass(frozen=True)
class Leg:
    """One bet of an arb."""

    outcome: Outcome
    book: str
    decimal_odds: float
    stake_mxn: int
    url: str | None = None


@dataclass(kw_only=True)
class Arb:
    """An arbitrage opportunity and its lifecycle."""

    id: int | None = None
    event_id: int
    market: MarketType
    line: float | None
    legs: list[Leg]
    roi: float
    profit_mxn: float
    first_seen: datetime
    last_seen: datetime
    closed_at: datetime | None = None
    telegram_msg_id: int | None = None
    misses: int = 0  # consecutive detection passes it was not found in
