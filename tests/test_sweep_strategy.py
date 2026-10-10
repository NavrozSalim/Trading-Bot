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


def test_buy_take_profit_is_one_to_one_from_the_breakout() -> None:
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        _buy_0603(), symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_yellow_retest"
    # Sweep low 9.00 to yellow top 9.70 is 0.70. Breakout close 9.85. Target 9.85 + 0.70, cancel at 9.85 + 1.26.
    assert signal.take_profit == round(9.85 + 0.70, 5)
    assert float(signal.extra["cancel_price"]) == round(9.85 + 0.70 * 1.8, 5)
    assert signal.extra["setup_clock"] == "17:02"
    assert signal.extra["sweep_clock"] == "17:03"
    assert signal.extra["breakout_close"] == "9.85"
    assert signal.extra["breakout_clock"] == "17:05"


def test_the_yellow_below_the_prior_low_is_not_the_sweep() -> None:
    """The yellow low is under the candle before it. No later candle goes under the yellow low."""
    candles = [
        _c(0, 10.0, 11.0, 9.50, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.20, 9.55),
        _c(3, 9.55, 9.65, 9.30, 9.60),
        _c(4, 9.60, 9.65, 9.25, 9.40),
        _c(5, 9.40, 10.20, 9.40, 9.85),
    ]
    colors = [""] * len(candles)
    colors[2] = "yellow"
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.reason == "yellow_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "1"
    assert "SETUP INVALID BECAUSE NO LOW SWEEP 17:05" in signal.extra["stage_log"]


def test_the_blue_above_the_prior_high_is_not_the_sweep() -> None:
    candles = [
        _c(0, 10.0, 10.20, 9.90, 10.15),
        _c(1, 10.15, 10.40, 10.10, 10.30),
        _c(2, 10.30, 10.90, 10.20, 10.25),
        _c(3, 10.25, 10.70, 10.15, 10.20),
        _c(4, 10.20, 10.30, 10.00, 10.05),
        _c(5, 10.05, 10.10, 9.80, 9.90),
    ]
    colors = [""] * len(candles)
    colors[2] = "blue"
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.reason == "blue_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "1"
    assert "SETUP INVALID BECAUSE NO HIGH SWEEP 17:04" in signal.extra["stage_log"]


def test_reds_before_the_yellow_do_not_have_to_touch() -> None:
    candles = _buy_0603()
    candles[0] = _c(0, 14.0, 14.2, 13.5, 13.6)
    colors = [""] * len(candles)
    colors[2] = "yellow"
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.reason == "await_yellow_retest"


def test_greens_before_the_blue_do_not_have_to_touch() -> None:
    candles = _sell_breakdown()
    candles[0] = _c(0, 8.0, 8.2, 7.5, 8.1)
    colors = [""] * len(candles)
    colors[2] = "blue"
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.reason == "await_blue_retest"


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


def test_sell_take_profit_is_one_to_one_from_the_breakdown() -> None:
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        _sell_breakdown(), symbol="XAUUSD", timeframe="1M"
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "await_blue_retest"
    # Sweep high 11.20 to blue bottom 10.20 is 1.00. Breakdown close 10.10. Target 9.10, cancel at 8.30.
    assert signal.take_profit == round(10.10 - 1.00, 5)
    assert float(signal.extra["cancel_price"]) == round(10.10 - 1.80, 5)


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
    # Yellow top 4402, sweep low 4398.2, breakout close 4403. Target is 4403 + 3.8.
    assert signal.take_profit == round(4403.0 + (4402.0 - 4398.2), 5)


