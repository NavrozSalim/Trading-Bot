"""Runtime health snapshot used by the bot loop and later by the dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HealthSnapshot:
    bot_status: str = "stopped"
    browser_status: str = "disconnected"
    logged_in: bool | None = None
    page_valid: bool | None = None
    current_symbol: str | None = None
    configured_symbol: str | None = None
    current_price: str | None = None
    latest_signal: str | None = None
    trading_mode: str = "DEMO"
    dry_run: bool = True
    kill_switch: str = "clear"
    last_error: str | None = None
    last_screenshot: str | None = None
    updated_at: datetime = field(default_factory=_utcnow)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bot_status": self.bot_status,
            "browser_status": self.browser_status,
            "logged_in": self.logged_in,
            "page_valid": self.page_valid,
            "current_symbol": self.current_symbol,
            "configured_symbol": self.configured_symbol,
            "current_price": self.current_price,
            "latest_signal": self.latest_signal,
            "trading_mode": self.trading_mode,
            "dry_run": self.dry_run,
            "kill_switch": self.kill_switch,
            "last_error": self.last_error,
            "last_screenshot": self.last_screenshot,
            "updated_at": self.updated_at.isoformat(),
            **self.extra,
        }


class HealthMonitor:
    def __init__(self) -> None:
        self._snapshot = HealthSnapshot()

    @property
    def snapshot(self) -> HealthSnapshot:
        return self._snapshot

    def update(self, **fields: Any) -> HealthSnapshot:
        for key, value in fields.items():
            if key == "extra" and isinstance(value, dict):
                self._snapshot.extra.update(value)
                continue
            if hasattr(self._snapshot, key):
                setattr(self._snapshot, key, value)
            else:
                self._snapshot.extra[key] = value
        self._snapshot.updated_at = _utcnow()
        return self._snapshot
