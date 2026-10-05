from __future__ import annotations

from datetime import datetime, timezone

import cv2
import numpy as np

from trading_bot.market.chart_prices import _parse_price, market_from_bgr, paint_text
from trading_bot.market.kkc_vision import ChartVision, HsvRange


def _vision() -> ChartVision:
    return ChartVision(
        x=0,
        y=0,
        width=80,
        height=40,
        yellow=HsvRange(15, 40, 80, 255, 120, 255),
        blue=HsvRange(100, 140, 80, 255, 80, 255),
        min_confidence=0.55,
        calibrated=True,
    )


def _bgr(hue: int, width: int, height: int) -> np.ndarray:
    hsv = np.zeros((height, width, 3), dtype=np.uint8)
    hsv[:, :] = (hue, 200, 220)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_zoom_moves_toward_about_45_candles_and_14_points() -> None:
    from trading_bot.market.chart_prices import zoom_action

    assert zoom_action(97, 32) == "time-in"
    assert zoom_action(30, 14) == "time-out"
    assert zoom_action(54, 19) is None
    assert zoom_action(45, 32) is None
    assert zoom_action(45, 8) is None
    assert zoom_action(45, 14) is None


def test_broken_axis_numbers_snap_back_onto_the_gold_scale() -> None:
    from trading_bot.market.chart_prices import _repair_axis

    labels = _repair_axis(
        [
            (10, "4,188.8"),
            (30, "4,186.8"),
            (50, "4,18.8"),
            (90, "4,180.08"),
            (110, "4,1.8"),
        ]
    )
    prices = sorted(price for _y, price in labels)
    assert prices == [4178.0, 4180.0, 4184.0, 4186.0, 4188.0]


def test_price_text_keeps_the_comma_and_the_decimals() -> None:
    assert _parse_price("4,122.325") == 4122.325
    assert _parse_price("4.122.325") == 4122.325
    assert _parse_price("4122.50") == 4122.5
    assert _parse_price("12") is None


def test_axis_numbers_are_read_back() -> None:
    image = np.full((220, 420, 3), 255, dtype=np.uint8)
    high_y = paint_text(image, "4,120.000", 300, 30)
    low_y = paint_text(image, "4,100.000", 300, 150)
    from trading_bot.market.chart_prices import _price_labels

    labels = _price_labels(image, 240)
    prices = [price for _y, price in labels]
    assert 4120.0 in prices
    assert 4100.0 in prices
    by_price = {price: y for y, price in labels}
    assert by_price[4120.0] < by_price[4100.0]
    assert high_y < low_y


def test_blue_low_is_that_candles_wick_not_the_later_red() -> None:
    image = np.full((240, 460, 3), 255, dtype=np.uint8)
    y_hi = paint_text(image, "4,130.000", 340, 28)
    paint_text(image, "4,120.000", 340, 68)
    y_lo = paint_text(image, "4,110.000", 340, 108)
    paint_text(image, "4,090.000", 340, 188)
    slope = (4110.0 - 4130.0) / (y_lo - y_hi)
    # Seven candles. The blue wick ends above the next red wick.
    candles = [
        (0, 90, 130, 70, 150),
        (60, 90, 130, 70, 150),
        (115, 80, 120, 60, 130),
        (0, 100, 150, 90, 190),
        (0, 90, 130, 70, 150),
        (0, 90, 130, 70, 150),
        (0, 90, 130, 70, 150),
    ]
    for index, (hue, body_top, body_bottom, wick_top, wick_bottom) in enumerate(candles):
        x = 20 + index * 36
        _body(image, hue, x, body_top, body_bottom)
        _wick(image, hue, x + 4, wick_top, wick_bottom)
    market = market_from_bgr(image, _vision(), datetime(2026, 9, 29, 5, 45, tzinfo=timezone.utc))
    assert market is not None
    assert market.clipped is False
    blue_at = market.colors.index("blue")
    red_at = blue_at + 1
    blue = market.closed[blue_at]
    red = market.closed[red_at]

    def at(y: float) -> float:
        return 4130.0 + slope * (y - y_hi)

    assert abs(blue.low - at(130)) < 1.5
    assert abs(red.low - at(190)) < 1.5
    assert red.close < red.open
    assert red.close < blue.low
    assert blue.low > red.low


def test_a_wick_on_the_edge_is_cut_off() -> None:
    image = _chart()
    _wick(image, 0, 24, 0, 30)
    market = market_from_bgr(image, _vision(), datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc))
    assert market is not None
    assert market.clipped is True


def test_price_scale_click_lands_on_the_quiet_numbers() -> None:
    from trading_bot.market.kkc_vision import _price_scale_point

    image = np.full((200, 400, 3), 255, dtype=np.uint8)
    point = _price_scale_point(image, _vision())
    assert point == (365, 70)
    image[:, 330:] = _bgr(0, 70, 200)
    assert _price_scale_point(image, _vision()) is None


def test_two_prices_do_not_set_the_scale() -> None:
    image = _chart([("4,299.000", 28), ("1,968.000", 108)])
    market = market_from_bgr(image, _vision(), datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc))
    assert market is not None
    assert market.scale is None
    assert market.price_span is None
    assert any(candle.close != candle.open for candle in market.closed)


def test_a_steep_scale_is_rejected() -> None:
    image = _chart(
        [("5,000.000", 28), ("4,000.000", 68), ("3,000.000", 108), ("2,000.000", 148)]
    )
    market = market_from_bgr(image, _vision(), datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc))
    assert market is not None
    assert market.scale is None


def test_a_clock_number_does_not_move_the_gold_price() -> None:
    image = _chart()
    paint_text(image, "18:45", 340, 210)
    market = market_from_bgr(image, _vision(), datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc))
    assert market is not None
    assert 4050 < market.forming.close < 4160


def test_a_bad_picture_keeps_the_last_good_scale() -> None:
    now = datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc)
    first = market_from_bgr(_chart(), _vision(), now)
    assert first is not None and first.scale is not None
    second = market_from_bgr(_chart([]), _vision(), now, previous_scale=first.scale)
    assert second is not None
    assert abs(second.forming.close - first.forming.close) < 0.05


def _chart(labels: list[tuple[str, int]] | None = None) -> np.ndarray:
    image = np.full((240, 460, 3), 255, dtype=np.uint8)
    if labels is None:
        labels = [("4,130.000", 28), ("4,120.000", 68), ("4,110.000", 108), ("4,090.000", 188)]
    for text, top in labels:
        paint_text(image, text, 340, top)
    candles = [
        (0, 90, 130, 70, 150),
        (60, 90, 130, 70, 150),
        (115, 80, 120, 60, 130),
        (0, 100, 150, 90, 190),
        (0, 90, 130, 70, 150),
        (0, 90, 130, 70, 150),
        (0, 90, 130, 70, 150),
    ]
    for index, (hue, body_top, body_bottom, wick_top, wick_bottom) in enumerate(candles):
        x = 20 + index * 36
        _body(image, hue, x, body_top, body_bottom)
        _wick(image, hue, x + 4, wick_top, wick_bottom)
    return image


def _body(image: np.ndarray, hue: int, x: int, y0: int, y1: int) -> None:
    patch = _bgr(hue, 10, y1 - y0)
    image[y0:y1, x : x + 10] = patch


def _wick(image: np.ndarray, hue: int, x: int, y0: int, y1: int) -> None:
    patch = _bgr(hue, 2, y1 - y0)
    image[y0:y1, x : x + 2] = patch