def test_buy_is_invalid_when_price_reaches_the_target_before_the_retest() -> None:
    candles = _buy_0603() + [_c(6, 9.90, 11.11, 9.85, 10.20)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "yellow_target_reached_without_retest"
    assert signal.take_profit == round(9.85 + 0.70, 5)
    assert signal.extra.get("await_retest") != "1"
    assert "1:1.8 PRICE REACHED WITHOUT A RETEST" in signal.extra["stage_log"]


def test_buy_stays_valid_when_price_passes_one_to_one_before_the_retest() -> None:
    # 10.60 is past the 1:1 target 10.55 but short of the 1:1.8 cancel 11.11.
    candles = _buy_0603() + [_c(6, 9.90, 10.60, 9.85, 10.20)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.reason == "await_yellow_retest"
    assert signal.take_profit == round(9.85 + 0.70, 5)


def test_sell_is_invalid_when_price_reaches_the_target_before_the_retest() -> None:
    candles = _sell_breakdown() + [_c(6, 10.00, 10.10, 8.30, 9.80)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "blue_target_reached_without_retest"
    assert signal.take_profit == round(10.10 - 1.00, 5)
    assert signal.extra.get("await_retest") != "1"


def test_sell_stays_valid_when_price_passes_one_to_one_before_the_retest() -> None:
    # 9.00 is past the 1:1 target 9.10 but short of the 1:1.8 cancel 8.30.
    candles = _sell_breakdown() + [_c(6, 10.00, 10.10, 9.00, 9.80)]
    signal = SweepBreakoutStrategy().evaluate(candles, symbol="XAUUSD", timeframe="1M")
    assert signal.reason == "await_blue_retest"


def test_buy_stage_log_lists_each_finished_part() -> None:
    signal = SweepBreakoutStrategy().evaluate(_buy_0603(), symbol="XAUUSD", timeframe="1M")
    text = signal.extra["stage_log"]
    assert "YELLOW DETECTED — BUY SETUP 17:" in text
    assert "2 RED CANDLES — REQUIREMENT COMPLETE 17:" in text
    assert "SWEEP COMPLETE 17:" in text
    assert "BREAKOUT COMPLETE 17:" in text
    assert "SETUP COMPLETE — WAITING FOR RETEST" in text


def test_a_two_candle_breakout_without_a_low_sweep_is_invalid() -> None:
    """The breakout closed with two bullish candles. The later low does not count as the sweep."""
    candles = [
        _c(0, 10.0, 11.0, 9.50, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.45, 9.55),
        _c(3, 9.55, 9.68, 9.48, 9.62),
        _c(4, 9.62, 10.10, 9.50, 9.95),
        _c(5, 9.90, 9.95, 9.20, 9.40),
    ]
    colors = [""] * len(candles)
    colors[2] = "yellow"
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "yellow_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "1"
    assert "BREAKOUT COMPLETE 17:04" in signal.extra["stage_log"]
    assert "SETUP INVALID BECAUSE NO LOW SWEEP 17:04" in signal.extra["stage_log"]
    assert "WAITING FOR RETEST" not in signal.extra["stage_log"]


def test_a_two_candle_breakdown_without_a_high_sweep_is_invalid() -> None:
    candles = [
        _c(0, 10.0, 10.20, 9.90, 10.15),
        _c(1, 10.15, 10.50, 10.10, 10.40),
        _c(2, 10.40, 10.45, 10.20, 10.25),
        _c(3, 10.25, 10.40, 10.22, 10.35),
        _c(4, 10.35, 10.42, 10.22, 10.28),
        _c(5, 10.28, 10.30, 10.05, 10.10),
        _c(6, 10.10, 11.40, 10.05, 10.20),
    ]
    colors = [""] * len(candles)
    colors[2] = "blue"
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "blue_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "1"
    assert "SETUP INVALID BECAUSE NO HIGH SWEEP 17:05" in signal.extra["stage_log"]
    assert "WAITING FOR RETEST" not in signal.extra["stage_log"]


def test_bearish_candles_can_sit_between_the_two_bullish_candles() -> None:
    """19:00 yellow, 19:01 sweep, 19:02 bullish under the high, then two reds, then the breakout."""
    candles = [
        _c(0, 10.0, 11.0, 9.50, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.20, 9.55),
        _c(3, 9.55, 9.60, 9.00, 9.40),
        _c(4, 9.40, 9.66, 9.35, 9.60),
        _c(5, 9.60, 9.62, 9.30, 9.40),
        _c(6, 9.40, 9.45, 9.28, 9.32),
        _c(7, 9.32, 10.10, 9.30, 9.95),
    ]
    colors = [""] * len(candles)
    colors[2] = "yellow"
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.reason == "await_yellow_retest"
    assert signal.extra["breakout_clock"] == "17:07"
    assert "BREAKOUT COMPLETE 17:07" in signal.extra["stage_log"]


def test_the_first_bullish_close_above_the_yellow_cancels_the_buy() -> None:
    """17:50 closed above the yellow high. The later green at 17:51 does not become the breakout."""
    candles = [
        _c(0, 10.0, 11.0, 9.50, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.20, 9.55),
        _c(3, 9.55, 9.60, 9.00, 9.40),
        _c(4, 9.40, 10.00, 9.35, 9.90),
        _c(5, 9.90, 10.40, 9.80, 10.20),
    ]
    colors = [""] * len(candles)
    colors[2] = "yellow"
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "yellow_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "1"
    assert "SETUP INVALID BECAUSE SINGLE CANDLE BREAKOUT 17:04" in signal.extra["stage_log"]
    assert "BREAKOUT COMPLETE" not in signal.extra["stage_log"]
    assert "WAITING FOR RETEST" not in signal.extra["stage_log"]


def test_the_first_bearish_close_below_the_blue_cancels_the_sell() -> None:
    candles = [
        _c(0, 10.0, 10.20, 9.90, 10.15),
        _c(1, 10.15, 10.40, 10.10, 10.30),
        _c(2, 10.30, 10.90, 10.20, 10.25),
        _c(3, 10.25, 11.20, 10.15, 10.40),
        _c(4, 10.40, 10.45, 10.00, 10.10),
        _c(5, 10.10, 10.15, 9.80, 9.90),
    ]
    colors = [""] * len(candles)
    colors[2] = "blue"
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "blue_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "1"
    assert "SETUP INVALID BECAUSE SINGLE CANDLE BREAKDOWN 17:04" in signal.extra["stage_log"]
    assert "BREAKDOWN COMPLETE" not in signal.extra["stage_log"]
    assert "WAITING FOR RETEST" not in signal.extra["stage_log"]


def _yellow_green_sweep() -> list[Candle]:
    """17:03 is green, sweeps the yellow low, and already closes above the yellow high."""
    return [
        _c(0, 10.0, 11.0, 9.50, 9.60),
        _c(1, 9.60, 10.0, 9.40, 9.50),
        _c(2, 9.50, 9.70, 9.20, 9.55),
        _c(3, 9.55, 9.90, 9.10, 9.85),
        _c(4, 9.85, 9.88, 9.60, 9.65),
        _c(5, 9.65, 9.70, 9.55, 9.60),
    ]


def _paint(candles: list[Candle], **marks: str) -> list[str]:
    colors = [""] * len(candles)
    for key, color in marks.items():
        colors[int(key[1:])] = color
    return colors


def test_a_green_sweep_closing_above_waits_for_one_more_bullish() -> None:
    candles = _yellow_green_sweep()
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="yellow")
    )
    assert signal.reason == "yellow_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "0"
    assert "SINGLE CANDLE" not in signal.extra["stage_log"]

    candles.append(_c(6, 9.60, 10.00, 9.58, 9.90))
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="yellow")
    )
    assert signal.reason == "await_yellow_retest"
    assert signal.extra["sweep_clock"] == "17:03"
    assert signal.extra["breakout_clock"] == "17:06"


