"""Two fake books whose combined odds hold the SPEC.md worked-example arb (4.55%, +90 MXN).

Best prices across both: home 2.60 (fake_a), draw 3.60 and away 3.40 (fake_b); neither
book is an arb on its own. fake_b lists Pumas vs Tigres reversed, so a broken swap check
shows up as a fake ~21% arb. Edit the prices and restart to see the arb update or close.
"""

from datetime import UTC, datetime, timedelta

from app.models import OddsFormat, RawOdds
from app.scrapers.base import BaseScraper

# (listing id, league, home, away, [(market, line, outcome, odds)])
Listing = tuple[str, str, str, str, list[tuple[str, str | None, str, float | str]]]

FAKE_A: list[Listing] = [
    ("a-1", "Liga MX", "Monterrey", "América", [
        ("Resultado final", None, "1", 2.60),
        ("Resultado final", None, "X", 3.25),
        ("Resultado final", None, "2", 2.75),
        ("Total de goles", "2.5", "Más", 1.90),
        ("Total de goles", "2.5", "Menos", 1.90),
    ]),
    ("a-2", "Liga MX", "Tigres UANL", "Pumas UNAM", [
        ("Resultado final", None, "1", 2.00),
        ("Resultado final", None, "X", 3.40),
        ("Resultado final", None, "2", 3.80),
    ]),
]

FAKE_B: list[Listing] = [  # American odds
    ("b-1", "México - Liga MX", "CF Monterrey", "Club América", [
        ("Resultado final", None, "1", "+120"),  # 2.20
        ("Resultado final", None, "X", "+260"),  # 3.60
        ("Resultado final", None, "2", "+240"),  # 3.40
        ("Total de goles", "2.5", "Más", "-125"),  # 1.80
        ("Total de goles", "2.5", "Menos", "+100"),  # 2.00
    ]),
    ("b-2", "México - Liga MX", "Pumas UNAM", "Tigres UANL", [
        ("Resultado final", None, "1", "+270"),  # 3.70
        ("Resultado final", None, "X", "+230"),  # 3.30
        ("Resultado final", None, "2", "+105"),  # 2.05
    ]),
]


class FakeScraper(BaseScraper):
    markets = {"Resultado final": "1x2", "Total de goles": "totals"}
    outcomes = {"1": "home", "X": "draw", "2": "away", "Más": "over", "Menos": "under"}

    def __init__(self, name: str, odds: list[RawOdds]) -> None:
        self.name = name
        self.odds = odds

    async def fetch(self) -> list[RawOdds]:
        return self.odds


def make_scrapers() -> list[BaseScraper]:
    kickoff = (datetime.now(UTC) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    return [
        FakeScraper("fake_a", _raw_odds("fake_a", "decimal", kickoff, FAKE_A)),
        FakeScraper("fake_b", _raw_odds("fake_b", "american", kickoff + timedelta(minutes=5), FAKE_B)),
    ]


def _raw_odds(book: str, fmt: OddsFormat, kickoff: datetime, listings: list[Listing]) -> list[RawOdds]:
    return [
        RawOdds(
            book=book,
            book_event_id=f"{listing_id}-{kickoff:%Y%m%d%H}",  # restarting on a later day makes new events
            sport="soccer",
            league=league,
            raw_home=home,
            raw_away=away,
            kickoff_utc=kickoff,
            raw_market=market,
            raw_line=line,
            raw_outcome=outcome,
            odds_value=odds,
            odds_format=fmt,
        )
        for listing_id, league, home, away, prices in listings
        for market, line, outcome, odds in prices
    ]
