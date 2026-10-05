"""Read the candles on the TradingView chart: position, red or green, and paint.

The price numbers on the right of the chart turn a pixel into a price.
When they cannot be read, the candles are still returned with pixel
heights so their reds and greens can be matched against MetaTrader.
Trade prices always come from MetaTrader.
"""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np

from trading_bot.market.candle_reader import Candle
from trading_bot.market.kkc_vision import (
    ChartVision,
    _candle_pitch,
    _candle_runs,
    _label_bar,
)
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.chart_prices")

_FONTS = ("Trebuchet MS", "Segoe UI", "Arial", "Tahoma", "Verdana", "Calibri")
_CHARS = tuple("0123456789.,")
_MIN_LABELS = 4


@dataclass(frozen=True)
class ChartMarket:
    closed: list[Candle]
    forming: Candle
    colors: list[str]
    fresh_colors: list[str] | None = None
    scale: tuple[float, float] | None = None
    clipped: bool = False
    price_span: float | None = None


def market_from_bgr(
    image: np.ndarray,
    vision: ChartVision,
    now: datetime,
    previous_scale: tuple[float, float] | None = None,
) -> ChartMarket | None:
    """Candles visible on the chart, oldest first. The rightmost bar is still forming."""
    if image.size == 0 or not vision.ready:
        return None
    import cv2

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    ink = _column_ink(hsv)
    if ink is None:
        _fail("chart picture is too small")
        return None
    runs = _candle_runs(ink)
    if len(runs) < 6:
        _fail("not enough candles on the chart")
        return None
    scale = _choose_scale(image, runs[-1][1], previous_scale)
    priced = scale is not None
    slope, intercept = scale if scale is not None else (-1.0, float(hsv.shape[0]))
    clipped = _wicks_touch_edge(hsv, runs)
    pitch = _candle_pitch(runs)
    forming_center = (runs[-1][0] + runs[-1][1]) / 2.0
    minute = _local_minute(now)
    closed_by_slot: dict[int, tuple[Candle, str, float]] = {}
    forming: Candle | None = None
    height = hsv.shape[0]
    for start, end in runs:
        center = (start + end) / 2.0
        minutes = (forming_center - center) / pitch
        slot = int(np.floor(minutes + 0.5))
        distance = abs(minutes - slot)
        if (start, end) == runs[-1]:
            slot = 0
            distance = 0.0
        elif slot < 1 or distance > 0.45:
            continue
        bar = _bar_ohlc(hsv, start, end, slope, intercept, vision, height)
        if bar is None:
            continue
        stamp = minute - timedelta(minutes=slot)
        candle = Candle(
            timestamp=stamp,
            open=bar[0],
            high=bar[1],
            low=bar[2],
            close=bar[3],
            is_closed=slot != 0,
        )
        if slot == 0:
            forming = candle
            continue
        previous = closed_by_slot.get(slot)
        if previous is not None and previous[2] <= distance:
            continue
        closed_by_slot[slot] = (candle, bar[4], distance)
    if forming is None or len(closed_by_slot) < 5:
        _fail("the chart candles could not be priced")
        return None
    ordered = [closed_by_slot[slot] for slot in sorted(closed_by_slot, reverse=True)]
    closed = [item[0] for item in ordered]
    colors = [item[1] for item in ordered]
    log.info(
        "chart_prices_read",
        candles=len(closed),
        forming_close=forming.close if priced else None,
        blue=colors.count("blue"),
        yellow=colors.count("yellow"),
        clipped=clipped,
        priced=priced,
    )
    return ChartMarket(
        closed=closed,
        forming=forming,
        colors=colors,
        fresh_colors=list(colors),
        scale=(slope, intercept) if priced else None,
        clipped=clipped,
        price_span=abs(slope) * float(height) if priced else None,
    )


def candle_run_count(image: np.ndarray) -> int:
    """How many candle bodies are visible. Used to decide a zoom step."""
    if image.size == 0:
        return 0
    import cv2

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    ink = _column_ink(hsv)
    if ink is None:
        return 0
    return len(_candle_runs(ink))


