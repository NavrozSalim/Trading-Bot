"""User strategy: TradingView yellow/blue, then sweep, then closed breakout, then retest.

Red = bearish (close < open). Green = bullish (close > open).
The rightmost yellow on the TradingView chart starts a buy. The rightmost blue
starts a sell. If the chart is open and neither color is seen, there is no trade.
Without chart colors (unit tests only) the anchor is found from price structure.

BUY
- Two red candles while price is falling, on the upper side of the yellow
- A green may sit between those reds and the yellow. The candles stay attached. A gap does not.
- A later candle (not required to be the next one) sweeps the yellow low
- At least two green candles. They need not be consecutive
- The breakout is the green that closes above the yellow high. Wait for that close
- The two reds, the sweep, and the breakout can finish in any order
- One candle can complete both the sweep and the breakout
- Every breakout then waits for a later candle to touch the yellow high. Buy at that touch
- After that close, later candles may be any color. The buy stays until the touch
- A red after the last yellow, up through the breakout, must touch the yellow. Body or wick is enough
- A red that does not touch the yellow cancels the buy. Reds after the breakout are not checked
- If price reaches that target before the retest, the yellow is finished and there is no buy

SELL is the mirror
- Two green candles while price is rising, on the lower side of the blue
- A red may sit between those greens and the blue. The candles stay attached. A gap does not.
- A later candle sweeps the blue high
- At least two red candles, not necessarily consecutive
- The breakdown is the red that closes below the blue low. Wait for that close
- The two greens, the sweep, and the breakdown can finish in any order
- One candle can complete both the sweep and the breakdown
- Every breakdown then waits for a later candle to touch the blue low. Sell at that touch
- After that close, later candles may be any color. The sell stays until the touch
- A green after the last blue, up through the breakdown, must touch the blue. Body or wick is enough
- A green that does not touch the blue cancels the sell. Greens after the breakdown are not checked
- If price reaches that target before the retest, the blue is finished and there is no sell

Stop is 0.80 beyond the extreme from the colored candle through the breakout or breakdown.
Take profit is the same point distance used for the lot, added beyond the entry.
While waiting, that entry is the yellow top or the blue bottom. The order uses the fill price.
The breakout close is not the entry. Never touches Playwright or MT5.
"""

from __future__ import annotations

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


def ranges_attached(a: CandleLike, b: CandleLike) -> bool:
    """True when two candles overlap or touch. A gap is un-attached."""
    return a.low <= b.high and a.high >= b.low


def unattached_below(candle: CandleLike, ref: CandleLike) -> bool:
    return candle.high < ref.low


def unattached_above(candle: CandleLike, ref: CandleLike) -> bool:
    return candle.low > ref.high


def _ts(candle: CandleLike) -> Any:
    return getattr(candle, "timestamp", "1970-01-01T00:00")


def target_from_entry(entry: float, extreme: float, *, buy: bool) -> float:
    """Lot distance measured from the fill. The extra 0.80 on the stop is not included.

    Buy target = entry + (entry - sweep low).
    Sell target = entry - (sweep high - entry).
    """
    if buy:
        return round(entry + (entry - extreme), 5)
    return round(entry - (extreme - entry), 5)


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


def _two_reds_on_the_upper_side(candles: Sequence[CandleLike], index: int) -> bool:
    """Two reds in the attached fall into the yellow. A green may sit between them."""
    yellow = candles[index]
    reds: list[CandleLike] = []
    previous = yellow
    for cursor in range(index - 1, max(-1, index - 11), -1):
        candle = candles[cursor]
        if not ranges_attached(candle, previous):
            break
        previous = candle
        if is_red(candle):
            reds.append(candle)
        if len(reds) >= 2 and any(red.high >= yellow.high for red in reds):
            return True
    return False


def _two_greens_on_the_lower_side(candles: Sequence[CandleLike], index: int) -> bool:
    """Two greens in the attached rise into the blue. A red may sit between them."""
    blue = candles[index]
    greens: list[CandleLike] = []
    previous = blue
    for cursor in range(index - 1, max(-1, index - 11), -1):
        candle = candles[cursor]
        if not ranges_attached(candle, previous):
            break
        previous = candle
        if is_green(candle):
            greens.append(candle)
        if len(greens) >= 2 and any(green.low <= blue.low for green in greens):
            return True
    return False


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
    *,
    reds_ready: bool,
    sweep_at: int | None,
    breakout_at: int | None,
    gapped: bool,
    missed: bool,
) -> str:
    lines = ["YELLOW DETECTED — BUY SETUP"]
    if reds_ready:
        lines.append("2 RED CANDLES — REQUIREMENT COMPLETE")
    if sweep_at is not None and sweep_at == breakout_at:
        lines.append("SWEEP + BREAKOUT COMPLETE")
    else:
        if sweep_at is not None:
            lines.append("SWEEP COMPLETE")
        if breakout_at is not None:
            lines.append("BREAKOUT COMPLETE")
    if gapped:
        lines.append("SETUP INVALID — RED DOES NOT TOUCH THE YELLOW")
    elif missed:
        lines.append("SETUP INVALID — TARGET REACHED WITHOUT A RETEST")
    elif reds_ready and sweep_at is not None and breakout_at is not None:
        lines.append("SETUP COMPLETE — WAITING FOR RETEST")
    return " | ".join(lines)


