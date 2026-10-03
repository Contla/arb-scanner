"""Arbitrage math. Pure functions only: no database, network or logging."""

from collections.abc import Mapping
from dataclasses import dataclass

from app.models import MARKET_OUTCOMES, Leg, Market, Outcome


@dataclass(frozen=True)
class ArbResult:
    legs: list[Leg]  # one per outcome, book's own odds, stakes rounded to whole pesos
    roi: float  # 1/S - 1 on net odds
    profit_mxn: float  # guaranteed profit with the rounded stakes


def net_odds(odds: float, fee_pct: float = 0.0) -> float:
    """Decimal odds after the book takes fee_pct of the winnings."""
    return 1 + (odds - 1) * (1 - fee_pct)


def find_arb(
    market: Market, bankroll_mxn: float, fees: Mapping[str, float] | None = None
) -> ArbResult | None:
    """Take the best net price per outcome across books; return the arb if S < 1.

    None when there is no arb or any outcome of the market has no price.
    fees maps book -> fee_pct; missing books pay no commission.
    """
    fees = fees or {}

    def net(book: str, odds: float) -> float:
        return net_odds(odds, fees.get(book, 0.0))

    best: list[tuple[Outcome, str, float]] = []
    for outcome in MARKET_OUTCOMES[market.market]:
        prices = market.odds.get(outcome)
        if not prices:
            return None
        book, odds = max(prices.items(), key=lambda price: net(*price))
        best.append((outcome, book, odds))

    nets = [net(book, odds) for _, book, odds in best]
    s = sum(1 / n for n in nets)
    if s >= 1:
        return None

    stakes = [round(bankroll_mxn * (1 / n) / s) for n in nets]
    profit = min(stake * n for stake, n in zip(stakes, nets)) - sum(stakes)
    legs = [Leg(outcome, book, odds, stake) for (outcome, book, odds), stake in zip(best, stakes)]
    return ArbResult(legs=legs, roi=1 / s - 1, profit_mxn=round(profit, 2))


def should_alert(arb: ArbResult, min_roi: float, min_profit_mxn: float) -> bool:
    """No upper ROI cap: big arbs still alert, see needs_verify."""
    return arb.roi >= min_roi and arb.profit_mxn >= min_profit_mxn


def needs_verify(roi: float, verify_roi_above: float) -> bool:
    """Big arbs are often a wrong match or a palpable price: tag VERIFICAR, log as suspicious."""
    return roi > verify_roi_above