def zoom_action(candle_count: int, price_span: float | None) -> str | None:
    """Zoom the time scale until about 40 to 55 candles are on screen.

    A price-scale zoom is not used. Gold's visible range sits near 18 points,
    and zooming that scale in pushes a wick into the edge. The next check then
    resets the scale, and the two actions repeat.
    """
    del price_span
    if candle_count > 55:
        return "time-in"
    if 0 < candle_count < 40:
        return "time-out"
    return None


def _local_minute(now: datetime) -> datetime:
    current = now.astimezone() if now.tzinfo is not None else now
    return current.replace(second=0, microsecond=0)


def _fail(reason: str) -> None:
    log.info("chart_prices_unavailable", reason=reason)


def _wicks_touch_edge(hsv: np.ndarray, runs: list[tuple[int, int]]) -> bool:
    """A wick on the first or last rows is cut off by the chart box."""
    height = hsv.shape[0]
    for start, end in runs:
        patch = hsv[:, start:end]
        if patch.size == 0:
            continue
        rows = np.where((patch[:, :, 1] > 35).any(axis=1))[0]
        if len(rows) == 0:
            continue
        if int(rows.min()) <= 2 or int(rows.max()) >= height - 3:
            return True
    return False


def _column_ink(hsv: np.ndarray) -> np.ndarray | None:
    height = hsv.shape[0]
    top = int(height * 0.08)
    bottom = int(height * 0.92)
    if bottom <= top:
        return None
    return np.count_nonzero(hsv[top:bottom, :, 1] > 35, axis=0) >= 3


def _bar_ohlc(
    hsv: np.ndarray,
    start: int,
    end: int,
    slope: float,
    intercept: float,
    vision: ChartVision,
    height: int,
) -> tuple[float, float, float, float, str] | None:
    patch = hsv[:, start:end]
    if patch.size == 0:
        return None
    saturated = patch[:, :, 1] > 35
    rows = np.where(saturated.any(axis=1))[0]
    if len(rows) == 0:
        return None
    width = end - start
    row_fill = saturated.sum(axis=1)
    body_rows = np.where(row_fill >= max(2, int(width * 0.45)))[0]
    if len(body_rows) == 0:
        body_rows = rows
    top_y = int(rows.min())
    bottom_y = int(rows.max())
    body_top = int(body_rows.min())
    body_bottom = int(body_rows.max())
    label = _label_bar(hsv, 0, height, start, end, vision)
    direction = "" if label in ("yellow", "blue") else _direction(patch, saturated, body_rows)
    high = _price_at(top_y, slope, intercept)
    low = _price_at(bottom_y, slope, intercept)
    body_high = _price_at(body_top, slope, intercept)
    body_low = _price_at(body_bottom, slope, intercept)
    if direction == "red":
        open_, close = body_high, body_low
    elif direction == "green":
        open_, close = body_low, body_high
    else:
        mid = round((body_high + body_low) / 2.0, 3)
        open_, close = mid, mid
    if high < low:
        high, low = low, high
    high = max(high, open_, close)
    low = min(low, open_, close)
    return open_, high, low, close, label


def _direction(patch: np.ndarray, saturated: np.ndarray, body_rows: np.ndarray) -> str:
    red = green = 0
    body = patch[body_rows]
    mask = saturated[body_rows]
    pixels = body[mask]
    for hue, sat, _val in pixels:
        if int(sat) < 40:
            continue
        hue_i = int(hue)
        if hue_i <= 12 or hue_i >= 168:
            red += 1
        elif 40 <= hue_i <= 95:
            green += 1
    if red >= 4 and red > green:
        return "red"
    if green >= 4 and green > red:
        return "green"
    return ""


def _price_at(y: float, slope: float, intercept: float) -> float:
    return round(float(intercept + slope * y), 3)


