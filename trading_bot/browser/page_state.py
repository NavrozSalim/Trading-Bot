"""Immutable snapshot of what the bot believes the page currently shows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PageState:
    url: str = ""
    title: str = ""
    logged_in: bool | None = None
    account_name: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    price_text: str | None = None
    trading_panel_visible: bool | None = None
    buy_sell_visible: bool | None = None
    positions_panel_visible: bool | None = None
    price_visible: bool | None = None
    captcha_detected: bool = False
    otp_detected: bool = False
    loading: bool = False
    maintenance: bool = False
    error_modal: bool = False
    session_expired: bool = False
    issues: list[str] = field(default_factory=list)
    captured_at: datetime = field(default_factory=_utcnow)

    @property
    def is_uncertain(self) -> bool:
        return bool(self.issues) or self.captcha_detected or self.loading or self.maintenance

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "logged_in": self.logged_in,
            "account_name": self.account_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "price_text": self.price_text,
            "trading_panel_visible": self.trading_panel_visible,
            "buy_sell_visible": self.buy_sell_visible,
            "positions_panel_visible": self.positions_panel_visible,
            "price_visible": self.price_visible,
            "captcha_detected": self.captcha_detected,
            "otp_detected": self.otp_detected,
            "loading": self.loading,
            "maintenance": self.maintenance,
            "error_modal": self.error_modal,
            "session_expired": self.session_expired,
            "issues": list(self.issues),
            "captured_at": self.captured_at.isoformat(),
        }
