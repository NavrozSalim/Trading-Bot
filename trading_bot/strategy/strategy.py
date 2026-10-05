"""User strategy: TradingView yellow/blue, then sweep, then closed breakout, then retest.

Red = bearish (close < open). Green = bullish (close > open).
The rightmost yellow on the TradingView chart starts a buy. The rightmost blue
starts a sell. If the chart is open and neither color is seen, there is no trade.
Without chart colors (unit tests only) the anchor is found from price structure.

BUY
- Two red candles in the fall before the yellow, within about 10 bars
- A green may sit between those reds, and between the reds and the yellow
- Those reds do not have to touch each other or the yellow
- The yellow candle is the sweep when its low is below the candle before it. A later candle that trades below the yellow low is also the sweep
- At least two green candles. They need not be consecutive
- The breakout is the green that closes above the yellow high. Wait for that close
- The two reds, the sweep, and the breakout can finish in any order
- One candle can complete both the sweep and the breakout
- Every breakout then waits for a later candle to touch the yellow high. Buy at that touch
- After that close, later candles may be any color. The buy stays until the touch
- A red after the last yellow, up through the breakout, must touch the yellow. Body or wick is enough
- A red that does not touch the yellow cancels the buy. Reds after the breakout are not checked
- If price reaches the 1:1.8 price before the retest, the yellow is finished and there is no buy

SELL is the mirror
- Two green candles in the rise before the blue, within about 10 bars
- A red may sit between those greens, and between the greens and the blue
- Those greens do not have to touch each other or the blue
- The blue candle is the sweep when its high is above the candle before it. A later candle that trades above the blue high is also the sweep
- At least two red candles, not necessarily consecutive
- The breakdown is the red that closes below the blue low. Wait for that close
- The two greens, the sweep, and the breakdown can finish in any order
- One candle can complete both the sweep and the breakdown
- Every breakdown then waits for a later candle to touch the blue low. Sell at that touch
- After that close, later candles may be any color. The sell stays until the touch
- A green after the last blue, up through the breakdown, must touch the blue. Body or wick is enough
- A green that does not touch the blue cancels the sell. Greens after the breakdown are not checked
- If price reaches the 1:1.8 price before the retest, the blue is finished and there is no sell

Stop is 0.80 beyond the extreme from the colored candle through the breakout or breakdown.
Risk is the yellow top minus the sweep low, or the sweep high minus the blue bottom.
The target is 1:1: the breakout or breakdown close plus or minus that risk.
A breakout at 4501.5 with 500 points of risk targets 4506.5.
The setup is cancelled if price reaches 1.8 times the risk from that close before the retest (4510.5).
Reaching 1:1 before the retest does not cancel it.
The order is sent at the retest. The breakout close is not the entry. Never touches Playwright or MT5.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

from trading_bot.strategy.signals import Signal, SignalType


class CandleLike(Protocol):
    timestamp: Any
    open: float
    high: float
    low: float
    close: float


def is_red(candle: CandleLike) -> bool:
    return candle.close < candle.open


def is_green(candle: CandleLike) -> bool:
    return candle.close > candle.open


def unattached_below(candle: CandleLike, ref: CandleLike) -> bool:
    return candle.high < ref.low


def unattached_above(candle: CandleLike, ref: CandleLike) -> bool:
    return candle.low > ref.high


def _ts(candle: CandleLike) -> Any:
    return getattr(candle, "timestamp", "1970-01-01T00:00")


REWARD_R = 1.0
CANCEL_R = 1.8


def reward_target(breakout_price: float, risk: float, *, buy: bool, ratio: float = REWARD_R) -> float:
    """Price `ratio` times the sweep distance from the breakout close. The 0.80 stop is not in the distance."""
    distance = risk * ratio
    if buy:
        return round(breakout_price + distance, 5)
    return round(breakout_price - distance, 5)


def is_yellow_sweep_bar(candle: CandleLike, previous: CandleLike) -> bool:
    """Sweeps the prior low and closes back above it. Treated as a yellow breakout bar."""
    return candle.low < previous.low and candle.close > previous.low


def is_blue_sweep_bar(candle: CandleLike, previous: CandleLike) -> bool:
    """Sweeps the prior high and closes back below it. Treated as a blue breakout bar."""
    return candle.high > previous.high and candle.close < previous.high


def body_outside_fraction(candle: CandleLike, edge: float, *, side: str) -> float:
    """Share of the candle body past the yellow high (buy) or blue low (sell)."""
    top = max(candle.open, candle.close)
    bottom = min(candle.open, candle.close)
    body = top - bottom
    if body <= 0:
        return 0.0
    if side == "BUY":
        outside = max(0.0, top - edge)
    else:
        outside = max(0.0, edge - bottom)
    return min(1.0, outside / body)


def _buy_sweep_at(candles: Sequence[CandleLike], index: int, *, include_own: bool) -> int | None:
    """A later candle below the yellow low, or the yellow itself when its low is below the candle before it.

    The yellow's own low counts only for the painted candle. A price-only scan
    still needs a later candle, so an ordinary red is not treated as a yellow.
    """
    yellow = candles[index]
    later = next((i for i in range(index + 1, len(candles)) if candles[i].low < yellow.low), None)
    if later is not None:
        return later
    if include_own and index > 0 and yellow.low < candles[index - 1].low:
        return index
    return None


def _sell_sweep_at(candles: Sequence[CandleLike], index: int, *, include_own: bool) -> int | None:
    """A later candle above the blue high, or the blue itself when its high is above the candle before it."""
    blue = candles[index]
    later = next((i for i in range(index + 1, len(candles)) if candles[i].high > blue.high), None)
    if later is not None:
        return later
    if include_own and index > 0 and blue.high > candles[index - 1].high:
        return index
    return None


def _two_reds_on_the_upper_side(candles: Sequence[CandleLike], index: int) -> list[CandleLike]:
    """Two reds in the 10 candles before the yellow. They do not have to touch it."""
    reds = [candle for candle in candles[max(0, index - 10) : index] if is_red(candle)]
    if len(reds) < 2:
        return []
    return reds[-2:]


def _two_greens_on_the_lower_side(candles: Sequence[CandleLike], index: int) -> list[CandleLike]:
    """Two greens in the 10 candles before the blue. They do not have to touch it."""
    greens = [candle for candle in candles[max(0, index - 10) : index] if is_green(candle)]
    if len(greens) < 2:
        return []
    return greens[-2:]


def _clock(candle: CandleLike) -> str:
    from trading_bot.market.match import server_to_chart_time

    stamp = getattr(candle, "timestamp", None)
    if hasattr(stamp, "strftime"):
        shown = server_to_chart_time(stamp) if hasattr(stamp, "tzinfo") else stamp
        return str(shown.strftime("%H:%M"))
    match = re.search(r"(\d{2}:\d{2})", str(stamp or ""))
    return match.group(1) if match else ""


def _timed(label: str, *candles: CandleLike) -> str:
    clocks = [clock for clock in (_clock(candle) for candle in candles) if clock]
    if not clocks:
        return label
    return f"{label} {', '.join(clocks)}"


def _latest_paint(colors: Sequence[str] | None, count: int) -> tuple[str, int] | None:
    """The rightmost yellow or blue on the chart. That candle chooses buy or sell."""
    if colors is None:
        return None
    marks = list(colors[-count:]) if len(colors) >= count else [""] * (count - len(colors)) + list(colors)
    for index in range(len(marks) - 1, -1, -1):
        if marks[index] in ("yellow", "blue"):
            return marks[index], index
    return None


MAX_SWEEP_CLUSTER = 3


def _buy_stage_log(
    candles: Sequence[CandleLike],
    *,
    yellow_at: int,
    reds: Sequence[CandleLike],
    sweep_at: int | None,
    breakout_at: int | None,
    gap_at: int | None,
    missed_at: int | None,
) -> str:
    lines = [_timed("YELLOW DETECTED — BUY SETUP", candles[yellow_at])]
    if reds:
        lines.append(_timed("2 RED CANDLES — REQUIREMENT COMPLETE", *reds))
    if sweep_at is not None and sweep_at == breakout_at:
        lines.append(_timed("SWEEP + BREAKOUT COMPLETE", candles[sweep_at]))
    else:
        if sweep_at is not None:
            lines.append(_timed("SWEEP COMPLETE", candles[sweep_at]))
        if breakout_at is not None:
            lines.append(_timed("BREAKOUT COMPLETE", candles[breakout_at]))
    if gap_at is not None:
        lines.append(_timed("SETUP INVALID — RED DOES NOT TOUCH THE YELLOW", candles[gap_at]))
    elif missed_at is not None:
        lines.append(_timed("SETUP INVALID — 1:1.8 PRICE REACHED WITHOUT A RETEST", candles[missed_at]))
    elif reds and sweep_at is not None and breakout_at is not None:
        lines.append("SETUP COMPLETE — WAITING FOR RETEST")
    return " | ".join(lines)


def _sell_stage_log(
    candles: Sequence[CandleLike],
    *,
    blue_at: int,
    greens: Sequence[CandleLike],
    sweep_at: int | None,
    breakdown_at: int | None,
    gap_at: int | None,
    missed_at: int | None,
) -> str:
    lines = [_timed("BLUE DETECTED — SELL SETUP", candles[blue_at])]
    if greens:
        lines.append(_timed("2 GREEN CANDLES — REQUIREMENT COMPLETE", *greens))
    if sweep_at is not None and sweep_at == breakdown_at:
        lines.append(_timed("SWEEP + BREAKDOWN COMPLETE", candles[sweep_at]))
    else:
        if sweep_at is not None:
            lines.append(_timed("SWEEP COMPLETE", candles[sweep_at]))
        if breakdown_at is not None:
            lines.append(_timed("BREAKDOWN COMPLETE", candles[breakdown_at]))
    if gap_at is not None:
        lines.append(_timed("SETUP INVALID — GREEN DOES NOT TOUCH THE BLUE", candles[gap_at]))
    elif missed_at is not None:
        lines.append(_timed("SETUP INVALID — 1:1.8 PRICE REACHED WITHOUT A RETEST", candles[missed_at]))
    elif greens and sweep_at is not None and breakdown_at is not None:
        lines.append("SETUP COMPLETE — WAITING FOR RETEST")
    return " | ".join(lines)


class SweepBreakoutStrategy:
    """Closed-candle setup. Entry is the later retest touch, not the breakout close."""

    def __init__(self, *, sl_offset: float = 0.80, tp_rr: float = 0.0) -> None:
        self.sl_offset = sl_offset
        self.tp_rr = tp_rr

    def evaluate(
        self,
        candles: Sequence[Any],
        *,
        symbol: str,
        timeframe: str,
        colors: Sequence[str] | None = None,
    ) -> Signal:
        if len(candles) < 5:
            return self._none(symbol, timeframe, candles, "not_enough_candles")

        anchor = _latest_paint(colors, len(candles)) if colors is not None else None
        if colors is not None and anchor is None:
            return self._none(symbol, timeframe, candles, "no_yellow_or_blue_on_chart")

        if anchor is None or anchor[0] == "yellow":
            buy = self._buy_from(candles, only_index=None if anchor is None else anchor[1])
            if buy is not None:
                sl, extra = buy
                kind = SignalType.BUY
                reason = "yellow_low_sweep_two_green_breakout"
                if extra.get("missed_target") == "1":
                    kind = SignalType.NO_TRADE
                    reason = "yellow_target_reached_without_retest"
                elif extra.get("setup_invalid") == "1":
                    kind = SignalType.NO_TRADE
                    reason = "yellow_on_chart_no_breakout"
                elif extra.get("await_retest") == "1":
                    kind = SignalType.NO_TRADE
                    reason = "await_yellow_retest"
                elif extra.get("stage_log"):
                    kind = SignalType.NO_TRADE
                    reason = "yellow_on_chart_no_breakout"
                return self._signal(kind, reason, symbol, timeframe, candles[-1], sl, extra)
            if anchor is not None:
                return self._none(symbol, timeframe, candles, "yellow_on_chart_no_breakout")
        if anchor is None or anchor[0] == "blue":
            sell = self._sell_from(candles, only_index=None if anchor is None else anchor[1])
            if sell is not None:
                sl, extra = sell
                kind = SignalType.SELL
                reason = "blue_high_sweep_two_red_breakout"
                if extra.get("missed_target") == "1":
                    kind = SignalType.NO_TRADE
                    reason = "blue_target_reached_without_retest"
                elif extra.get("setup_invalid") == "1":
                    kind = SignalType.NO_TRADE
                    reason = "blue_on_chart_no_breakout"
                elif extra.get("await_retest") == "1":
                    kind = SignalType.NO_TRADE
                    reason = "await_blue_retest"
                elif extra.get("stage_log"):
                    kind = SignalType.NO_TRADE
                    reason = "blue_on_chart_no_breakout"
                return self._signal(kind, reason, symbol, timeframe, candles[-1], sl, extra)
            if anchor is not None:
                return self._none(symbol, timeframe, candles, "blue_on_chart_no_breakout")
        return self._none(symbol, timeframe, candles, "no_sweep_breakout_pattern")

    def _buy_from(
        self, candles: Sequence[CandleLike], *, only_index: int | None = None
    ) -> tuple[float, dict[str, str]] | None:
        if len(candles) < 6:
            return None
        indexes = [only_index] if only_index is not None else range(len(candles) - 4, 1, -1)
        for y in indexes:
            if y is None or y < 2 or y >= len(candles):
                continue
            if only_index is None and y > len(candles) - 4:
                continue
            yellow = candles[y]
            reds = _two_reds_on_the_upper_side(candles, y)
            sweep_at = _buy_sweep_at(candles, y, include_own=only_index is not None)
            breakout_at = None
            for i in range(y + 1, len(candles)):
                if not (is_green(candles[i]) and candles[i].close > yellow.high):
                    continue
                greens = sum(1 for c in candles[y + 1 : i + 1] if is_green(c))
                if greens >= 2:
                    breakout_at = i
                    break
            gap_end = breakout_at + 1 if breakout_at is not None else len(candles)
            gap_at = next(
                (
                    i
                    for i in range(y + 1, gap_end)
                    if is_red(candles[i]) and unattached_below(candles[i], yellow)
                ),
                None,
            )
            ready = bool(reds) and sweep_at is not None and breakout_at is not None and gap_at is None
            sl = 0.0
            extra: dict[str, str] = {
                "pattern": "buy_yellow_sweep",
                "yellow_low": str(yellow.low),
                "yellow_high": str(yellow.high),
                "last_yellow": "1",
                "retest_level": str(yellow.high),
                "anchor_time": str(_ts(yellow)),
                "setup_clock": _clock(yellow),
                "stage_log": _buy_stage_log(
                    candles,
                    yellow_at=y,
                    reds=reds,
                    sweep_at=sweep_at,
                    breakout_at=breakout_at,
                    gap_at=gap_at,
                    missed_at=None,
                ),
            }
            if not ready:
                if only_index is None:
                    continue
                extra["setup_invalid"] = "1" if gap_at is not None else "0"
                return sl, extra
            end = max(sweep_at, breakout_at)
            window = candles[y : end + 1]
            after_low = min(c.low for c in window)
            sweep_candle = min(window, key=lambda candle: candle.low)
            if after_low >= yellow.high:
                if only_index is None:
                    continue
                return sl, extra
            sl = round(min(after_low, yellow.low) - self.sl_offset, 5)
            breakout_close = float(candles[breakout_at].close)
            tp = reward_target(breakout_close, yellow.high - after_low, buy=True)
            cancel = reward_target(breakout_close, yellow.high - after_low, buy=True, ratio=CANCEL_R)
            missed_at = next(
                (i for i in range(breakout_at, len(candles)) if candles[i].high >= cancel),
                None,
            )
            extra.update(
                {
                    "sweep_low": str(after_low),
                    "sweep_clock": _clock(sweep_candle),
                    "breakout_close": str(breakout_close),
                    "breakout_clock": _clock(candles[breakout_at]),
                    "stop_loss": str(sl),
                    "take_profit": str(tp),
                    "cancel_price": str(cancel),
                    "stage_log": _buy_stage_log(
                        candles,
                        yellow_at=y,
                        reds=reds,
                        sweep_at=sweep_at,
                        breakout_at=breakout_at,
                        gap_at=None,
                        missed_at=missed_at,
                    ),
                }
            )
            missed = missed_at is not None
            if missed:
                extra["missed_target"] = "1"
            else:
                extra["await_retest"] = "1"
            return sl, extra
        return None

    def _sell_from(
        self, candles: Sequence[CandleLike], *, only_index: int | None = None
    ) -> tuple[float, dict[str, str]] | None:
        if len(candles) < 6:
            return None
        indexes = [only_index] if only_index is not None else range(len(candles) - 4, 1, -1)
        for b in indexes:
            if b is None or b < 2 or b >= len(candles):
                continue
            if only_index is None and b >= len(candles) - 3:
                continue
            blue = candles[b]
            greens = _two_greens_on_the_lower_side(candles, b)
            sweep_at = _sell_sweep_at(candles, b, include_own=only_index is not None)
            breakdown_at = None
            for i in range(b + 1, len(candles)):
                if not (is_red(candles[i]) and candles[i].close < blue.low):
                    continue
                reds = sum(1 for c in candles[b + 1 : i + 1] if is_red(c))
                if reds >= 2:
                    breakdown_at = i
                    break
            gap_end = breakdown_at + 1 if breakdown_at is not None else len(candles)
            gap_at = next(
                (
                    i
                    for i in range(b + 1, gap_end)
                    if is_green(candles[i]) and unattached_above(candles[i], blue)
                ),
                None,
            )
            ready = (
                bool(greens) and sweep_at is not None and breakdown_at is not None and gap_at is None
            )
            sl = 0.0
            extra: dict[str, str] = {
                "pattern": "sell_blue_sweep",
                "blue_high": str(blue.high),
                "blue_low": str(blue.low),
                "last_blue": "1",
                "retest_level": str(blue.low),
                "anchor_time": str(_ts(blue)),
                "setup_clock": _clock(blue),
                "stage_log": _sell_stage_log(
                    candles,
                    blue_at=b,
                    greens=greens,
                    sweep_at=sweep_at,
                    breakdown_at=breakdown_at,
                    gap_at=gap_at,
                    missed_at=None,
                ),
            }
            if not ready:
                if only_index is None:
                    continue
                extra["setup_invalid"] = "1" if gap_at is not None else "0"
                return sl, extra
            end = max(sweep_at, breakdown_at)
            window = candles[b : end + 1]
            after_high = max(c.high for c in window)
            sweep_candle = max(window, key=lambda candle: candle.high)
            if after_high <= blue.low:
                if only_index is None:
                    continue
                return sl, extra
            sl = round(max(after_high, blue.high) + self.sl_offset, 5)
            breakdown_close = float(candles[breakdown_at].close)
            tp = reward_target(breakdown_close, after_high - blue.low, buy=False)
            cancel = reward_target(breakdown_close, after_high - blue.low, buy=False, ratio=CANCEL_R)
            missed_at = next(
                (i for i in range(breakdown_at, len(candles)) if candles[i].low <= cancel),
                None,
            )
            extra.update(
                {
                    "sweep_high": str(after_high),
                    "sweep_clock": _clock(sweep_candle),
                    "breakout_close": str(breakdown_close),
                    "breakout_clock": _clock(candles[breakdown_at]),
                    "stop_loss": str(sl),
                    "take_profit": str(tp),
                    "cancel_price": str(cancel),
                    "stage_log": _sell_stage_log(
                        candles,
                        blue_at=b,
                        greens=greens,
                        sweep_at=sweep_at,
                        breakdown_at=breakdown_at,
                        gap_at=None,
                        missed_at=missed_at,
                    ),
                }
            )
            missed = missed_at is not None
            if missed:
                extra["missed_target"] = "1"
            else:
                extra["await_retest"] = "1"
            return sl, extra
        return None

    def _signal(
        self,
        kind: SignalType,
        reason: str,
        symbol: str,
        timeframe: str,
        close_candle: CandleLike,
        sl: float,
        extra: dict[str, str],
    ) -> Signal:
        entry = float(close_candle.close)
        raw_tp = extra.get("take_profit")
        tp = float(raw_tp) if raw_tp else None
        return Signal(
            signal=kind,
            reason=reason,
            symbol=symbol,
            timeframe=timeframe,
            candle_timestamp=_ts(close_candle),
            price=entry,
            stop_loss=sl,
            take_profit=tp,
            extra=extra,
        )

    def _none(
        self, symbol: str, timeframe: str, candles: Sequence[Any], reason: str
    ) -> Signal:
        ts = _ts(candles[-1]) if candles else "1970-01-01T00:00"
        return Signal(
            signal=SignalType.NO_TRADE,
            reason=reason,
            symbol=symbol,
            timeframe=timeframe,
            candle_timestamp=str(ts),
        )


class PlaceholderStrategy:
    """Safe fallback. Prefer SweepBreakoutStrategy."""

    def evaluate(self, candles: Sequence[Any], *, symbol: str, timeframe: str) -> Signal:
        ts = _ts(candles[-1]) if candles else "1970-01-01T00:00"
        return Signal(
            signal=SignalType.NO_TRADE,
            reason="strategy_not_configured",
            symbol=symbol,
            timeframe=timeframe,
            candle_timestamp=str(ts),
        )


def get_strategy(*, sl_offset: float = 0.80, tp_rr: float = 0.0) -> SweepBreakoutStrategy:
    return SweepBreakoutStrategy(sl_offset=sl_offset, tp_rr=tp_rr)
