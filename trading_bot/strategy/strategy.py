"""User strategy: TradingView yellow/blue, then sweep, then closed breakout, then retest.

Red = bearish (close < open). Green = bullish (close > open).
A yellow is a bearish candle and a blue is a bullish candle.
A yellow doji or a blue doji is invalid. The close must not equal the open.
A yellow starts a buy. A blue starts a sell. If the chart is open and neither
color is seen, there is no trade.
Without chart colors (unit tests only) the anchor is found from price structure.

WHICH YELLOW OR BLUE
- A newer candle of the same color before the breakout replaces the older one
- After the breakout is complete, the older one stays until its retest. A newer one waits
- A retest candle that is itself yellow still buys the original yellow. The sell side is the mirror
- A yellow after a blue, before the breakdown:
  sweeps the blue high, or closes under the blue low: the sell ends and the yellow starts a buy
  does neither: the blue and that yellow both expire. The next yellow or blue starts again
- A blue after a yellow, before the breakout, is the mirror

BUY
- Two red candles in the 15 candles before the yellow
- A green may sit between those reds, and between the reds and the yellow
- A yellow is not counted as one of those reds. That exclusion is only for this count
- Those reds do not have to touch each other or the yellow
- Then a later candle sweeps the yellow low. Wick or body is enough. The yellow itself is not the sweep
- Then at least two bullish candles after the yellow. One may come before the low sweep and the other after it. Bearish candles may sit between them
- A green sweep candle counts as the first of the two when it does not close above the yellow high
- The breakout is a bullish close above the yellow high after the low has been swept, and it is at least the second bullish candle
- The breakout body must be bigger than 90 points and bigger than the upper wick. A weaker close stays open
- A bullish close above the yellow high before that sweep, or the first bullish close above the high, stays open
- From that close, 1:1 is the yellow high plus the distance from the yellow high down to the sweep low. Before any sweep, the yellow low is used
- A wick to that 1:1 before a red candle closes at or below the yellow high finishes the setup. The wick counts even when that candle closes back inside
- Only a red candle closing at or below the yellow high keeps it alive. A green candle closing there does not
- A candle that sweeps the yellow low and also closes above the yellow high does not finish the buy, even when another green candle already stayed below the high
- A bearish candle must close back at or below the yellow high. Then a later bullish close above the yellow high is the breakout
- After the red close, the next green close above the yellow high is the breakout once the low was swept by an earlier candle and two greens exist
- If that next close is still before the sweep, the same 1:1 wait starts again
- Every completed breakout then waits for a later candle to touch the yellow high. Buy at that touch
- After that close, later candles may be any color. The buy stays until the touch
- A red after the last yellow, up through the breakout, must touch the yellow. Body or wick is enough
- A red that does not touch the yellow cancels the buy. Reds after the breakout are not checked
- If price reaches the 1:1.8 price before the retest, the yellow is finished and there is no buy

SELL is the mirror
- Two green candles in the 15 candles before the blue
- A red may sit between those greens, and between the greens and the blue
- A blue is not counted as one of those greens. That exclusion is only for this count
- Those greens do not have to touch each other or the blue
- Then a later candle sweeps the blue high. Wick or body is enough. The blue itself is not the sweep
- Then at least two bearish candles after the blue. One may come before the high sweep and the other after it. Bullish candles may sit between them
- A red sweep candle counts as the first of the two when it does not close under the blue low
- The breakdown is a bearish close under the blue low after the high has been swept, and it is at least the second bearish candle
- The breakdown body must be bigger than 90 points and bigger than the lower wick. A weaker close stays open
- A bearish close under the blue low before that sweep, or the first bearish close under the low, stays open
- From that close, 1:1 is the blue low minus the distance from the sweep high down to the blue low. Before any sweep, the blue high is used
- A wick to that 1:1 before a green candle closes at or above the blue low finishes the setup
- Only a green candle closing at or above the blue low keeps it alive. A red candle closing there does not
- A candle that sweeps the blue high and also closes under the blue low does not finish the sell, even when another red candle already stayed above the low
- A bullish candle must close back at or above the blue low. Then a later bearish close under the blue low is the breakdown
- After the green close, the next red close under the blue low is the breakdown once the high was swept by an earlier candle and two reds exist
- If that next close is still before the sweep, the same 1:1 wait starts again
- Every completed breakdown then waits for a later candle to touch the blue low. Sell at that touch
- After that close, later candles may be any color. The sell stays until the touch
- A green after the last blue, up through the breakdown, must touch the blue. Body or wick is enough
- A green that does not touch the blue cancels the sell. Greens after the breakdown are not checked
- If price reaches the 1:1.8 price before the retest, the blue is finished and there is no sell

Stop is 0.80 beyond the extreme from the colored candle through the breakout or breakdown.
The lot risks the fixed dollars to that stop, including the 0.80.
The target is 1:1 from the yellow high or the blue low, plus 0.50. It is not measured from the breakout close.
A yellow high at 4500 with a sweep low at 4495 targets 4505.50.
The setup is cancelled if price reaches 1.8 times the sweep distance from the yellow high before the retest (4509). The extra 0.50 is not part of that cancel price.
Reaching the 1:1 target after the breakout is complete does not cancel it.
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


def is_doji(candle: CandleLike) -> bool:
    """Close equals open. A yellow or blue doji is not a setup."""
    return candle.close == candle.open


def unattached_below(candle: CandleLike, ref: CandleLike) -> bool:
    return candle.high < ref.low


def unattached_above(candle: CandleLike, ref: CandleLike) -> bool:
    return candle.low > ref.high


def _ts(candle: CandleLike) -> Any:
    return getattr(candle, "timestamp", "1970-01-01T00:00")


REWARD_R = 1.0
CANCEL_R = 1.8
TARGET_EXTRA = 0.50


def reward_target(anchor_price: float, risk: float, *, buy: bool, ratio: float = REWARD_R) -> float:
    """Price `ratio` times the sweep distance from the yellow high or blue low. The 0.80 stop is not in the distance."""
    distance = risk * ratio
    if buy:
        return round(anchor_price + distance, 5)
    return round(anchor_price - distance, 5)


def order_target(anchor_price: float, risk: float, *, buy: bool) -> float:
    """The order target: 1:1 from the yellow high or blue low, plus 0.50."""
    base = reward_target(anchor_price, risk, buy=buy)
    if buy:
        return round(base + TARGET_EXTRA, 5)
    return round(base - TARGET_EXTRA, 5)


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


class _Parts:
    """Sweep, breakout, and the candle that finished the setup early, as candle indexes."""

    def __init__(
        self,
        sweep_at: int | None = None,
        breakout_at: int | None = None,
        early_at: int | None = None,
        no_sweep_at: int | None = None,
        awaiting_return_at: int | None = None,
        weak_at: int | None = None,
    ) -> None:
        self.sweep_at = sweep_at
        self.breakout_at = breakout_at
        self.early_at = early_at
        self.no_sweep_at = no_sweep_at
        self.awaiting_return_at = awaiting_return_at
        self.weak_at = weak_at

    @property
    def finished(self) -> bool:
        return self.early_at is not None or self.no_sweep_at is not None


def _outside_limit(anchor: CandleLike, extreme: float | None, *, buy: bool) -> float:
    """1:1 from the yellow high or blue low. Before a sweep, the other end of that candle is used."""
    if buy:
        ref = anchor.low if extreme is None else extreme
        return round(anchor.high + (anchor.high - ref), 5)
    ref = anchor.high if extreme is None else extreme
    return round(anchor.low - (ref - anchor.low), 5)


def _wicked_outside_limit(candle: CandleLike, limit: float, *, buy: bool) -> bool:
    if buy:
        return candle.high >= limit
    return candle.low <= limit


# 90 points on gold. A smaller body, or a body no bigger than the breakout wick, is not a breakout.
MIN_BREAKOUT_BODY = 0.90


def _breakout_is_weak(candle: CandleLike, *, buy: bool) -> bool:
    """Body of 90 points or less, or a body that is not bigger than the breakout wick.

    One point is 0.01. Prices are rounded to that before the check, so a close
    of 4000.90 after an open of 4000.00 is 90 points.
    """
    body = round(abs(candle.close - candle.open), 2)
    if body <= MIN_BREAKOUT_BODY:
        return True
    if buy:
        wick = round(candle.high - max(candle.open, candle.close), 2)
    else:
        wick = round(min(candle.open, candle.close) - candle.low, 2)
    return body <= wick


def _closed_back_inside(candle: CandleLike, anchor: CandleLike, *, buy: bool) -> bool:
    """A red M1 close at or below the yellow high, or a green M1 close at or above the blue low."""
    if buy:
        return is_red(candle) and candle.close <= anchor.high
    return is_green(candle) and candle.close >= anchor.low


def _setup_parts(candles: Sequence[CandleLike], at: int, *, buy: bool) -> _Parts:
    """Sweep, then two candles in the breakout direction, then a close past the level.

    A close past the level before the sweep, or the first close past the level,
    stays open. It dies if a wick reaches 1:1 before price closes back inside.
    A sweep that closes back outside does not finish it. The next close past the
    level, once the sweep exists and two trend candles exist, is the breakout.
    """
    anchor = candles[at]
    with_trend = is_green if buy else is_red
    sweep_at: int | None = None
    extreme: float | None = None
    count = 0
    outside = False
    awaiting_return_at: int | None = None
    weak_at: int | None = None
    for index in range(at + 1, len(candles)):
        candle = candles[index]
        swept = candle.low < anchor.low if buy else candle.high > anchor.high
        if swept:
            if sweep_at is None:
                sweep_at = index
            if buy:
                extreme = candle.low if extreme is None else min(extreme, candle.low)
            else:
                extreme = candle.high if extreme is None else max(extreme, candle.high)
        if outside:
            if _wicked_outside_limit(candle, _outside_limit(anchor, extreme, buy=buy), buy=buy):
                return _Parts(sweep_at=sweep_at, early_at=index)
            if _closed_back_inside(candle, anchor, buy=buy):
                outside = False
                awaiting_return_at = None
                continue
            if with_trend(candle):
                count += 1
            continue
        if not with_trend(candle):
            continue
        count += 1
        past = candle.close > anchor.high if buy else candle.close < anchor.low
        if not past:
            continue
        # A candle that creates the sweep and also closes through the level is only
        # the first break. An opposite candle must close back inside, then a later
        # candle in the breakout direction must close through the level.
        weak = _breakout_is_weak(candle, buy=buy)
        if sweep_at is not None and count >= 2 and sweep_at != index and not weak:
            return _Parts(sweep_at=sweep_at, breakout_at=index)
        if _wicked_outside_limit(candle, _outside_limit(anchor, extreme, buy=buy), buy=buy):
            return _Parts(sweep_at=sweep_at, early_at=index)
        outside = True
        awaiting_return_at = index
        weak_at = index if weak else None
    return _Parts(
        sweep_at=sweep_at,
        awaiting_return_at=awaiting_return_at if outside else None,
        weak_at=weak_at if outside else None,
    )


def _retested(candles: Sequence[CandleLike], parts: _Parts, level: float, *, buy: bool) -> bool:
    if parts.breakout_at is None:
        return False
    after = candles[parts.breakout_at + 1 :]
    if buy:
        return any(candle.low <= level for candle in after)
    return any(candle.high >= level for candle in after)


LOOKBACK_BARS = 15


def _two_reds_on_the_upper_side(
    candles: Sequence[CandleLike], index: int, marks: Sequence[str] | None = None
) -> list[CandleLike]:
    """Two reds in the 15 candles before the yellow. A yellow is not one of them."""
    start = max(0, index - LOOKBACK_BARS)
    reds = [
        candle
        for offset, candle in enumerate(candles[start:index], start)
        if is_red(candle) and not (marks and marks[offset] == "yellow")
    ]
    if len(reds) < 2:
        return []
    return reds[-2:]


def _two_greens_on_the_lower_side(
    candles: Sequence[CandleLike], index: int, marks: Sequence[str] | None = None
) -> list[CandleLike]:
    """Two greens in the 15 candles before the blue. A blue is not one of them."""
    start = max(0, index - LOOKBACK_BARS)
    greens = [
        candle
        for offset, candle in enumerate(candles[start:index], start)
        if is_green(candle) and not (marks and marks[offset] == "blue")
    ]
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


def _aligned_marks(colors: Sequence[str], count: int) -> list[str]:
    """One mark per candle, right-aligned with the candles."""
    if len(colors) >= count:
        return list(colors[-count:])
    return [""] * (count - len(colors)) + list(colors)


def _takes_over(older: CandleLike, newer: CandleLike, *, older_color: str) -> bool:
    """A yellow that sweeps the blue high or closes under the blue low, or the blue mirror."""
    if older_color == "blue":
        return newer.high > older.high or newer.close < older.low
    return newer.low < older.low or newer.close > older.high


MAX_SWEEP_CLUSTER = 3


def _buy_stage_log(
    candles: Sequence[CandleLike],
    *,
    yellow_at: int,
    reds: Sequence[CandleLike],
    sweep_at: int | None,
    breakout_at: int | None,
    gap_at: int | None,
    early_at: int | None,
    missing_sweep: bool,
    missed_at: int | None,
    awaiting_return_at: int | None = None,
    weak_at: int | None = None,
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
    if early_at is not None:
        lines.append(
            _timed("SETUP INVALID — 1:1 REACHED BEFORE A RED CLOSE BACK INSIDE", candles[early_at])
        )
    elif weak_at is not None and breakout_at is None:
        lines.append(
            _timed(
                "WEAK BREAKOUT — WAITING FOR A RED CLOSE BACK INSIDE",
                candles[weak_at],
            )
        )
    elif awaiting_return_at is not None and breakout_at is None:
        lines.append(
            _timed(
                "CLOSED ABOVE THE YELLOW — WAITING FOR A RED CLOSE BACK INSIDE",
                candles[awaiting_return_at],
            )
        )
    elif missing_sweep and breakout_at is not None:
        lines.append(_timed("SETUP INVALID BECAUSE NO LOW SWEEP", candles[breakout_at]))
    elif gap_at is not None:
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
    early_at: int | None,
    missing_sweep: bool,
    missed_at: int | None,
    awaiting_return_at: int | None = None,
    weak_at: int | None = None,
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
    if early_at is not None:
        lines.append(
            _timed(
                "SETUP INVALID — 1:1 REACHED BEFORE A GREEN CLOSE BACK INSIDE",
                candles[early_at],
            )
        )
    elif weak_at is not None and breakdown_at is None:
        lines.append(
            _timed(
                "WEAK BREAKDOWN — WAITING FOR A GREEN CLOSE BACK INSIDE",
                candles[weak_at],
            )
        )
    elif awaiting_return_at is not None and breakdown_at is None:
        lines.append(
            _timed(
                "CLOSED UNDER THE BLUE — WAITING FOR A GREEN CLOSE BACK INSIDE",
                candles[awaiting_return_at],
            )
        )
    elif missing_sweep and breakdown_at is not None:
        lines.append(_timed("SETUP INVALID BECAUSE NO HIGH SWEEP", candles[breakdown_at]))
    elif gap_at is not None:
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

        marks: list[str] | None = None
        anchor: tuple[str, int] | None = None
        if colors is not None:
            marks = _aligned_marks(colors, len(candles))
            anchor, expired = self._follow(candles, marks)
            if anchor is None and expired is None:
                return self._none(symbol, timeframe, candles, "no_yellow_or_blue_on_chart")
            if anchor is None and expired is not None:
                return self._both_expired(candles, expired, symbol, timeframe)

        if anchor is None or anchor[0] == "yellow":
            buy = self._buy_from(
                candles, only_index=None if anchor is None else anchor[1], marks=marks
            )
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
            sell = self._sell_from(
                candles, only_index=None if anchor is None else anchor[1], marks=marks
            )
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

    def _follow(
        self, candles: Sequence[CandleLike], marks: Sequence[str]
    ) -> tuple[tuple[str, int] | None, tuple[str, int, str, int] | None]:
        """The yellow or blue the bot follows, or the pair that expired together."""
        active: tuple[str, int] | None = None
        expired: tuple[str, int, str, int] | None = None
        for index, color in enumerate(marks):
            if color not in ("yellow", "blue"):
                continue
            if active is None:
                active, expired = (color, index), None
                continue
            older_color, older_at = active
            state = self._older_state(candles[:index], older_color, older_at, marks)
            if state == "pending":
                continue
            if state == "finished" or color == older_color:
                active = (color, index)
                continue
            if _takes_over(candles[older_at], candles[index], older_color=older_color):
                active = (color, index)
                continue
            active, expired = None, (older_color, older_at, color, index)
        return active, expired

    def _older_state(
        self, candles: Sequence[CandleLike], color: str, at: int, marks: Sequence[str]
    ) -> str:
        """pending: breakout done, retest not yet touched. building: still forming. finished: over."""
        buy = color == "yellow"
        before = (
            _two_reds_on_the_upper_side(candles, at, marks)
            if buy
            else _two_greens_on_the_lower_side(candles, at, marks)
        )
        if not before:
            return "finished"
        built = (
            self._buy_from(candles, only_index=at, marks=marks)
            if buy
            else self._sell_from(candles, only_index=at, marks=marks)
        )
        if built is None:
            return "building"
        extra = built[1]
        if extra.get("setup_invalid") == "1" or extra.get("missed_target") == "1":
            return "finished"
        if extra.get("await_retest") != "1":
            return "building"
        level = candles[at].high if buy else candles[at].low
        parts = _setup_parts(candles, at, buy=buy)
        return "finished" if _retested(candles, parts, level, buy=buy) else "pending"

    def _both_expired(
        self,
        candles: Sequence[CandleLike],
        expired: tuple[str, int, str, int],
        symbol: str,
        timeframe: str,
    ) -> Signal:
        older_color, older_at, _, newer_at = expired
        older = candles[older_at]
        buy = older_color == "yellow"
        name, other = ("YELLOW", "BLUE") if buy else ("BLUE", "YELLOW")
        stage_log = " | ".join(
            [
                _timed(f"{name} DETECTED — {'BUY' if buy else 'SELL'} SETUP", older),
                _timed(
                    f"SETUP INVALID BECAUSE {other} CANDLE AFTER THE {name}", candles[newer_at]
                ),
                "BOTH EXPIRED — WAITING FOR A NEW YELLOW OR BLUE",
            ]
        )
        extra = {
            "pattern": "buy_yellow_sweep" if buy else "sell_blue_sweep",
            f"{older_color}_low": str(older.low),
            f"{older_color}_high": str(older.high),
            "anchor_time": str(_ts(older)),
            "setup_clock": _clock(older),
            "setup_invalid": "1",
            "stage_log": stage_log,
        }
        reason = f"{older_color}_on_chart_no_breakout"
        return self._signal(SignalType.NO_TRADE, reason, symbol, timeframe, candles[-1], 0.0, extra)

    def _buy_from(
        self,
        candles: Sequence[CandleLike],
        *,
        only_index: int | None = None,
        marks: Sequence[str] | None = None,
    ) -> tuple[float, dict[str, str]] | None:
        if len(candles) < (5 if only_index is not None else 6):
            return None
        indexes = [only_index] if only_index is not None else range(len(candles) - 4, 1, -1)
        for y in indexes:
            if y is None or y < 2 or y >= len(candles):
                continue
            if only_index is None and y > len(candles) - 4:
                continue
            yellow = candles[y]
            if is_doji(yellow):
                if only_index is None:
                    continue
                return 0.0, {
                    "pattern": "buy_yellow_sweep",
                    "yellow_low": str(yellow.low),
                    "yellow_high": str(yellow.high),
                    "last_yellow": "1",
                    "anchor_time": str(_ts(yellow)),
                    "setup_clock": _clock(yellow),
                    "setup_invalid": "1",
                    "stage_log": _timed("YELLOW DETECTED — BUY SETUP", yellow)
                    + " | "
                    + _timed("SETUP INVALID BECAUSE THE YELLOW IS A DOJI", yellow),
                }
            reds = _two_reds_on_the_upper_side(candles, y, marks)
            parts = _setup_parts(candles, y, buy=True)
            sweep_at, early_at = parts.sweep_at, parts.early_at
            awaiting_return_at = parts.awaiting_return_at
            missing_sweep = parts.no_sweep_at is not None
            breakout_at = parts.no_sweep_at if missing_sweep else parts.breakout_at
            gap_end = breakout_at + 1 if breakout_at is not None else len(candles)
            gap_at = next(
                (
                    i
                    for i in range(y + 1, gap_end)
                    if is_red(candles[i]) and unattached_below(candles[i], yellow)
                ),
                None,
            )
            ready = (
                bool(reds)
                and sweep_at is not None
                and breakout_at is not None
                and gap_at is None
                and early_at is None
            )
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
                    early_at=early_at,
                    missing_sweep=missing_sweep,
                    missed_at=None,
                    awaiting_return_at=awaiting_return_at,
                    weak_at=parts.weak_at,
                ),
            }
            if not ready:
                if only_index is None:
                    continue
                extra["setup_invalid"] = (
                    "1" if gap_at is not None or early_at is not None or missing_sweep else "0"
                )
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
            risk = yellow.high - after_low
            tp = order_target(yellow.high, risk, buy=True)
            cancel = reward_target(yellow.high, risk, buy=True, ratio=CANCEL_R)
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
                        early_at=None,
                        missing_sweep=False,
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
        self,
        candles: Sequence[CandleLike],
        *,
        only_index: int | None = None,
        marks: Sequence[str] | None = None,
    ) -> tuple[float, dict[str, str]] | None:
        if len(candles) < (5 if only_index is not None else 6):
            return None
        indexes = [only_index] if only_index is not None else range(len(candles) - 4, 1, -1)
        for b in indexes:
            if b is None or b < 2 or b >= len(candles):
                continue
            if only_index is None and b >= len(candles) - 3:
                continue
            blue = candles[b]
            if is_doji(blue):
                if only_index is None:
                    continue
                return 0.0, {
                    "pattern": "sell_blue_sweep",
                    "blue_low": str(blue.low),
                    "blue_high": str(blue.high),
                    "last_blue": "1",
                    "anchor_time": str(_ts(blue)),
                    "setup_clock": _clock(blue),
                    "setup_invalid": "1",
                    "stage_log": _timed("BLUE DETECTED — SELL SETUP", blue)
                    + " | "
                    + _timed("SETUP INVALID BECAUSE THE BLUE IS A DOJI", blue),
                }
            greens = _two_greens_on_the_lower_side(candles, b, marks)
            parts = _setup_parts(candles, b, buy=False)
            sweep_at, early_at = parts.sweep_at, parts.early_at
            awaiting_return_at = parts.awaiting_return_at
            missing_sweep = parts.no_sweep_at is not None
            breakdown_at = parts.no_sweep_at if missing_sweep else parts.breakout_at
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
                bool(greens)
                and sweep_at is not None
                and breakdown_at is not None
                and gap_at is None
                and early_at is None
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
                    early_at=early_at,
                    missing_sweep=missing_sweep,
                    missed_at=None,
                    awaiting_return_at=awaiting_return_at,
                    weak_at=parts.weak_at,
                ),
            }
            if not ready:
                if only_index is None:
                    continue
                extra["setup_invalid"] = (
                    "1" if gap_at is not None or early_at is not None or missing_sweep else "0"
                )
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
            risk = after_high - blue.low
            tp = order_target(blue.low, risk, buy=False)
            cancel = reward_target(blue.low, risk, buy=False, ratio=CANCEL_R)
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
                        early_at=None,
                        missing_sweep=False,
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
