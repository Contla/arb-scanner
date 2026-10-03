import pytest

from app.arbitrage import find_arb, needs_verify, net_odds, should_alert
from app.models import Market

BANKROLL = 2000
MIN_ROI = 0.02
MIN_PROFIT = 50
VERIFY_ROI_ABOVE = 0.15


def market(kind, odds):
    return Market(event_id=1, market=kind, line=None, odds=odds)


def test_worked_example():
    # SPEC.md worked example, plus worse prices that must not be picked.
    arb = find_arb(
        market(
            "1x2",
            {
                "home": {"caliente": 2.60, "codere": 2.45, "stake": 2.50},
                "draw": {"caliente": 3.40, "codere": 3.60, "stake": 3.30},
                "away": {"caliente": 3.10, "codere": 3.25, "stake": 3.40},
            },
        ),
        BANKROLL,
    )
    assert [(leg.outcome, leg.book, leg.decimal_odds, leg.stake_mxn) for leg in arb.legs] == [
        ("home", "caliente", 2.60, 804),
        ("draw", "codere", 3.60, 581),
        ("away", "stake", 3.40, 615),
    ]
    assert arb.roi == pytest.approx(0.0455, abs=5e-5)
    assert arb.profit_mxn == pytest.approx(90.40)  # min payout 2,090.40 - 2,000 staked
    assert should_alert(arb, MIN_ROI, MIN_PROFIT)


def test_two_way_even_odds():
    arb = find_arb(market("moneyline", {"home": {"a": 2.10}, "away": {"b": 2.10}}), BANKROLL)
    assert arb.roi == pytest.approx(0.05)
    assert [leg.stake_mxn for leg in arb.legs] == [1000, 1000]
    assert arb.profit_mxn == pytest.approx(100)
    assert should_alert(arb, MIN_ROI, MIN_PROFIT)


def test_no_arb():
    # S = 1/4.00 + 1/1.25 = 1.05
    assert find_arb(market("moneyline", {"home": {"a": 4.00}, "away": {"b": 1.25}}), BANKROLL) is None


@pytest.mark.parametrize(
    "odds",
    [
        {"home": {"a": 2.60}, "away": {"b": 3.40}},
        {"home": {"a": 2.60}, "draw": {}, "away": {"b": 3.40}},
    ],
)
def test_missing_outcome_is_no_arb(odds):
    # home + away alone sum to 0.68, which would look like a huge arb.
    assert find_arb(market("1x2", odds), BANKROLL) is None


def test_rounding_drops_profit_below_min():
    # Exact stakes would make 57.39; rounded stakes 1888 / 112 pay 2,057.92 / 2,048.48.
    arb = find_arb(market("moneyline", {"home": {"a": 1.09}, "away": {"b": 18.29}}), BANKROLL)
    assert BANKROLL * arb.roi == pytest.approx(57.39, abs=0.01)
    assert [leg.stake_mxn for leg in arb.legs] == [1888, 112]
    assert arb.profit_mxn == pytest.approx(48.48)
    assert not should_alert(arb, MIN_ROI, MIN_PROFIT)


def test_big_arb_still_alerts_but_needs_verify():
    arb = find_arb(market("moneyline", {"home": {"a": 2.60}, "away": {"b": 2.60}}), BANKROLL)
    assert arb.roi == pytest.approx(0.30)
    assert should_alert(arb, MIN_ROI, MIN_PROFIT)
    assert needs_verify(arb.roi, VERIFY_ROI_ABOVE)


def test_fee_is_applied_before_filters():
    m = market("moneyline", {"home": {"a": 2.06}, "away": {"b": 2.06}})
    assert should_alert(find_arb(m, BANKROLL), MIN_ROI, MIN_PROFIT)  # 3% before commission

    arb = find_arb(m, BANKROLL, fees={"b": 0.05})  # b nets 1 + 1.06 * 0.95 = 2.007
    assert net_odds(2.06, 0.05) == pytest.approx(2.007)
    assert arb.roi == pytest.approx(0.0166, abs=1e-4)
    assert arb.legs[1].decimal_odds == 2.06  # message shows the book's own price
    assert not should_alert(arb, MIN_ROI, MIN_PROFIT)


def test_best_price_is_chosen_after_fee():
    # b shows 2.15 but nets 1 + 1.15 * 0.95 = 2.0925 < a's 2.10.
    m = market("moneyline", {"home": {"a": 2.10, "b": 2.15}, "away": {"c": 2.10}})
    arb = find_arb(m, BANKROLL, fees={"b": 0.05})
    assert arb.legs[0].book == "a"
