"""Read yellow and blue candle paint from the TradingView chart."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from typing import Any

from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.tv_colors")

Pixel = tuple[int, int, int]
Rows = list[list[Pixel]]

_CHART_SELECTORS = (
    "div.chart-gui-wrapper",
    "div.chart-container",
    "div.layout__area--center",
)


def _hue(r: int, g: int, b: int) -> float | None:
    peak = max(r, g, b)
    floor = min(r, g, b)
    if peak == floor or peak < 70:
        return None
    span = peak - floor
    if peak == r:
        hue = ((g - b) / span) % 6
    elif peak == g:
        hue = (b - r) / span + 2
    else:
        hue = (r - g) / span + 4
    return hue * 60


def is_yellow(r: int, g: int, b: int) -> bool:
    hue = _hue(r, g, b)
    return hue is not None and 35 <= hue <= 75 and max(r, g, b) >= 150 and (max(r, g, b) - min(r, g, b)) >= 40


def is_blue(r: int, g: int, b: int) -> bool:
    """Blue or purple paint. Teal candle bodies stay out of this range."""
    hue = _hue(r, g, b)
    return hue is not None and 190 <= hue <= 320 and max(r, g, b) >= 70 and (max(r, g, b) - min(r, g, b)) >= 25


def _is_background(r: int, g: int, b: int) -> bool:
    if r > 235 and g > 235 and b > 235:
        return True
    return abs(r - g) < 14 and abs(g - b) < 14 and r > 170


def colors_from_rows(rows: Rows, count: int) -> list[str]:
    """Align chart columns to the last `count` closed candles. Rightmost bar is still forming."""
    if count <= 0 or not rows:
        return []
    height = len(rows)
    width = len(rows[0])
    top = int(height * 0.18)
    bottom = int(height * 0.90)
    if bottom <= top:
        top, bottom = 0, height
    paint = [""] * width
    ink = [False] * width
    for x in range(width):
        yellows = blues = marks = 0
        for y in range(top, bottom):
            r, g, b = rows[y][x]
            if _is_background(r, g, b):
                continue
            marks += 1
            if is_yellow(r, g, b):
                yellows += 1
            elif is_blue(r, g, b):
                blues += 1
        if marks >= 4:
            ink[x] = True
        if yellows >= 2 and yellows > blues:
            paint[x] = "yellow"
        elif blues >= 2 and blues > yellows:
            paint[x] = "blue"
    bars: list[str] = []
    x = 0
    while x < width:
        if not ink[x] and not paint[x]:
            x += 1
            continue
        start = x
        while x < width and (ink[x] or paint[x]):
            x += 1
        chunk = paint[start:x]
        yellows = chunk.count("yellow")
        blues = chunk.count("blue")
        if yellows > blues and yellows > 0:
            bars.append("yellow")
        elif blues > yellows and blues > 0:
            bars.append("blue")
        else:
            bars.append("")
        gap = 0
        while x < width and not ink[x] and not paint[x]:
            gap += 1
            x += 1
            if gap >= 2:
                break
    if len(bars) >= 2:
        bars = bars[:-1]
    if len(bars) >= count:
        return bars[-count:]
    return [""] * (count - len(bars)) + bars


def colors_from_png(data: bytes, count: int) -> list[str]:
    return colors_from_rows(_decode_png(data), count)


class TvColorReader:
    """Screenshot the open TradingView chart and label the last closed candles."""

    def __init__(self, page_provider: Callable[[], Any]) -> None:
        self._page_provider = page_provider

    async def colors_for(self, count: int) -> list[str]:
        page = self._page_provider()
        if page is None:
            log.info("tv_colors_skipped", reason="no_chart_page")
            return []
        png = await _screenshot_chart(page)
        colors = colors_from_png(png, count)
        log.info(
            "tv_colors_read",
            yellow=colors.count("yellow"),
            blue=colors.count("blue"),
            bars=len(colors),
        )
        return colors


async def _screenshot_chart(page: Any) -> bytes:
    for selector in _CHART_SELECTORS:
        locator = page.locator(selector).first
        try:
            if await locator.count() > 0:
                return await locator.screenshot(type="png")
        except Exception:  # noqa: BLE001
            continue
    return await page.screenshot(type="png")


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    if abs(estimate - left) <= abs(estimate - up) and abs(estimate - left) <= abs(estimate - up_left):
        return left
    if abs(estimate - up) <= abs(estimate - up_left):
        return up
    return up_left


def _decode_png(data: bytes) -> Rows:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Chart screenshot is not a PNG.")
    pos = 8
    width = height = 0
    color_type = 2
    compressed = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
            if bit_depth != 8 or color_type not in (2, 6):
                raise ValueError("Unsupported chart PNG.")
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
    raw = zlib.decompress(bytes(compressed))
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    rows: Rows = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scan = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        rebuilt = bytearray(stride)
        for index in range(stride):
            left = rebuilt[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            value = scan[index]
            if filter_type == 1:
                value = (value + left) & 255
            elif filter_type == 2:
                value = (value + up) & 255
            elif filter_type == 3:
                value = (value + ((left + up) // 2)) & 255
            elif filter_type == 4:
                value = (value + _paeth(left, up, up_left)) & 255
            rebuilt[index] = value
        previous = rebuilt
        rows.append(
            [(rebuilt[i], rebuilt[i + 1], rebuilt[i + 2]) for i in range(0, stride, channels)]
        )
    return rows
