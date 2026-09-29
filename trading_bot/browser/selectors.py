"""Central selector registry.

All Playwright locators used by the bot MUST be declared here (or in the
YAML overlay loaded from SELECTORS_FILE). Do not scatter CSS across modules.

Priority for values you paste in:
1. data-testid
2. aria-label
3. role (Playwright role=... syntax)
4. text (Playwright text=... / get_by_text)
5. stable CSS

Avoid XPath unless the broker exposes nothing else.

Empty string means "not configured yet" — the bot must refuse to click.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from playwright.async_api import Locator, Page

from trading_bot.exceptions import SelectorNotConfigured
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.selectors")

# Built-in placeholders. Replace via config/selectors.yaml produced by --setup.
SELECTORS: dict[str, str] = {
    # Authentication
    "login_username": "",
    "login_password": "",
    "login_submit": "",
    "logged_in_indicator": "",
    "login_error": "",
    "captcha": "",
    "otp_input": "",
    "account_name": "",
    # Market / page identity
    "symbol_display": '[data-name="legend-source-title"], [data-name="header-toolbar-symbol-search"]',
    "timeframe_display": '[data-name="header-toolbar-intervals"]',
    "symbol_search_button": '[data-name="header-toolbar-symbol-search"]',
    "symbol_search_input": '[data-name="symbol-search-items-dialog"] input',
    "price_display": "",
    "trading_panel": "",
    "buy_sell_controls": "",
    "positions_panel": "",
    "open_positions": "",
    # Order ticket
    "buy_button": "",
    "sell_button": "",
    "quantity_input": "",
    "stop_loss_input": "",
    "take_profit_input": "",
    "confirm_order": "",
    "order_ticket": "",
    "order_ticket_side": "",
    "order_ticket_symbol": "",
    "order_ticket_quantity": "",
    "order_ticket_stop_loss": "",
    "order_ticket_take_profit": "",
    "order_confirmation_message": "",
    "close_position": "",
    # Safety / UI state
    "error_modal": "",
    "confirmation_popup": "",
    "insufficient_funds": "",
    "loading_overlay": "",
    "maintenance_page": "",
    "disconnected_banner": "",
    "session_expired": "",
}

REQUIRED_FOR_TRADING: tuple[str, ...] = (
    "buy_button",
    "sell_button",
    "quantity_input",
    "stop_loss_input",
    "take_profit_input",
    "confirm_order",
    "open_positions",
    "close_position",
    "symbol_display",
    "price_display",
    "trading_panel",
    "logged_in_indicator",
)

REQUIRED_FOR_LOGIN: tuple[str, ...] = (
    "login_username",
    "login_password",
    "login_submit",
)

# Selectors that --test-selectors checks without ever submitting an order.
SELECTOR_TEST_KEYS: tuple[str, ...] = (
    "buy_button",
    "sell_button",
    "quantity_input",
    "stop_loss_input",
    "take_profit_input",
    "confirm_order",
    "open_positions",
    "close_position",
)

# Common CAPTCHA / 2FA patterns. These are detection-only — never solved.
BUILTIN_CAPTCHA_LOCATORS: tuple[str, ...] = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[title*="reCAPTCHA" i]',
    '[data-testid*="captcha" i]',
    '[aria-label*="captcha" i]',
)

BUILTIN_OTP_LOCATORS: tuple[str, ...] = (
    'input[autocomplete="one-time-code"]',
    'input[name*="otp" i]',
    'input[name*="totp" i]',
    '[data-testid*="otp" i]',
    '[data-testid*="2fa" i]',
    '[aria-label*="verification code" i]',
    '[aria-label*="authenticator" i]',
)

BUILTIN_LOGGED_IN_HINTS: tuple[str, ...] = (
    '[data-testid*="account" i]',
    '[data-testid*="logout" i]',
    '[aria-label*="logout" i]',
    '[aria-label*="account" i]',
)


@dataclass(frozen=True)
class SelectorProbeResult:
    name: str
    selector: str
    configured: bool
    found: bool
    visible: bool | None
    enabled: bool | None
    count: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.configured and self.found and bool(self.visible)


class SelectorRegistry:
    def __init__(self, overlay_path: Path | None = None) -> None:
        self._selectors: dict[str, str] = dict(SELECTORS)
        self._overlay_path = overlay_path
        if overlay_path is not None:
            self.load_overlay(overlay_path)

    @property
    def all(self) -> dict[str, str]:
        return dict(self._selectors)

    def get_raw(self, name: str) -> str:
        if name not in self._selectors:
            raise KeyError(f"Unknown selector name: {name}")
        return (self._selectors.get(name) or "").strip()

    def is_configured(self, name: str) -> bool:
        return bool(self.get_raw(name))

    def require(self, name: str) -> str:
        value = self.get_raw(name)
        if not value:
            raise SelectorNotConfigured(
                f"Selector '{name}' is empty. Run python main.py --setup "
                "or edit config/selectors.yaml."
            )
        return value

    def locator(self, page: Page, name: str) -> Locator:
        return page.locator(self.require(name))

    def optional_locator(self, page: Page, name: str) -> Locator | None:
        raw = self.get_raw(name)
        if not raw:
            return None
        return page.locator(raw)

    def load_overlay(self, path: Path) -> None:
        if not path.exists():
            log.info("selectors_overlay_missing", path=str(path))
            return
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Selector overlay must be a mapping: {path}")
        loaded = 0
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            text = "" if value is None else str(value).strip()
            if key not in self._selectors:
                log.warning("unknown_selector_in_overlay", name=key)
                self._selectors[key] = text
            else:
                self._selectors[key] = text
            if text:
                loaded += 1
        log.info("selectors_overlay_loaded", path=str(path), configured=loaded)

    def save_overlay(self, path: Path | None = None) -> Path:
        target = path or self._overlay_path
        if target is None:
            raise ValueError("No selectors overlay path configured.")
        target.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: v for k, v in self._selectors.items()}
        with target.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(serializable, handle, sort_keys=True, allow_unicode=True)
        log.info("selectors_overlay_saved", path=str(target))
        return target

    def set(self, name: str, value: str) -> None:
        self._selectors[name] = value.strip()

    def missing(self, names: tuple[str, ...] | list[str]) -> list[str]:
        return [name for name in names if not self.is_configured(name)]

    async def probe(self, page: Page, name: str, *, timeout_ms: int = 2_000) -> SelectorProbeResult:
        raw = self.get_raw(name) if name in self._selectors else ""
        if not raw:
            return SelectorProbeResult(
                name=name,
                selector="",
                configured=False,
                found=False,
                visible=None,
                enabled=None,
                count=0,
                error="not configured",
            )
        locator = page.locator(raw)
        try:
            count = await locator.count()
            if count == 0:
                return SelectorProbeResult(
                    name=name,
                    selector=raw,
                    configured=True,
                    found=False,
                    visible=False,
                    enabled=None,
                    count=0,
                )
            visible = await locator.first.is_visible()
            enabled: bool | None
            try:
                enabled = await locator.first.is_enabled()
            except Exception:  # noqa: BLE001 — not every node supports enabled
                enabled = None
            return SelectorProbeResult(
                name=name,
                selector=raw,
                configured=True,
                found=True,
                visible=visible,
                enabled=enabled,
                count=count,
            )
        except Exception as exc:  # noqa: BLE001
            return SelectorProbeResult(
                name=name,
                selector=raw,
                configured=True,
                found=False,
                visible=None,
                enabled=None,
                count=0,
                error=str(exc),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "selectors": self.all,
            "missing_trading": self.missing(REQUIRED_FOR_TRADING),
            "missing_login": self.missing(REQUIRED_FOR_LOGIN),
        }