def _blue_red_sweep() -> list[Candle]:
    """19:01 in the example: red, sweeps the blue high, and already closes under the blue low."""
    return [
        _c(0, 10.0, 10.20, 9.90, 10.15),
        _c(1, 10.15, 10.40, 10.10, 10.30),
        _c(2, 10.30, 10.90, 10.20, 10.25),
        _c(3, 10.25, 11.00, 10.00, 10.05),
        _c(4, 10.05, 10.15, 10.00, 10.10),
        _c(5, 10.10, 10.12, 10.02, 10.11),
    ]


def test_a_red_sweep_closing_under_waits_for_one_more_red() -> None:
    candles = _blue_red_sweep()
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="blue")
    )
    assert signal.reason == "blue_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "0"
    assert "SINGLE CANDLE" not in signal.extra["stage_log"]

    candles.append(_c(6, 10.11, 10.12, 9.90, 9.95))
    signal = SweepBreakoutStrategy(sl_offset=0.80).evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="blue")
    )
    assert signal.reason == "await_blue_retest"
    assert signal.extra["sweep_clock"] == "17:03"
    assert signal.extra["breakout_clock"] == "17:06"
    assert signal.stop_loss == round(11.00 + 0.80, 5)


def test_a_newer_blue_before_the_breakdown_replaces_the_older_blue() -> None:
    candles = [
        _c(0, 10.0, 10.20, 9.90, 10.15),
        _c(1, 10.15, 10.40, 10.10, 10.30),
        _c(2, 10.30, 10.90, 10.20, 10.25),
        _c(3, 10.25, 10.50, 10.22, 10.45),
        _c(4, 10.45, 10.60, 10.40, 10.55),
        _c(5, 10.55, 10.70, 10.45, 10.50),
        _c(6, 10.50, 10.52, 10.30, 10.35),
    ]
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="blue", m4="blue")
    )
    assert signal.reason == "await_blue_retest"
    assert signal.extra["setup_clock"] == "17:04"
    assert signal.extra["retest_level"] == "10.4"


