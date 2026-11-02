from __future__ import annotations

import pytest

from trading_bot.exceptions import RiskLimitError
from trading_bot.trading.position_sizer import SymbolContract, calculate_lot


GOLD = SymbolContract(
    volume_min=0.01,
    volume_max=50.0,
    volume_step=0.01,
    tick_size=0.01,
    tick_value=1.0,
)


def test_gold_lot_from_two_percent_and_80_cent_stop() -> None:
    sized = calculate_lot(
        balance=49708.88,
        risk_pct=2.0,
        entry=4309.79,
        stop_loss=4309.79 - 0.80,
        direction="BUY",
        contract=GOLD,
        max_position_size=50.0,
    )
    assert sized.quantity > 0
    expected = (49708.88 * 0.02) / ((0.80 / 0.01) * 1.0)
    assert abs(sized.quantity - round(expected // 0.01 * 0.01, 2)) < 0.02


def test_lot_is_risk_divided_by_points_from_low_to_breakout() -> None:
    """$10 risk over the stop, which is 521 points plus the 0.80."""
    low = 4264.826
    sized = calculate_lot(
        balance=500,
        risk_pct=2.0,
        entry=low + 5.210,
        stop_loss=low - 0.80,
        direction="BUY",
        contract=GOLD,
        max_position_size=1.0,
    )
    assert sized.quantity == 0.02
    assert sized.risk_money == 10


def test_sell_lot_is_risk_divided_by_points_from_high_to_breakdown() -> None:
    """Same rule as buy: $10 risk over the stop, including the 0.80."""
    high = 4286.754
    sized = calculate_lot(
        balance=500,
        risk_pct=2.0,
        entry=high - 5.210,
        stop_loss=high + 0.80,
        direction="SELL",
        contract=GOLD,
        max_position_size=1.0,
    )
    assert sized.quantity == 0.02
    assert sized.risk_money == 10


def test_six_hundred_points_on_a_500_account_rounds_to_two_cents() -> None:
    """$10 risk over a 5.80 stop is 0.017, and the nearest lot step is 0.02."""
    sized = calculate_lot(
        balance=500,
        risk_pct=2.0,
        entry=4500,
        stop_loss=4494.2,
        direction="BUY",
        contract=GOLD,
        max_position_size=1.0,
    )
    assert sized.quantity == 0.02
    assert sized.risk_money == 10


def test_fixed_15_dollars_sets_the_lot_from_the_full_stop() -> None:
    """Yellow 4500, sweep 4496.7, stop 4495.9. $15 over 410 points rounds to 0.04."""
    sized = calculate_lot(
        balance=751,
        risk_pct=2.0,
        risk_amount=15,
        entry=4500,
        stop_loss=4495.9,
        direction="BUY",
        contract=GOLD,
        max_position_size=1.0,
    )
    assert sized.risk_money == 15
    assert sized.quantity == 0.04


def test_cap_applies() -> None:
    sized = calculate_lot(
        balance=49708.88,
        risk_pct=2.0,
        entry=4309.79,
        stop_loss=4308.99,
        direction="BUY",
        contract=GOLD,
        max_position_size=1.0,
    )
    assert sized.quantity == 1.0
    assert sized.capped is True


def test_buy_sl_above_entry_rejected() -> None:
    with pytest.raises(RiskLimitError):
        calculate_lot(
            balance=1000,
            risk_pct=2,
            entry=100,
            stop_loss=101,
            direction="BUY",
            contract=GOLD,
            max_position_size=1,
        )


def test_sell_requires_sl_above_entry() -> None:
    with pytest.raises(RiskLimitError):
        calculate_lot(
            balance=1000,
            risk_pct=2,
            entry=100,
            stop_loss=99,
            direction="SELL",
            contract=GOLD,
            max_position_size=1,
        )
