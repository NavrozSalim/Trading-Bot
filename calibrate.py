"""Select the TradingView candle area and sample one yellow and one blue body.

Put the XAUUSD 1-minute KKC chart on screen first. Keep the zoom fixed.
Drag each box, then press Enter. Press C to cancel a box.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mss
import numpy as np

from trading_bot.market.kkc_vision import ChartVision, range_from_samples, save_vision


def _capture_monitor() -> tuple[np.ndarray, int, int]:
    with mss.mss() as capture:
        monitor = capture.monitors[1]
        shot = np.array(capture.grab(monitor))
    return shot[:, :, :3], int(monitor["left"]), int(monitor["top"])


def _select(image: np.ndarray, title: str) -> tuple[int, int, int, int] | None:
    import cv2

    box = cv2.selectROI(title, image, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    x, y, width, height = (int(v) for v in box)
    if width < 4 or height < 4:
        return None
    return x, y, width, height


def main() -> int:
    print("Leave the TradingView chart still, with one yellow candle and one blue candle visible.")
    print("Hide the floating price tag. Leave the price numbers on the right visible.")
    print("1. Drag a box around the candles AND those price numbers. Press Enter.")
    print("2. Drag a box inside a yellow candle body. Press Enter.")
    print("3. Drag a box inside a blue candle body. Press Enter.")
    image, origin_x, origin_y = _capture_monitor()
    chart = _select(image, "Candle area")
    if chart is None:
        print("Candle area was not selected.")
        return 1
    yellow_box = _select(image, "Yellow candle body")
    blue_box = _select(image, "Blue candle body")
    if yellow_box is None or blue_box is None:
        print("Yellow and blue samples are both required.")
        return 1
    import cv2

    def sample(box: tuple[int, int, int, int]) -> np.ndarray:
        x, y, width, height = box
        crop = image[y : y + height, x : x + width]
        return cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)

    yellow = range_from_samples(sample(yellow_box))
    blue = range_from_samples(sample(blue_box))
    if yellow is None or blue is None:
        print("The sample boxes did not contain enough candle color. Draw them on the candle body.")
        return 1
    x, y, width, height = chart
    save_vision(
        ChartVision(
            x=origin_x + x,
            y=origin_y + y,
            width=width,
            height=height,
            yellow=yellow,
            blue=blue,
            min_confidence=0.55,
            calibrated=True,
        )
    )
    print(f"Saved config.json. Chart area {width}x{height} at ({origin_x + x}, {origin_y + y}).")
    print("Keep this chart size and zoom. Restart the bot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
