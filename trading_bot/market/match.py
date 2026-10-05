"""Pair a painted column with a MetaTrader bar.

The picture's red and green candles are lined up with MetaTrader's bars.
Chart time is UTC+5. Server time is UTC. This module is the only
place that converts one into the other.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

CHART_UTC_OFFSET = timedelta(hours=5)
_CHART_ZONE = timezone(CHART_UTC_OFFSET)


def chart_to_server_time(chart_time: datetime) -> datetime:
    """Chart clock (UTC+5) to MetaTrader server time (UTC)."""
    if chart_time.tzinfo is None:
        chart_time = chart_time.replace(tzinfo=_CHART_ZONE)
    return chart_time.astimezone(timezone.utc)


def server_to_chart_time(server_time: datetime) -> datetime:
    """Server time back to the chart clock. Uses the same five-hour offset."""
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=timezone.utc)
    return server_time.astimezone(_CHART_ZONE)


def _minute(stamp: datetime) -> datetime:
    return stamp.replace(second=0, microsecond=0)


def paint_by_time(
    chart_times: list[datetime],
    colors: list[str],
    bar_times: list[datetime],
) -> list[str]:
    """Put each painted column on the MetaTrader bar with the same minute.

    A column the picture skipped leaves its own bar blank. It does not move
    the yellow or blue onto a newer bar.
    """
    painted: dict[datetime, str] = {}
    for stamp, color in zip(chart_times, colors):
        if color in ("yellow", "blue"):
            painted[_minute(chart_to_server_time(stamp))] = color
    return [painted.get(_minute(_as_server(bar)), "") for bar in bar_times]


def _direction(candle) -> int:
    if candle.close > candle.open:
        return 1
    if candle.close < candle.open:
        return -1
    return 0


def chart_offset(chart_candles: list, bars: list, reach: int = 5) -> timedelta:
    """Minutes to add to the picture's times so its reds and greens sit on MetaTrader's.

    The picture dates each candle by counting from the rightmost one. When
    that candle is missed, every time is off by a minute or more. Sliding the
    red and green pattern over MetaTrader's bars finds the true position.
    """
    return chart_match(chart_candles, bars, reach)[0]


def chart_match(chart_candles: list, bars: list, reach: int) -> tuple[timedelta, int, int]:
    """Best shift, and how many reds and greens agree with MetaTrader out of how many compared.

    A chart scrolled away from the newest candle still dates its rightmost
    candle as now, so the shift can be far more than a few minutes.
    """
    by_minute = {_minute(_as_server(bar.timestamp)): _direction(bar) for bar in bars}
    marks = [
        (_minute(chart_to_server_time(candle.timestamp)), _direction(candle))
        for candle in chart_candles
    ]
    marks = [(stamp, side) for stamp, side in marks if side != 0]
    best = (0, 0, 0, 0)
    for shift in sorted(range(-reach, reach + 1), key=abs):
        step = timedelta(minutes=shift)
        agree = votes = 0
        for stamp, side in marks:
            other = by_minute.get(stamp + step, 0)
            if other == 0:
                continue
            votes += 1
            agree += 1 if other == side else 0
        score = 2 * agree - votes
        if score > best[1]:
            best = (shift, score, agree, votes)
    shift, _score, agree, votes = best
    return timedelta(minutes=shift), agree, votes


def _as_server(bar_time: datetime) -> datetime:
    if bar_time.tzinfo is None:
        return bar_time.replace(tzinfo=timezone.utc)
    return bar_time.astimezone(timezone.utc)


def align_paint(colors: list[str], count: int) -> list[str]:
    """Put the rightmost paint on the newest bar. Older bars with no paint stay blank."""
    if count <= 0:
        return []
    if len(colors) >= count:
        return list(colors[-count:])
    return [""] * (count - len(colors)) + list(colors)
