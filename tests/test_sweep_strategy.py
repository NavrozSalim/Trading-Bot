from __future__ import annotations

from datetime import datetime, timezone

from trading_bot.market.candle_reader import Candle
from trading_bot.strategy.signals import SignalType
from trading_bot.strategy.strategy import SweepBreakoutStrategy


def _c(
    minute: int, open_: float, high: float, low: float, close: float
) -> Candle:
    return Candle(
        timestamp=datetime(2026, 9, 23, 12, minute, tzinfo=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
        is_closed=True,
    )


def _buy_0603() -> list[Candle]:
    """Two reds, last yellow, next candle sweeps the low, a red in between, green close above the yellow high."""
    return [
        _c(0, 10.0, 11.0, 9.50, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.20, 9.55),
        _c(3, 9.55, 9.65, 9.00, 9.60),
        _c(4, 9.40, 9.60, 9.30, 9.55),
        _c(5, 9.50, 10.20, 9.40, 9.85),
    ]


def _sell_breakdown() -> list[Candle]:
    """Two greens, last blue, next candle sweeps the high, then two reds. Stop is above the sweep high."""
    return [
        _c(0, 10.0, 10.20, 9.90, 10.15),
        _c(1, 10.15, 10.40, 10.10, 10.30),
        _c(2, 10.30, 10.90, 10.20, 10.25),
        _c(3, 10.25, 11.20, 10.15, 10.40),
        _c(4, 10.40, 10.45, 10.20, 10.22),
        _c(5, 10.22, 10.24, 10.05, 10.10),
    ]


def test_buy_next_candle_sweeps_then_green_closes_above_yellow_high() -> None:
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        _buy_0603(), symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"
    assert signal.stop_loss == round(9.00 - 0.80, 5)
    assert "yellow" in signal.reason


def test_sell_sweep_then_two_reds_stop_above_setup_high() -> None:
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        _sell_breakdown(), symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_blue_retest"
    assert signal.stop_loss == round(11.20 + 0.80, 5)


def test_buy_allows_a_green_between_the_reds_and_the_yellow() -> None:
    candles = [
        _c(0, 10.2, 10.4, 9.9, 10.0),
        _c(1, 10.0, 10.1, 9.7, 9.8),
        _c(2, 9.8, 9.95, 9.6, 9.9),
        _c(3, 9.9, 10.0, 9.4, 9.5),
        _c(4, 9.5, 9.7, 9.2, 9.55),
        _c(5, 9.55, 9.65, 9.0, 9.6),
        _c(6, 9.4, 9.6, 9.3, 9.55),
        _c(7, 9.5, 10.2, 9.4, 9.85),
    ]
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        candles, symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"
    assert signal.extra["retest_level"] == "9.7"


def test_buy_inside_yellow_high_is_no_trade() -> None:
    candles = _buy_0603()
    candles[-1] = _c(5, 9.45, 9.68, 9.40, 9.65)
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        candles, symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE


def test_no_trade_without_breakout() -> None:
    candles = _buy_0603()[:-1]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE


def test_too_few_candles() -> None:
    signal = SweepBreakoutStrategy().evaluate([], symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "not_enough_candles"


def test_buy_take_profit_is_one_point_eight_from_the_breakout() -> None:
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        _buy_0603(), symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"
    # Sweep low 9.00 to yellow top 9.70 is 0.70. Breakout close 9.85. Target is 9.85 + 0.70 * 1.8.
    assert signal.take_profit == round(9.85 + (9.70 - 9.00) * 1.8, 5)


def test_buy_invalid_when_red_unattached_from_yellow() -> None:
    candles = _buy_0603()
    candles[1] = _c(1, 12.40, 12.80, 12.20, 12.30)
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE


def test_sell_invalid_when_green_unattached_from_blue() -> None:
    candles = _sell_breakdown()
    candles[1] = _c(1, 9.10, 9.40, 9.00, 9.30)
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE


def test_buy_invalid_when_breakout_candle_gapped_below_yellow() -> None:
    candles = _buy_0603()
    candles[4] = _c(4, 8.40, 8.80, 8.30, 8.20)
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE


def test_sell_follows_last_of_two_blue_candles() -> None:
    candles = [
        _c(0, 10.0, 10.30, 9.90, 10.15),
        _c(1, 10.10, 10.40, 10.05, 10.30),
        _c(2, 10.30, 10.55, 10.25, 10.50),
        _c(3, 10.50, 10.90, 10.22, 10.40),
        _c(4, 10.40, 11.20, 10.25, 10.50),
        _c(5, 10.50, 10.55, 10.30, 10.35),
        _c(6, 10.35, 10.40, 10.05, 10.10),
    ]
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        candles, symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_blue_retest"
    assert signal.stop_loss == round(11.20 + 0.80, 5)
    assert signal.extra["blue_high"] == "10.9"


def test_buy_uses_last_yellow_before_the_sweep() -> None:
    candles = [
        _c(0, 10.0, 11.0, 8.00, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.20, 9.55),
        _c(3, 9.55, 9.65, 9.05, 9.60),
        _c(4, 9.50, 9.65, 9.40, 9.60),
        _c(5, 9.60, 9.90, 9.45, 9.85),
    ]
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        candles, symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"
    assert signal.stop_loss == round(9.05 - 0.80, 5)
    assert signal.extra["yellow_low"] == "9.2"


def test_buy_cancelled_when_breakout_is_blue_sweep() -> None:
    candles = _buy_0603()
    candles[4] = _c(4, 9.60, 9.90, 9.40, 9.50)
    candles[5] = _c(5, 9.50, 10.20, 9.40, 9.80)
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE


def test_sell_cancelled_when_breakdown_is_yellow_sweep() -> None:
    candles = _sell_breakdown()
    candles[4] = _c(4, 10.50, 10.55, 10.00, 10.20)
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE


def test_green_after_the_breakdown_does_not_cancel_the_sell() -> None:
    candles = _sell_breakdown() + [_c(6, 11.60, 12.00, 11.50, 11.80)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_blue_retest"


def test_green_wick_touching_the_blue_after_the_breakdown_stays_valid() -> None:
    candles = _sell_breakdown() + [_c(6, 10.90, 11.20, 10.90, 11.10)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_blue_retest"


def test_red_after_the_breakout_does_not_cancel_the_buy() -> None:
    candles = _buy_0603() + [_c(6, 8.70, 8.90, 8.40, 8.50)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"


def test_red_wick_touching_the_yellow_after_the_breakout_stays_valid() -> None:
    candles = _buy_0603() + [_c(6, 9.30, 9.40, 9.20, 9.25)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"


def test_green_after_the_breakdown_still_waits_for_the_blue_touch() -> None:
    candles = _sell_breakdown() + [_c(6, 10.10, 10.30, 10.05, 10.25)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_blue_retest"
    assert signal.extra["retest_level"] == "10.2"


def test_sell_take_profit_is_one_point_eight_from_the_breakdown() -> None:
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        _sell_breakdown(), symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_blue_retest"
    # Sweep high 11.20 to blue bottom 10.20 is 1.00. Breakdown close 10.10. Target is 10.10 - 1.80.
    assert signal.take_profit == round(10.10 - (11.20 - 10.20) * 1.8, 5)


def test_chart_blue_blocks_a_buy() -> None:
    colors = [""] * len(_buy_0603())
    colors[2] = "blue"
    signal = SweepBreakoutStrategy().evaluate(
        _buy_0603(), symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "blue_on_chart_no_breakout"


def test_chart_yellow_still_buys() -> None:
    colors = [""] * len(_buy_0603())
    colors[2] = "yellow"
    signal = SweepBreakoutStrategy().evaluate(
        _buy_0603(), symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"


def test_big_sell_breakout_waits_for_a_retest() -> None:
    candles = _sell_breakdown()
    candles[-1] = _c(5, 10.24, 10.26, 9.40, 9.50)
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_blue_retest"
    assert signal.extra["retest_level"] == "10.2"


def test_big_buy_breakout_waits_for_a_retest() -> None:
    candles = _buy_0603()
    candles[-1] = _c(5, 9.60, 10.30, 9.55, 10.10)
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"
    assert signal.extra["retest_level"] == "9.7"


def test_buy_target_uses_the_sweep_low_not_the_yellow_low() -> None:
    candles = [
        _c(0, 4402.0, 4403.0, 4400.5, 4400.8),
        _c(1, 4400.8, 4401.5, 4400.2, 4400.4),
        _c(2, 4400.4, 4402.0, 4400.0, 4401.0),
        _c(3, 4401.0, 4401.4, 4398.2, 4400.6),
        _c(4, 4400.6, 4401.2, 4400.4, 4401.0),
        _c(5, 4401.0, 4405.0, 4400.8, 4403.0),
    ]
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        candles, symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"
    # Yellow top 4402, sweep low 4398.2, breakout close 4403. Target is 4403 + 3.8 * 1.8.
    assert signal.take_profit == round(4403.0 + (4402.0 - 4398.2) * 1.8, 5)


def test_buy_is_invalid_when_price_reaches_the_target_before_the_retest() -> None:
    candles = _buy_0603() + [_c(6, 9.90, 11.11, 9.85, 10.20)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "yellow_target_reached_without_retest"
    assert signal.take_profit == round(9.85 + (9.70 - 9.00) * 1.8, 5)
    assert signal.extra.get("await_retest") != "1"
    assert "1:1.8 TARGET REACHED WITHOUT A RETEST" in signal.extra["stage_log"]


def test_buy_stays_valid_when_price_reaches_one_to_one_before_the_retest() -> None:
    candles = _buy_0603() + [_c(6, 9.90, 10.50, 9.85, 10.20)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.reason == "await_yellow_retest"


def test_sell_is_invalid_when_price_reaches_the_target_before_the_retest() -> None:
    candles = _sell_breakdown() + [_c(6, 10.00, 10.10, 8.30, 9.80)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "blue_target_reached_without_retest"
    assert signal.take_profit == round(10.10 - (11.20 - 10.20) * 1.8, 5)
    assert signal.extra.get("await_retest") != "1"


def test_buy_stage_log_lists_each_finished_part() -> None:
    signal = SweepBreakoutStrategy().evaluate(_buy_0603(), symbol="XAUUSD", timeframe="1M")
    text = signal.extra["stage_log"]
    assert "YELLOW DETECTED — BUY SETUP 12:" in text
    assert "2 RED CANDLES — REQUIREMENT COMPLETE 12:" in text
    assert "SWEEP COMPLETE 12:" in text
    assert "BREAKOUT COMPLETE 12:" in text
    assert "SETUP COMPLETE — WAITING FOR RETEST" in text


def test_breakout_before_the_sweep_still_waits_for_the_retest() -> None:
    candles = [
        _c(0, 10.0, 11.0, 9.50, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.20, 9.55),
        _c(3, 9.60, 10.00, 9.50, 9.90),
        _c(4, 9.90, 10.10, 9.55, 10.00),
        _c(5, 9.80, 9.90, 9.10, 9.40),
    ]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.reason == "await_yellow_retest"
    assert "BREAKOUT COMPLETE" in signal.extra["stage_log"]
    assert "SWEEP COMPLETE" in signal.extra["stage_log"]


def test_one_candle_can_sweep_and_break_out() -> None:
    candles = [
        _c(0, 10.0, 11.0, 9.50, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.20, 9.55),
        _c(3, 9.55, 9.65, 9.50, 9.60),
        _c(4, 9.60, 10.00, 9.10, 9.85),
        _c(5, 9.80, 9.90, 9.40, 9.70),
    ]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.reason == "await_yellow_retest"
    assert "SWEEP + BREAKOUT COMPLETE" in signal.extra["stage_log"]


def test_one_point_eight_target_uses_the_breakout_price() -> None:
    from trading_bot.strategy.strategy import reward_target

    # Yellow top 4500, sweep 4495, risk 500 points. Breakout 4501.5 targets 4510.5.
    assert reward_target(4501.5, 5.0, buy=True) == 4510.5
    assert reward_target(4501.5, 5.0, buy=False) == 4492.5