def test_a_newer_blue_after_the_breakdown_keeps_the_older_sell() -> None:
    candles = _sell_breakdown() + [_c(6, 10.10, 10.18, 10.05, 10.15)]
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="blue", m6="blue")
    )
    assert signal.reason == "await_blue_retest"
    assert signal.extra["setup_clock"] == "17:02"
    assert signal.extra["retest_level"] == "10.2"


def _blue_then(yellow: Candle) -> list[Candle]:
    return [
        _c(0, 10.0, 10.20, 9.90, 10.15),
        _c(1, 10.15, 10.40, 10.10, 10.30),
        _c(2, 10.30, 10.90, 10.20, 10.25),
        yellow,
        _c(4, 10.15, 10.20, 10.12, 10.18),
        _c(5, 10.18, 10.22, 10.14, 10.16),
    ]


def test_a_yellow_sweeping_the_blue_high_ends_the_sell_and_starts_a_buy() -> None:
    candles = _blue_then(_c(3, 10.25, 11.00, 10.10, 10.15))
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="blue", m3="yellow")
    )
    assert signal.extra["pattern"] == "buy_yellow_sweep"
    assert signal.extra["setup_clock"] == "17:03"


def test_a_yellow_closing_under_the_blue_ends_the_sell_and_starts_a_buy() -> None:
    candles = _blue_then(_c(3, 10.25, 10.40, 9.90, 10.00))
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="blue", m3="yellow")
    )
    assert signal.extra["pattern"] == "buy_yellow_sweep"
    assert signal.extra["setup_clock"] == "17:03"


def test_a_yellow_that_does_neither_expires_both() -> None:
    candles = _blue_then(_c(3, 10.30, 10.50, 10.22, 10.28))
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="blue", m3="yellow")
    )
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "blue_on_chart_no_breakout"
    assert signal.extra["setup_invalid"] == "1"
    assert signal.extra["setup_clock"] == "17:02"
    assert "SETUP INVALID BECAUSE YELLOW CANDLE AFTER THE BLUE 17:03" in signal.extra["stage_log"]
    assert "BOTH EXPIRED" in signal.extra["stage_log"]


def test_after_both_expire_the_next_blue_starts_again() -> None:
    candles = _blue_then(_c(3, 10.30, 10.50, 10.22, 10.28))
    colors = _paint(candles, m2="blue", m3="yellow", m5="blue")
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=colors
    )
    assert signal.extra["pattern"] == "sell_blue_sweep"
    assert signal.extra["setup_clock"] == "17:05"


def test_a_blue_that_does_neither_expires_the_buy() -> None:
    candles = _buy_0603()
    candles[3] = _c(3, 9.55, 9.65, 9.25, 9.60)
    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m2="yellow", m3="blue")
    )
    assert signal.reason == "yellow_on_chart_no_breakout"
    assert "SETUP INVALID BECAUSE BLUE CANDLE AFTER THE YELLOW 17:03" in signal.extra["stage_log"]


def test_a_yellow_is_not_counted_as_a_red_before_the_next_yellow() -> None:
    candles = [
        _c(0, 9.90, 10.50, 9.85, 10.40),
        _c(1, 10.40, 10.45, 10.00, 10.10),
        _c(2, 10.10, 10.15, 9.80, 9.90),
        _c(3, 9.90, 10.00, 9.70, 9.85),
        _c(4, 9.85, 9.95, 9.60, 9.90),
        _c(5, 9.90, 10.20, 9.85, 10.10),
    ]
    plain = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m3="yellow")
    )
    assert plain.reason == "await_yellow_retest"

    signal = SweepBreakoutStrategy().evaluate(
        candles, symbol="XAUUSD", timeframe="1M", colors=_paint(candles, m1="yellow", m3="yellow")
    )
    assert signal.extra["setup_clock"] == "17:03"
    assert signal.reason != "await_yellow_retest"
    assert "2 RED CANDLES" not in signal.extra["stage_log"]


def test_target_and_cancel_use_the_breakout_price() -> None:
    from trading_bot.strategy.strategy import CANCEL_R, reward_target

    # Yellow top 4500, sweep 4495, risk 500 points. Breakout 4501.5 targets 4506.5, cancels at 4510.5.
    assert reward_target(4501.5, 5.0, buy=True) == 4506.5
    assert reward_target(4501.5, 5.0, buy=False) == 4496.5
    assert reward_target(4501.5, 5.0, buy=True, ratio=CANCEL_R) == 4510.5
    assert reward_target(4501.5, 5.0, buy=False, ratio=CANCEL_R) == 4492.5
