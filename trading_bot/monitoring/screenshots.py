"""Screenshot capture for debugging. Never a substitute for DOM verification."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from trading_bot.monitoring.logger import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

log = get_logger("trading_bot.screenshots")

_UNSAFE_CHARS = '<>:"/\\|?* '


def _safe_token(value: str) -> str:
    cleaned = "".join("_" if c in _UNSAFE_CHARS else c for c in value.strip())
    return cleaned[:80] or "unknown"


class ScreenshotManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, suffix: str, symbol: str | None = None) -> Path:
        now = datetime.now(timezone.utc)
        day_dir = self.root_dir / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        parts = [stamp]
        if symbol:
            parts.append(_safe_token(symbol))
        parts.append(_safe_token(suffix))
        return day_dir / ("_".join(parts) + ".png")

    async def capture(
        self,
        page: Page | None,
        suffix: str,
        *,
        symbol: str | None = None,
        full_page: bool = False,
    ) -> Path | None:
        if page is None:
            log.warning("screenshot_skipped_no_page", suffix=suffix)
            return None
        path = self._path(suffix, symbol=symbol)
        try:
            await page.screenshot(path=str(path), full_page=full_page)
            log.info("screenshot_saved", path=str(path), suffix=suffix)
            return path
        except Exception as exc:  # noqa: BLE001 — screenshot failure must not crash the bot
            log.error("screenshot_failed", suffix=suffix, error=str(exc))
            return None