def _choose_scale(
    image: np.ndarray,
    candle_right: int,
    previous: tuple[float, float] | None,
) -> tuple[float, float] | None:
    fresh = _price_scale(image, candle_right)
    height = image.shape[0]
    if fresh is not None and previous is not None:
        slope, intercept, count = fresh
        jumped = abs(_mid_price((slope, intercept), height) - _mid_price(previous, height)) > 30
        if jumped and count < 6:
            log.info("chart_price_scale_rejected", step=round(slope, 5), top=round(intercept, 3))
            fresh = None
        else:
            return slope, intercept
    if fresh is not None:
        return fresh[0], fresh[1]
    if previous is not None and _span_ok(previous, height):
        log.info("chart_price_scale_held", step=round(previous[0], 5), top=round(previous[1], 3))
        return previous
    return None


def _mid_price(scale: tuple[float, float], height: int) -> float:
    slope, intercept = scale
    return float(intercept + slope * (height / 2.0))


def _span_ok(scale: tuple[float, float], height: int) -> bool:
    slope, _intercept = scale
    span = abs(slope) * max(height, 1)
    return slope < 0 and 3.0 <= span <= 100.0


def _price_scale(image: np.ndarray, candle_right: int) -> tuple[float, float, int] | None:
    labels = _price_labels(image, candle_right)
    if len(labels) < _MIN_LABELS:
        _fail("price numbers are not inside the chart box")
        return None
    kept = _clustered_labels(_same_size_labels(labels))
    if len(kept) < _MIN_LABELS:
        _fail("price numbers could not be read")
        return None
    fitted = _fit_labels(kept)
    if fitted is None:
        _fail("price numbers do not sit on a straight scale")
        return None
    slope, intercept, used = fitted
    if not _span_ok((slope, intercept), image.shape[0]):
        _fail("price scale does not match the chart")
        return None
    log.info(
        "chart_price_scale",
        labels=len(used),
        top=round(float(intercept), 3),
        step=round(float(slope), 5),
    )
    return slope, intercept, len(used)


