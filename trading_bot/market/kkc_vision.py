"""Read KKC candle colors from the on-screen TradingView chart.

The chart area is captured in memory. Each candle body is classified in HSV.
A low-confidence body is unknown, and an unknown color is not a trade.
Each body is snapped to the minute slot it sits on. A missing bar or the
price-scale letters cannot slide a yellow or blue onto a later candle.
The rightmost candle is still forming and is not classified.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from trading_bot.config import PROJECT_ROOT
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.kkc_vision")

CONFIG_PATH = PROJECT_ROOT / "config.json"
MIN_CONFIDENCE = 0.55


@dataclass(frozen=True)
class HsvRange:
    h_min: int
    h_max: int
    s_min: int
    s_max: int
    v_min: int
    v_max: int

    def contains(self, hue: int, sat: int, val: int) -> bool:
        if not (self.s_min <= sat <= self.s_max and self.v_min <= val <= self.v_max):
            return False
        if self.h_min <= self.h_max:
            return self.h_min <= hue <= self.h_max
        return hue >= self.h_min or hue <= self.h_max

    def as_dict(self) -> dict[str, int]:
        return {
            "h_min": self.h_min,
            "h_max": self.h_max,
            "s_min": self.s_min,
            "s_max": self.s_max,
            "v_min": self.v_min,
            "v_max": self.v_max,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HsvRange:
        return cls(
            int(raw["h_min"]),
            int(raw["h_max"]),
            int(raw["s_min"]),
            int(raw["s_max"]),
            int(raw["v_min"]),
            int(raw["v_max"]),
        )


@dataclass(frozen=True)
class ChartVision:
    x: int
    y: int
    width: int
    height: int
    yellow: HsvRange
    blue: HsvRange
    min_confidence: float
    calibrated: bool

    @property
    def ready(self) -> bool:
        return self.calibrated and self.width > 20 and self.height > 20


def load_vision(path: Path = CONFIG_PATH) -> ChartVision:
    if not path.exists():
        return _empty_vision()
    raw = json.loads(path.read_text(encoding="utf-8"))
    roi = raw.get("roi") or {}
    hsv = raw.get("hsv") or {}
    yellow = hsv.get("yellow")
    blue = hsv.get("blue")
    if not yellow or not blue:
        return _empty_vision()
    return ChartVision(
        x=int(roi.get("x", 0)),
        y=int(roi.get("y", 0)),
        width=int(roi.get("width", 0)),
        height=int(roi.get("height", 0)),
        yellow=HsvRange.from_dict(yellow),
        blue=HsvRange.from_dict(blue),
        min_confidence=float(raw.get("min_confidence", MIN_CONFIDENCE)),
        calibrated=bool(raw.get("calibrated")),
    )


def save_vision(vision: ChartVision, path: Path = CONFIG_PATH) -> None:
    payload = {
        "roi": {"x": vision.x, "y": vision.y, "width": vision.width, "height": vision.height},
        "hsv": {"yellow": vision.yellow.as_dict(), "blue": vision.blue.as_dict()},
        "min_confidence": vision.min_confidence,
        "calibrated": vision.calibrated,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _empty_vision() -> ChartVision:
    blank = HsvRange(0, 0, 0, 0, 0, 0)
    return ChartVision(0, 0, 0, 0, blank, blank, MIN_CONFIDENCE, False)


def range_from_samples(pixels: np.ndarray) -> HsvRange | None:
    """Build an HSV window from sampled body pixels. OpenCV hue is 0-179."""
    if pixels.size == 0:
        return None
    usable = pixels[pixels[:, 1] > 40]
    if len(usable) < 8:
        return None
    hue = np.sort(usable[:, 0].astype(int))
    sat = np.sort(usable[:, 1].astype(int))
    val = np.sort(usable[:, 2].astype(int))
    low = max(0, len(usable) // 20)
    high = max(low + 1, len(usable) - low - 1)
    return HsvRange(
        h_min=max(0, int(hue[low]) - 6),
        h_max=min(179, int(hue[high]) + 6),
        s_min=max(20, int(sat[low]) - 40),
        s_max=255,
        v_min=max(20, int(val[low]) - 40),
        v_max=255,
    )


def colors_from_bgr(image: np.ndarray, vision: ChartVision, count: int) -> list[str]:
    """Label the last `count` closed candles, one slot per minute.

    The rightmost candle is still forming and is not labeled. Every other
    body is placed by how many minutes it sits left of that forming candle.
    """
    if count <= 0 or image.size == 0 or not vision.ready:
        return []
    import cv2

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, _width = hsv.shape[:2]
    top = int(height * 0.08)
    bottom = int(height * 0.92)
    if bottom <= top:
        return [""] * count
    ink = np.count_nonzero(hsv[top:bottom, :, 1] > 35, axis=0) >= 3
    runs = _candle_runs(ink)
    if len(runs) < 2:
        return [""] * count
    pitch = _candle_pitch(runs)
    forming_center = (runs[-1][0] + runs[-1][1]) / 2.0
    labels = [""] * count
    distance = [1.0] * count
    for start, end in runs[:-1]:
        center = (start + end) / 2.0
        minutes = (forming_center - center) / pitch
        slot_from_right = int(np.floor(minutes + 0.5))
        if slot_from_right < 1 or slot_from_right > count:
            continue
        if abs(minutes - slot_from_right) > 0.45:
            continue
        slot = count - slot_from_right
        label = _label_bar(hsv, top, bottom, start, end, vision)
        if not _prefer_label(labels[slot], label, distance[slot], abs(minutes - slot_from_right)):
            continue
        labels[slot] = label
        distance[slot] = abs(minutes - slot_from_right)
    log.info("kkc_candle_grid", pitch=round(pitch, 3), runs=len(runs) - 1, slots=count)
    return labels


def _candle_runs(ink: np.ndarray) -> list[tuple[int, int]]:
    """Candle bodies, with the price-scale letters on the right removed."""
    runs = _ink_runs(ink)
    runs = _cut_price_scale(runs)
    runs = _drop_right_outlier(runs)
    return _drop_wide_runs(runs)


def _ink_runs(ink: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x, on in enumerate(ink.tolist()):
        if on and start is None:
            start = x
        elif not on and start is not None:
            if x - start >= 2:
                runs.append((start, x))
            start = None
    if start is not None and len(ink) - start >= 2:
        runs.append((start, len(ink)))
    return runs


def _cut_price_scale(runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop a tight cluster on the right sitting past a wide gap. That is the price label."""
    if len(runs) < 4:
        return runs
    centers = [(start + end) / 2.0 for start, end in runs]
    gaps = [centers[index + 1] - centers[index] for index in range(len(centers) - 1)]
    cut: int | None = None
    for index, gap in enumerate(gaps):
        left = gaps[:index]
        if len(left) < 2:
            continue
        typical = float(np.median(left))
        if typical <= 0 or gap < typical * 1.55:
            continue
        right = runs[index + 1 :]
        if len(right) < 2:
            continue
        cluster_width = right[-1][1] - right[0][0]
        if cluster_width < typical * 5:
            cut = index
    if cut is None:
        return runs
    return runs[: cut + 1]


