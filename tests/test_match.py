from datetime import datetime, timezone

from datetime import timedelta

from trading_bot.market.match import (
    align_paint,
    chart_to_server_time,
    paint_by_time,
    server_to_chart_time,
)

_CHART = timezone(timedelta(hours=5))


def _bar(stamp: datetime, green: bool | None):
    from trading_bot.market.candle_reader import Candle

    if green is None:
        return Candle(timestamp=stamp, open=10.0, high=10.2, low=9.8, close=10.0, is_closed=True)
    open_, close = (9.9, 10.1) if green else (10.1, 9.9)
    return Candle(timestamp=stamp, open=open_, high=10.2, low=9.8, close=close, is_closed=True)


def test_picture_times_two_minutes_late_are_moved_back() -> None:
    from trading_bot.market.match import chart_offset

    pattern = [True, False, False, True, True, False, True, False, True, True, False, False]
    bars = [_bar(datetime(2026, 9, 30, 13, 10 + i, tzinfo=timezone.utc), g) for i, g in enumerate(pattern)]
    # The picture shows the same candles, stamped two minutes late.
    chart = [
        _bar(datetime(2026, 9, 30, 18, 12 + i, tzinfo=_CHART), g) for i, g in enumerate(pattern)
    ]
    assert chart_offset(chart, bars) == timedelta(minutes=-2)
    exact = [_bar(datetime(2026, 9, 30, 18, 10 + i, tzinfo=_CHART), g) for i, g in enumerate(pattern)]
    assert chart_offset(exact, bars) == timedelta(0)


def _mixed(count: int, seed: int) -> list[bool]:
    import random

    rng = random.Random(seed)
    return [rng.random() < 0.5 for _ in range(count)]


def test_a_chart_scrolled_forty_minutes_back_is_found() -> None:
    from trading_bot.market.match import chart_match

    pattern = _mixed(200, 7)
    start = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    bars = [_bar(start + timedelta(minutes=i), g) for i, g in enumerate(pattern)]
    # The picture shows 45 candles ending 40 minutes before the newest bar,
    # but dates its rightmost candle as the newest minute.
    shown = pattern[-85:-40]
    first_stamp = (start + timedelta(minutes=200 - 45)).astimezone(_CHART)
    chart = [_bar(first_stamp + timedelta(minutes=i), g) for i, g in enumerate(shown)]
    shift, agree, votes = chart_match(chart, bars, 180)
    assert shift == timedelta(minutes=-40)
    assert agree == votes == 45


def test_a_picture_of_something_else_does_not_match() -> None:
    from trading_bot.market.match import chart_match

    start = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    bars = [_bar(start + timedelta(minutes=i), g) for i, g in enumerate(_mixed(200, 7))]
    first_stamp = (start + timedelta(minutes=155)).astimezone(_CHART)
    chart = [_bar(first_stamp + timedelta(minutes=i), g) for i, g in enumerate(_mixed(45, 99))]
    _shift, agree, votes = chart_match(chart, bars, 180)
    assert agree < votes * 0.75


def test_a_skipped_doji_does_not_move_the_yellow() -> None:
    # Chart 17:49 yellow. The picture skipped 17:52 and 17:54.
    chart_minutes = [47, 48, 49, 50, 51, 53, 55, 56, 57, 58]
    chart_times = [datetime(2026, 9, 30, 17, m, tzinfo=_CHART) for m in chart_minutes]
    colors = ["", "", "yellow", "", "", "", "", "", "", ""]
    bars = [datetime(2026, 9, 30, 12, m, tzinfo=timezone.utc) for m in range(47, 59)]
    painted = paint_by_time(chart_times, colors, bars)
    assert painted.count("yellow") == 1
    assert bars[painted.index("yellow")].minute == 49
    assert align_paint(colors, len(bars)).index("yellow") != painted.index("yellow")


def test_paint_lines_up_with_the_newest_bar() -> None:
    colors = align_paint(["", "yellow", "blue"], 5)
    assert colors == ["", "", "", "yellow", "blue"]


def test_chart_time_minus_five_hours_is_server_time() -> None:
    chart = datetime(2026, 9, 30, 17, 6, tzinfo=timezone.utc)
    # 17:06 treated as UTC is not the chart clock. A naive chart time is UTC+5.
    naive = datetime(2026, 9, 30, 17, 6)
    server = chart_to_server_time(naive)
    assert server == datetime(2026, 9, 30, 12, 6, tzinfo=timezone.utc)
    assert server_to_chart_time(server).hour == 17
    assert chart_to_server_time(chart).hour == 17
