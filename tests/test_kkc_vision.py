from __future__ import annotations

import cv2
import numpy as np

from trading_bot.market.kkc_vision import (
    ChartVision,
    ColorMemory,
    HsvRange,
    colors_from_bgr,
    range_from_samples,
)


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


def _bgr(hue: int, width: int) -> np.ndarray:
    hsv = np.zeros((20, width, 3), dtype=np.uint8)
    hsv[:, :] = (hue, 200, 220)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_closed_yellow_and_blue_are_kept_and_the_forming_bar_is_dropped() -> None:
    image = np.full((40, 80, 3), 255, dtype=np.uint8)
    image[8:28, 4:12] = _bgr(25, 8)
    image[8:28, 24:32] = _bgr(115, 8)
    image[8:28, 60:70] = _bgr(0, 10)
    colors = colors_from_bgr(image, _vision(), 2)
    assert colors == ["yellow", "blue"]


def test_price_tag_on_the_right_is_not_a_blue_candle() -> None:
    image = np.full((40, 140, 3), 255, dtype=np.uint8)
    image[8:28, 4:12] = _bgr(25, 8)
    image[8:28, 24:32] = _bgr(115, 8)
    image[8:28, 48:58] = _bgr(0, 10)
    tag = _bgr(115, 50)
    image[12:22, 80:130] = tag[:10]
    colors = colors_from_bgr(image, _vision(), 2)
    assert colors == ["yellow", "blue"]


def test_low_confidence_body_is_unknown() -> None:
    image = np.full((40, 40, 3), 255, dtype=np.uint8)
    image[8:28, 4:8] = _bgr(25, 4)
    image[8:28, 8:16] = _bgr(60, 8)
    image[8:28, 28:36] = _bgr(0, 8)
    colors = colors_from_bgr(image, _vision(), 1)
    assert colors == [""]


def test_uncalibrated_chart_returns_no_colors() -> None:
    vision = _vision()
    blank = ChartVision(
        vision.x,
        vision.y,
        vision.width,
        vision.height,
        vision.yellow,
        vision.blue,
        vision.min_confidence,
        False,
    )
    image = np.full((40, 40, 3), 255, dtype=np.uint8)
    assert colors_from_bgr(image, blank, 3) == []


def _paint(image: np.ndarray, hue: int, x0: int, x1: int, y0: int = 10, y1: int = 30) -> None:
    patch = _bgr(hue, x1 - x0)
    image[y0:y1, x0:x1] = patch[: y1 - y0]


def _regular_chart(
    hues: list[int | None], pitch: int = 16, body: int = 6, extra_right: int = 20
) -> np.ndarray:
    left = 12
    width = left + len(hues) * pitch + extra_right
    image = np.full((48, width, 3), 255, dtype=np.uint8)
    half = body // 2
    for index, hue in enumerate(hues):
        if hue is None:
            continue
        center = left + index * pitch + pitch // 2
        _paint(image, hue, center - half, center + half)
    return image


def test_missing_candle_does_not_move_the_blue_onto_a_later_minute() -> None:
    hues: list[int | None] = [25, None, 115, 0, 0, 0]
    colors = colors_from_bgr(_regular_chart(hues), _vision(), 10)
    assert colors[7] == "blue"
    assert colors[6] == ""
    assert colors.count("blue") == 1


def test_split_body_is_still_one_blue_on_the_same_minute() -> None:
    hues: list[int | None] = [25, 0, 115, 0, 0]
    image = _regular_chart(hues)
    _paint(image, 115, 56, 59)
    colors = colors_from_bgr(image, _vision(), 8)
    assert colors.count("blue") == 1
    assert colors[6] == "blue"


def test_price_label_letters_are_not_extra_blue_candles() -> None:
    hues: list[int | None] = [0, 25, 115, 0, 0, 0, 0, 0]
    image = _regular_chart(hues, extra_right=80)
    for index in range(6):
        _paint(image, 115, 168 + index * 7, 172 + index * 7, y0=14, y1=24)
    colors = colors_from_bgr(image, _vision(), 7)
    assert colors.count("blue") == 1
    assert colors[2] == "blue"


def test_a_blue_seen_twice_stays_when_the_next_picture_is_blank() -> None:
    memory = ColorMemory()
    stamps = ["20:57", "21:00"]
    assert memory.apply(stamps, ["blue", ""]) == ["", ""]
    assert memory.apply(stamps, ["blue", ""]) == ["blue", ""]
    assert memory.apply(stamps, ["", ""]) == ["blue", ""]


def test_one_false_blue_does_not_become_the_setup() -> None:
    memory = ColorMemory()
    stamps = ["20:57", "21:00"]
    assert memory.apply(stamps, ["", "blue"]) == ["", ""]
    assert memory.apply(stamps, ["", ""]) == ["", ""]
    assert memory.apply(stamps, ["", "blue"]) == ["", ""]


def test_a_held_blue_stays_on_its_minute_when_a_new_candle_opens() -> None:
    memory = ColorMemory()
    memory.apply(["20:56", "20:57"], ["", "blue"])
    memory.apply(["20:56", "20:57"], ["", "blue"])
    assert memory.apply(["20:57", "21:00"], ["", ""]) == ["blue", ""]


def test_sample_range_covers_the_sampled_yellow() -> None:
    hsv = np.zeros((12, 12, 3), dtype=np.uint8)
    hsv[:, :] = (28, 180, 210)
    found = range_from_samples(hsv.reshape(-1, 3))
    assert found is not None
    assert found.contains(28, 180, 210)
