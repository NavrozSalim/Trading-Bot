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
    """$500 at 2% is $10. 521 points from 4264.826 to the breakout is 10/521 = 0.02."""
    low = 4264.826
    sized = calculate_lot(
        balance=500,
        risk_pct=2.0,
        entry=low + 5.210,
        stop_loss=low - 0.80,
        direction="BUY",
        contract=GOLD,
        max_position_size=1.0,
        sizing_extreme=low,
    )
    assert sized.quantity == 0.02
    assert sized.risk_money == 10


def test_sell_lot_is_risk_divided_by_points_from_high_to_breakdown() -> None:
    """Same rule as buy: $10 risk / 521 points from the high after the blue to the breakdown."""
    high = 4286.754
    sized = calculate_lot(
        balance=500,
        risk_pct=2.0,
        entry=high - 5.210,
        stop_loss=high + 0.80,
        direction="SELL",
        contract=GOLD,
        max_position_size=1.0,
        sizing_extreme=high,
    )
    assert sized.quantity == 0.02
    assert sized.risk_money == 10


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