def _sell_stage_log(
    *,
    greens_ready: bool,
    sweep_at: int | None,
    breakdown_at: int | None,
    gapped: bool,
    missed: bool,
) -> str:
    lines = ["BLUE DETECTED — SELL SETUP"]
    if greens_ready:
        lines.append("2 GREEN CANDLES — REQUIREMENT COMPLETE")
    if sweep_at is not None and sweep_at == breakdown_at:
        lines.append("SWEEP + BREAKDOWN COMPLETE")
    else:
        if sweep_at is not None:
            lines.append("SWEEP COMPLETE")
        if breakdown_at is not None:
            lines.append("BREAKDOWN COMPLETE")
    if gapped:
        lines.append("SETUP INVALID — GREEN DOES NOT TOUCH THE BLUE")
    elif missed:
        lines.append("SETUP INVALID — TARGET REACHED WITHOUT A RETEST")
    elif greens_ready and sweep_at is not None and breakdown_at is not None:
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
            reds_ready = _two_reds_on_the_upper_side(candles, y)
            sweep_at = next(
                (i for i in range(y + 1, len(candles)) if candles[i].low < yellow.low),
                None,
            )
            breakout_at = None
            for i in range(y + 1, len(candles)):
                if not (is_green(candles[i]) and candles[i].close > yellow.high):
                    continue
                greens = sum(1 for c in candles[y + 1 : i + 1] if is_green(c))
                if greens >= 2:
                    breakout_at = i
                    break
            gap_end = breakout_at + 1 if breakout_at is not None else len(candles)
            gapped = any(
                is_red(c) and unattached_below(c, yellow) for c in candles[y + 1 : gap_end]
            )
            ready = reds_ready and sweep_at is not None and breakout_at is not None and not gapped
            sl = 0.0
            extra: dict[str, str] = {
                "pattern": "buy_yellow_sweep",
                "yellow_low": str(yellow.low),
                "yellow_high": str(yellow.high),
                "last_yellow": "1",
                "retest_level": str(yellow.high),
                "anchor_time": str(_ts(yellow)),
                "stage_log": _buy_stage_log(
                    reds_ready=reds_ready,
                    sweep_at=sweep_at,
                    breakout_at=breakout_at,
                    gapped=gapped,
                    missed=False,
                ),
            }
            if not ready:
                if only_index is None:
                    continue
                extra["setup_invalid"] = "1" if gapped else "0"
                return sl, extra
            end = max(sweep_at, breakout_at)
            after = candles[y + 1 : end + 1]
            after_low = min(c.low for c in after)
            if after_low >= yellow.high:
                if only_index is None:
                    continue
                return sl, extra
            sl = round(min(after_low, yellow.low) - self.sl_offset, 5)
            tp = target_from_entry(yellow.high, after_low, buy=True)
            missed = any(c.high >= tp for c in candles[breakout_at:])
            extra.update(
                {
                    "sweep_low": str(after_low),
                    "stop_loss": str(sl),
                    "take_profit": str(tp),
                    "stage_log": _buy_stage_log(
                        reds_ready=True,
                        sweep_at=sweep_at,
                        breakout_at=breakout_at,
                        gapped=False,
                        missed=missed,
                    ),
                }
            )
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
            greens_ready = _two_greens_on_the_lower_side(candles, b)
            sweep_at = next(
                (i for i in range(b + 1, len(candles)) if candles[i].high > blue.high),
                None,
            )
            breakdown_at = None
            for i in range(b + 1, len(candles)):
                if not (is_red(candles[i]) and candles[i].close < blue.low):
                    continue
                reds = sum(1 for c in candles[b + 1 : i + 1] if is_red(c))
                if reds >= 2:
                    breakdown_at = i
                    break
            gap_end = breakdown_at + 1 if breakdown_at is not None else len(candles)
            gapped = any(
                is_green(c) and unattached_above(c, blue) for c in candles[b + 1 : gap_end]
            )
            ready = (
                greens_ready and sweep_at is not None and breakdown_at is not None and not gapped
            )
            sl = 0.0
            extra: dict[str, str] = {
                "pattern": "sell_blue_sweep",
                "blue_high": str(blue.high),
                "blue_low": str(blue.low),
                "last_blue": "1",
                "retest_level": str(blue.low),
                "anchor_time": str(_ts(blue)),
                "stage_log": _sell_stage_log(
                    greens_ready=greens_ready,
                    sweep_at=sweep_at,
                    breakdown_at=breakdown_at,
                    gapped=gapped,
                    missed=False,
                ),
            }
            if not ready:
                if only_index is None:
                    continue
                extra["setup_invalid"] = "1" if gapped else "0"
                return sl, extra
            end = max(sweep_at, breakdown_at)
            after = candles[b + 1 : end + 1]
            after_high = max(c.high for c in after)
            if after_high <= blue.low:
                if only_index is None:
                    continue
                return sl, extra
            sl = round(max(after_high, blue.high) + self.sl_offset, 5)
            tp = target_from_entry(blue.low, after_high, buy=False)
            missed = any(c.low <= tp for c in candles[breakdown_at:])
            extra.update(
                {
                    "sweep_high": str(after_high),
                    "stop_loss": str(sl),
                    "take_profit": str(tp),
                    "stage_log": _sell_stage_log(
                        greens_ready=True,
                        sweep_at=sweep_at,
                        breakdown_at=breakdown_at,
                        gapped=False,
                        missed=missed,
                    ),
                }
            )
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
