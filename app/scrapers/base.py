from abc import ABC, abstractmethod
from typing import ClassVar

from app.models import MarketType, Outcome, RawOdds


class BaseScraper(ABC):
    name: str  # "caliente"
    interval_s: int = 60

    # The book's own labels -> canonical ones. Labels not listed are ignored, never guessed.
    # An outcome label equal to the listing's home or away team name maps to home/away.
    markets: ClassVar[dict[str, MarketType]] = {}
    outcomes: ClassVar[dict[str, Outcome]] = {}

    @abstractmethod
    async def fetch(self) -> list[RawOdds]: ...