def _clustered_labels(labels: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Keep the prices that sit near each other. A clock like 1845 is not 4160."""
    ordered = sorted(labels, key=lambda item: item[1])
    best: list[tuple[float, float]] = []
    start = 0
    for end, (_y, price) in enumerate(ordered):
        while ordered[start][1] < price - 80:
            start += 1
        window = ordered[start : end + 1]
        if len(window) > len(best):
            best = window
    return best


def _same_size_labels(labels: list[tuple[float, float]]) -> list[tuple[float, float]]:
    widths = [len(str(int(price))) for _y, price in labels]
    usual = max(set(widths), key=widths.count)
    return [(y, price) for (y, price), width in zip(labels, widths, strict=True) if width == usual]


def _fit_labels(
    labels: list[tuple[float, float]],
) -> tuple[float, float, list[tuple[float, float]]] | None:
    points = list(labels)
    for _ in range(3):
        if len(points) < _MIN_LABELS:
            return None
        slope, intercept, resid = _line(points)
        if slope >= 0:
            return None
        worst = int(np.argmax(resid))
        if resid[worst] <= 0.35 or len(points) <= _MIN_LABELS:
            if float(np.max(resid)) > 0.35:
                return None
            return slope, intercept, points
        del points[worst]
    return None


def _line(points: list[tuple[float, float]]) -> tuple[float, float, np.ndarray]:
    ys = np.array([y for y, _price in points], dtype=float)
    prices = np.array([price for _y, price in points], dtype=float)
    slope, intercept = np.polyfit(ys, prices, 1)
    resid = np.abs(prices - (intercept + slope * ys))
    return float(slope), float(intercept), resid


def _price_labels(image: np.ndarray, candle_right: int) -> list[tuple[float, float]]:
    height, width = image.shape[:2]
    x0 = min(width - 1, int(candle_right) + 4)
    if width - x0 < 16:
        return []
    strip = image[:, x0:]
    mask = _text_mask(strip)
    if mask is None or not np.any(mask):
        return []
    seen: list[tuple[float, str]] = []
    for top, bottom in _text_lines(mask):
        center = (top + bottom - 1) / 2.0
        if center > height * 0.88:
            continue
        line = mask[top:bottom]
        seen.append((center, _read_line(line)))
    labels = _repair_axis(seen)
    if len(labels) < _MIN_LABELS and seen:
        log.info("chart_axis_text", lines=[text for _y, text in seen])
    return labels


def _junk_fraction(frac: str) -> bool:
    """Trailing zeros on 4,188.000 are often read as 8. That is not a real decimal."""
    return bool(frac) and set(frac) <= set("08")


def _axis_parts(text: str) -> tuple[str, str] | None:
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ",.")
    if not cleaned or not cleaned[0].isdigit():
        return None
    parts = [part for part in re.split(r"[,.]", cleaned) if part]
    if not parts or any(not part.isdigit() for part in parts):
        return None
    if len(parts) == 1:
        return parts[0], ""
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 3 and len(parts[1]) == 3:
        return parts[0] + parts[1], parts[2]
    if len(parts) == 3 and _junk_fraction(parts[2]):
        return parts[0] + parts[1], parts[2]
    return None


def _repair_axis(lines: list[tuple[float, str]]) -> list[tuple[float, float]]:
    """Snap 4,188.8 and 4,18.8 back onto 4,188 and 4,184. Keep a real decimal such as 4,122.325."""
    exact: list[tuple[float, float]] = []
    anchors: list[tuple[float, float, str]] = []
    weak: list[tuple[float, str]] = []
    for y, text in lines:
        split = _axis_parts(text)
        if split is None:
            continue
        whole, frac = split
        if frac and not _junk_fraction(frac):
            price = _parse_price(text)
            if price is not None:
                exact.append((y, price))
            continue
        if len(whole) >= 4:
            anchors.append((y, float(whole), whole))
        elif len(whole) >= 2:
            weak.append((y, whole))
    guides = _clustered_labels([(y, price) for y, price, _whole in anchors] + exact)
    if len(guides) < 2:
        return guides
    slope, intercept, _resid = _line(guides)
    repaired: list[tuple[float, float]] = list(exact)
    for y, price, _whole in anchors:
        predicted = intercept + slope * y
        rounded = float(round(predicted))
        chosen = rounded if abs(price - rounded) <= 1.25 else price
        if abs(chosen - predicted) <= 1.5:
            repaired.append((y, chosen))
    for y, whole in weak:
        predicted = intercept + slope * y
        rounded = float(round(predicted))
        if abs(predicted - rounded) <= 0.75 and str(int(rounded)).startswith(whole):
            repaired.append((y, rounded))
    return repaired


def _text_mask(strip: np.ndarray) -> np.ndarray | None:
    import cv2

    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    background = float(np.median(gray))
    if background > 140:
        mask = gray < background - 40
    else:
        mask = gray > background + 40
    mask = mask & (hsv[:, :, 1] < 50)
    if mask.shape[1] == 0 or mask.shape[0] == 0:
        return None
    column = mask.mean(axis=0)
    row = mask.mean(axis=1)
    mask[:, column > 0.45] = False
    mask[row > 0.55, :] = False
    return mask


def _text_lines(mask: np.ndarray) -> list[tuple[int, int]]:
    rows = mask.sum(axis=1) >= 2
    lines: list[tuple[int, int]] = []
    start: int | None = None
    for y, on in enumerate(rows.tolist()):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= 6:
                lines.append((start, y))
            start = None
    if start is not None and len(rows) - start >= 6:
        lines.append((start, len(rows)))
    return lines


def _read_line(line: np.ndarray) -> str:
    glyphs = _glyphs(line)
    if not glyphs:
        return ""
    line_h = line.shape[0]
    chars: list[str] = []
    for x0, x1, y0, y1 in glyphs:
        crop = line[y0:y1, x0:x1]
        mark = _punctuation(crop, line_h, y0, y1)
        if mark is not None:
            chars.append(mark)
            continue
        chars.append(_match_glyph(crop, line_h))
    return "".join(chars)


def _glyphs(line: np.ndarray) -> list[tuple[int, int, int, int]]:
    columns = line.any(axis=0)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x, on in enumerate(columns.tolist()):
        if on and start is None:
            start = x
        elif not on and start is not None:
            if x - start >= 1:
                runs.append((start, x))
            start = None
    if start is not None and len(columns) - start >= 1:
        runs.append((start, len(columns)))
    glyphs: list[tuple[int, int, int, int]] = []
    for x0, x1 in runs:
        rows = np.where(line[:, x0:x1].any(axis=1))[0]
        if len(rows) == 0:
            continue
        glyphs.append((x0, x1, int(rows.min()), int(rows.max()) + 1))
    return glyphs


def _punctuation(crop: np.ndarray, line_h: int, y0: int, y1: int) -> str | None:
    if crop.size == 0 or line_h <= 0:
        return None
    height, width = crop.shape[:2]
    if height > line_h * 0.5 or width > line_h * 0.5:
        return None
    if y1 >= line_h * 0.9 and height >= 2:
        return ","
    if y0 >= line_h * 0.45:
        return "."
    return None


def _match_glyph(crop: np.ndarray, line_h: int) -> str:
    height = max(8, min(28, int(line_h)))
    scores: dict[str, float] = {}
    for char in "0123456789":
        glyphs = _templates(height, char)
        if not glyphs:
            continue
        scores[char] = max(_iou(crop, glyph) for glyph in glyphs)
    if not scores:
        return ""
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < 0.42 or best_score - second < 0.04:
        return ""
    return best


def _iou(left: np.ndarray, right: np.ndarray) -> float:
    import cv2

    a = _content(left)
    b = _content(right)
    if a.size == 0 or b.size == 0:
        return 0.0
    aa = cv2.resize(a.astype(np.uint8), (12, 18), interpolation=cv2.INTER_NEAREST) > 0
    bb = cv2.resize(b.astype(np.uint8), (12, 18), interpolation=cv2.INTER_NEAREST) > 0
    union = int(np.logical_or(aa, bb).sum())
    if union == 0:
        return 0.0
    return float(np.logical_and(aa, bb).sum()) / float(union)


def _content(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return np.zeros((1, 1), dtype=bool)
    return mask[int(ys.min()) : int(ys.max()) + 1, int(xs.min()) : int(xs.max()) + 1]


@lru_cache(maxsize=256)
def _templates(height: int, char: str) -> tuple[np.ndarray, ...]:
    glyphs = [_render_char(char, height, font) for font in _FONTS]
    return tuple(glyph for glyph in glyphs if glyph is not None)


def _parse_price(text: str) -> float | None:
    """Read 4122.325, 4,122.325, or a picture where the comma and the dot look the same."""
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ",.")
    if not cleaned or not cleaned[0].isdigit():
        return None
    parts = [part for part in re.split(r"[,.]", cleaned) if part]
    if not parts or any(not part.isdigit() for part in parts):
        return None
    if len(parts) == 1:
        whole, frac = parts[0], ""
    elif len(parts) == 2:
        whole, frac = parts
    elif len(parts) == 3 and len(parts[1]) == 3:
        whole, frac = parts[0] + parts[1], parts[2]
    else:
        return None
    if len(whole) < 3 or len(frac) > 5:
        return None
    try:
        value = float(whole if not frac else f"{whole}.{frac}")
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def paint_text(image: np.ndarray, text: str, x: int, y: int) -> int:
    """Draw black axis text. Returns the vertical center of the text. Tests use this."""
    glyph = _render_text(text, 16, "Arial")
    if glyph is None:
        return y
    gray, top_pad = glyph
    height, width = gray.shape[:2]
    y0 = max(0, y)
    x0 = max(0, x)
    y1 = min(image.shape[0], y0 + height)
    x1 = min(image.shape[1], x0 + width)
    if y1 <= y0 or x1 <= x0:
        return y
    patch = image[y0:y1, x0:x1]
    ink = gray[: y1 - y0, : x1 - x0] < 128
    patch[ink] = (20, 20, 20)
    return y0 + top_pad


def _render_char(char: str, height: int, font: str) -> np.ndarray | None:
    rendered = _render_text(char, height, font)
    if rendered is None:
        return None
    return rendered[0] < 128


def _render_text(text: str, height: int, font: str) -> tuple[np.ndarray, int] | None:
    bitmap = _gdi_text(text, height, font)
    if bitmap is None:
        return None
    gray, ink_top, _ink_bottom = bitmap
    center = (ink_top + bitmap[2]) / 2.0
    return gray, int(round(center))


def _gdi_text(text: str, height: int, font: str) -> tuple[np.ndarray, int, int] | None:
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    _bind_gdi(gdi32, user32)
    screen = user32.GetDC(0)
    if not screen:
        return None
    dc = gdi32.CreateCompatibleDC(screen)
    font_handle = gdi32.CreateFontW(
        -int(height),
        0,
        0,
        0,
        400,
        0,
        0,
        0,
        1,
        0,
        0,
        3,
        0,
        font,
    )
    old_font = gdi32.SelectObject(dc, font_handle)
    size = wintypes.SIZE()
    gdi32.GetTextExtentPoint32W(dc, text, len(text), ctypes.byref(size))
    width = max(4, int(size.cx) + 6)
    pixel_h = max(4, int(size.cy) + 6)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, pixel_h)
    old_bitmap = gdi32.SelectObject(dc, bitmap)
    rect = wintypes.RECT(0, 0, width, pixel_h)
    gdi32.SetBkColor(dc, 0x00FFFFFF)
    gdi32.SetTextColor(dc, 0x00000000)
    gdi32.ExtTextOutW(dc, 2, 2, 2, ctypes.byref(rect), text, len(text), None)
    info = _BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.biWidth = width
    info.biHeight = pixel_h
    info.biPlanes = 1
    info.biBitCount = 32
    info.biCompression = 0
    raw = (ctypes.c_ubyte * (width * pixel_h * 4))()
    got = gdi32.GetDIBits(dc, bitmap, 0, pixel_h, raw, ctypes.byref(info), 0)
    gdi32.SelectObject(dc, old_bitmap)
    gdi32.SelectObject(dc, old_font)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteObject(font_handle)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(0, screen)
    if not got:
        return None
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(pixel_h, width, 4)
    gray = np.flipud(pixels[:, :, 0])
    ink_rows = np.where((gray < 128).any(axis=1))[0]
    if len(ink_rows) == 0:
        return None
    return gray, int(ink_rows.min()), int(ink_rows.max())


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


_GDI_READY = False


def _bind_gdi(gdi32: ctypes.WinDLL, user32: ctypes.WinDLL) -> None:
    global _GDI_READY
    if _GDI_READY:
        return
    handle = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [handle]
    gdi32.CreateCompatibleDC.restype = handle
    gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = handle
    gdi32.CreateFontW.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPCWSTR,
    ]
    gdi32.CreateFontW.restype = handle
    gdi32.SelectObject.argtypes = [handle, handle]
    gdi32.SelectObject.restype = handle
    gdi32.GetTextExtentPoint32W.argtypes = [
        handle,
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(wintypes.SIZE),
    ]
    gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL
    gdi32.SetBkColor.argtypes = [handle, wintypes.DWORD]
    gdi32.SetBkColor.restype = wintypes.DWORD
    gdi32.SetTextColor.argtypes = [handle, wintypes.DWORD]
    gdi32.SetTextColor.restype = wintypes.DWORD
    gdi32.ExtTextOutW.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_void_p,
    ]
    gdi32.ExtTextOutW.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        handle,
        handle,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFOHEADER),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [handle]
    gdi32.DeleteDC.restype = wintypes.BOOL
    user32.GetDC.argtypes = [handle]
    user32.GetDC.restype = handle
    user32.ReleaseDC.argtypes = [handle, handle]
    user32.ReleaseDC.restype = ctypes.c_int
    _GDI_READY = True