def _drop_right_outlier(runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop one odd blob that sits to the right of the last candle."""
    if len(runs) < 3:
        return runs
    centers = [(start + end) / 2.0 for start, end in runs]
    gaps = [centers[index + 1] - centers[index] for index in range(len(centers) - 1)]
    typical = float(np.median(gaps[:-1]))
    last_width = runs[-1][1] - runs[-1][0]
    median_width = float(np.median([end - start for start, end in runs[:-1]]))
    odd_width = median_width > 0 and (
        last_width > median_width * 2.2 or last_width < median_width * 0.7
    )
    if typical > 0 and gaps[-1] > typical * 1.6 and odd_width:
        return runs[:-1]
    return runs


def _drop_wide_runs(runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop a run much wider than a candle. A price tag is wide. A candle is not."""
    if len(runs) < 3:
        return runs
    widths = [end - start for start, end in runs]
    median = float(np.median(widths))
    if median <= 0:
        return runs
    last = runs[-1]
    kept = [run for run in runs[:-1] if (run[1] - run[0]) <= median * 2.2]
    if (last[1] - last[0]) <= median * 2.2:
        kept.append(last)
    return kept


def _candle_pitch(runs: list[tuple[int, int]]) -> float:
    """Pixel distance of one minute, measured across the whole row of candles."""
    centers = [(start + end) / 2.0 for start, end in runs]
    if len(centers) < 2:
        return float(max(2, runs[0][1] - runs[0][0]))
    neighbor = [centers[index + 1] - centers[index] for index in range(len(centers) - 1)]
    rough = float(np.median(neighbor))
    single = [gap for gap in neighbor if rough * 0.75 <= gap <= rough * 1.25]
    if len(single) >= 3:
        rough = float(np.median(single))
    if rough < 2:
        rough = 2.0
    span = centers[-1] - centers[0]
    steps = max(1, int(np.floor(span / rough + 0.5)))
    return max(2.0, span / steps)


def _prefer_label(current: str, new: str, current_distance: float, new_distance: float) -> bool:
    if current == "":
        return True
    if new == "":
        return False
    return new_distance < current_distance


def _label_bar(
    hsv: np.ndarray,
    top: int,
    bottom: int,
    start: int,
    end: int,
    vision: ChartVision,
) -> str:
    body = _body_pixels(hsv, top, bottom, start, end)
    if len(body) < 4:
        return ""
    yellow = blue = 0
    for hue, sat, val in body:
        if vision.yellow.contains(int(hue), int(sat), int(val)):
            yellow += 1
        elif vision.blue.contains(int(hue), int(sat), int(val)):
            blue += 1
    total = len(body)
    if yellow >= blue and yellow / total >= vision.min_confidence:
        return "yellow"
    if blue > yellow and blue / total >= vision.min_confidence:
        return "blue"
    return ""


def _body_pixels(hsv: np.ndarray, top: int, bottom: int, start: int, end: int) -> np.ndarray:
    """Pixels in the candle body. A thin wick is not the color source."""
    patch = hsv[top:bottom, start:end]
    if patch.size == 0:
        return np.empty((0, 3), dtype=patch.dtype)
    saturated = patch[:, :, 1] > 35
    width = end - start
    row_fill = saturated.sum(axis=1)
    body_rows = row_fill >= max(2, int(width * 0.45))
    if not np.any(body_rows):
        chosen = patch[saturated]
    else:
        chosen = patch[body_rows][saturated[body_rows]]
    return chosen.reshape(-1, 3)


class ColorMemory:
    """Keep a yellow or blue that has been seen twice. A blank picture does not erase it."""

    def __init__(self) -> None:
        self.confirmed: dict[str, str] = {}
        self._seen_once: dict[str, str] = {}

    def apply(self, timestamps: list[str], fresh: list[str] | None) -> list[str] | None:
        if fresh is None:
            return None
        alive = set(timestamps)
        self.confirmed = {ts: color for ts, color in self.confirmed.items() if ts in alive}
        self._seen_once = {ts: color for ts, color in self._seen_once.items() if ts in alive}
        reading = list(fresh)
        if len(reading) < len(timestamps):
            reading = [""] * (len(timestamps) - len(reading)) + reading
        elif len(reading) > len(timestamps):
            reading = reading[-len(timestamps) :]
        held: list[str] = []
        for ts, seen in zip(timestamps, reading, strict=True):
            if seen in ("yellow", "blue"):
                if self.confirmed.get(ts) == seen or self._seen_once.get(ts) == seen:
                    self.confirmed[ts] = seen
                    self._seen_once.pop(ts, None)
                else:
                    self._seen_once[ts] = seen
            else:
                self._seen_once.pop(ts, None)
            held.append(self.confirmed.get(ts, ""))
        log.info(
            "kkc_colors_held",
            yellow=held.count("yellow"),
            blue=held.count("blue"),
            unknown=held.count(""),
        )
        return held


class ScreenColorReader:
    """Capture the calibrated chart area and return yellow/blue labels."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self.vision = load_vision(path)
        self._market_memory = ColorMemory()
        self._scale: tuple[float, float] | None = None
        self._candle_count = 0
        self._last_fit = 0.0
        self.last_bars = 0
        self._last_zoom = ("", 0.0)

    @property
    def ready(self) -> bool:
        return self.vision.ready

    async def colors_for(self, count: int) -> list[str]:
        self.vision = load_vision(self.path)
        if not self.vision.ready:
            log.info("kkc_roi_not_calibrated")
            return []
        try:
            image = await asyncio.to_thread(_grab_roi, self.vision)
            colors = colors_from_bgr(image, self.vision, count)
        except Exception as exc:  # noqa: BLE001
            log.warning("kkc_capture_failed", error=str(exc))
            return []
        log.info(
            "kkc_colors_read",
            yellow=colors.count("yellow"),
            blue=colors.count("blue"),
            unknown=colors.count(""),
            bars=len(colors),
        )
        return colors

    async def read_market(self):
        """Candle prices and colors from the on-screen chart. None if the scale cannot be read."""
        from trading_bot.market.chart_prices import ChartMarket, candle_run_count, market_from_bgr

        self.vision = load_vision(self.path)
        if not self.vision.ready:
            log.info("kkc_roi_not_calibrated")
            return None
        try:
            image = await asyncio.to_thread(_grab_roi, self.vision)
        except Exception as exc:  # noqa: BLE001
            log.warning("kkc_capture_failed", error=str(exc))
            return None
        self.last_bars = await asyncio.to_thread(candle_run_count, image)
        market = await asyncio.to_thread(
            market_from_bgr,
            image,
            self.vision,
            datetime.now().astimezone(),
            self._scale,
        )
        if market is None:
            return None
        if market.scale is not None:
            if self._scale is not None and abs(_scale_mid(market.scale, image.shape[0]) - _scale_mid(self._scale, image.shape[0])) > 30:
                self._market_memory = ColorMemory()
            self._scale = market.scale
        if self._candle_count and abs(len(market.closed) - self._candle_count) > 8:
            self._market_memory = ColorMemory()
        self._candle_count = len(market.closed)
        held = self._market_memory.apply(
            [str(candle.timestamp) for candle in market.closed],
            market.colors,
        )
        if held is None:
            return None
        return ChartMarket(
            closed=market.closed,
            forming=market.forming,
            colors=held,
            fresh_colors=list(market.colors),
            scale=market.scale,
            clipped=market.clipped,
            price_span=market.price_span,
        )

    async def adjust_zoom(self, action: str) -> bool:
        """One zoom step toward about 45 candles and 14 points of price. Skips the trade."""
        now = time.monotonic()
        previous, when = self._last_zoom
        opposite = {"time-in": "time-out", "time-out": "time-in", "price-in": "price-out", "price-out": "price-in"}
        if previous == opposite.get(action) and now - when < 60:
            log.info("chart_zoom_held", action=action)
            return False
        point = await self._zoom_point(action)
        if point is None:
            log.info("chart_zoom_skipped", action=action)
            return False
        self._last_zoom = (action, now)
        self._scale = None
        self._market_memory = ColorMemory()
        self._candle_count = 0
        notches = 2 if action.endswith("in") else -2
        await asyncio.to_thread(_scroll_at, point[0], point[1], notches)
        log.info("chart_zoom", action=action, x=point[0], y=point[1])
        return True

    async def _zoom_point(self, action: str) -> tuple[int, int] | None:
        if action.startswith("time"):
            return (
                self.vision.x + int(self.vision.width * 0.35),
                self.vision.y + int(self.vision.height * 0.45),
            )
        try:
            image = await asyncio.to_thread(_grab_roi, self.vision)
        except Exception as exc:  # noqa: BLE001
            log.warning("kkc_capture_failed", error=str(exc))
            return None
        return _price_scale_point(image, self.vision)

    async def fit_price_scale(self) -> bool:
        """Double-click the TradingView price scale so a candle cut off at the edge comes back into view."""
        self._scale = None
        now = time.monotonic()
        if now - self._last_fit < 45:
            return False
        try:
            image = await asyncio.to_thread(_grab_roi, self.vision)
        except Exception as exc:  # noqa: BLE001
            log.warning("kkc_capture_failed", error=str(exc))
            return False
        point = _price_scale_point(image, self.vision)
        if point is None:
            log.info("price_scale_fit_skipped", reason="price scale is not inside the chart box")
            return False
        self._last_fit = now
        await asyncio.to_thread(_double_click, point[0], point[1])
        log.info("price_scale_fit", x=point[0], y=point[1])
        return True


def _scale_mid(scale: tuple[float, float], height: int) -> float:
    slope, intercept = scale
    return float(intercept + slope * (height / 2.0))


def _price_scale_point(image: np.ndarray, vision: ChartVision) -> tuple[int, int] | None:
    """A quiet spot on the price scale, in screen pixels. None if that strip is still candles."""
    import cv2

    height, width = image.shape[:2]
    if width < 80 or height < 40:
        return None
    strip = image[:, width - 70 :]
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    if float((hsv[:, :, 1] > 50).mean()) > 0.12:
        return None
    dark = strip.mean(axis=2) < 80
    y0, y1 = int(height * 0.20), int(height * 0.50)
    quiet = [y for y in range(y0, y1) if float(dark[y].mean()) < 0.08]
    if not quiet:
        return None
    return vision.x + width - 35, vision.y + quiet[len(quiet) // 2]


def _scroll_at(x: int, y: int, notches: int) -> None:
    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    previous = POINT()
    user32.GetCursorPos(ctypes.byref(previous))
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    step = 120 if notches > 0 else -120
    for _ in range(abs(notches)):
        user32.mouse_event(0x0800, 0, 0, step, 0)
        time.sleep(0.04)
    user32.SetCursorPos(previous.x, previous.y)


def _double_click(x: int, y: int) -> None:
    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    previous = POINT()
    user32.GetCursorPos(ctypes.byref(previous))
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    for _ in range(2):
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.02)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.06)
    user32.SetCursorPos(previous.x, previous.y)


def _grab_roi(vision: ChartVision) -> np.ndarray:
    import mss

    region = {"left": vision.x, "top": vision.y, "width": vision.width, "height": vision.height}
    with mss.mss() as capture:
        shot = np.array(capture.grab(region))
    return shot[:, :, :3]
