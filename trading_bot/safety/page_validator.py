"""Verify the bot is on the expected trading page before any action.

If expected elements disappear: STOP NEW TRADES. Do not guess. Screenshot + log.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from playwright.async_api import Page

from trading_bot.browser.navigation import Navigator, normalize_symbol
from trading_bot.browser.page_state import PageState
from trading_bot.browser.selectors import SelectorRegistry
from trading_bot.config import Settings
from trading_bot.exceptions import PageValidationError, SessionExpired, SymbolMismatch
from trading_bot.monitoring.logger import get_logger
from trading_bot.monitoring.screenshots import ScreenshotManager
from trading_bot.safety.kill_switch import KillSwitch

log = get_logger("trading_bot.page_validator")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ValidationResult:
    ok: bool
    state: PageState
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]


class PageValidator:
    def __init__(
        self,
        settings: Settings,
        registry: SelectorRegistry,
        navigator: Navigator,
        screenshots: ScreenshotManager,
        kill_switch: KillSwitch,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.navigator = navigator
        self.screenshots = screenshots
        self.kill_switch = kill_switch

    async def inspect(self, page: Page) -> PageState:
        state = PageState(url=page.url, title=await _safe_title(page))
        state.logged_in = await self._visible(page, "logged_in_indicator")
        state.account_name = await self._text(page, "account_name")
        state.symbol = await self.navigator.read_displayed_symbol(page)
        state.timeframe = await self._text(page, "timeframe_display")
        state.price_text = await self._text(page, "price_display")
        state.price_visible = await self._visible(page, "price_display")
        state.trading_panel_visible = await self._visible(page, "trading_panel")
        state.buy_sell_visible = await self._visible(page, "buy_sell_controls") or (
            await self._visible(page, "buy_button") and await self._visible(page, "sell_button")
        )
        state.positions_panel_visible = await self._visible(page, "positions_panel") or await self._visible(
            page, "open_positions"
        )
        state.captcha_detected = await self._visible(page, "captcha")
        state.otp_detected = await self._visible(page, "otp_input")
        state.loading = await self._visible(page, "loading_overlay")
        state.maintenance = await self._visible(page, "maintenance_page")
        state.error_modal = await self._visible(page, "error_modal")
        state.session_expired = await self._visible(page, "session_expired")
        if await self._visible(page, "disconnected_banner"):
            state.issues.append("market_disconnected")
        return state

    async def validate(self, page: Page, *, require_symbol: bool = True) -> ValidationResult:
        state = await self.inspect(page)
        checks: list[CheckResult] = []

        checks.append(self._check("logged_in", state.logged_in is True, "session not logged in"))
        if self.settings.account_name:
            displayed = normalize_symbol(state.account_name or "")
            expected = normalize_symbol(self.settings.account_name)
            checks.append(
                self._check(
                    "account",
                    bool(state.account_name) and displayed == expected,
                    f"account '{state.account_name}' != '{self.settings.account_name}'",
                )
            )
        if require_symbol:
            displayed = state.symbol
            expected = self.settings.symbol
            symbol_ok = bool(displayed) and normalize_symbol(displayed) == normalize_symbol(expected)
            checks.append(
                self._check(
                    "symbol",
                    symbol_ok,
                    f"displayed '{displayed}' != configured '{expected}'",
                )
            )
        checks.append(
            self._check("trading_panel", state.trading_panel_visible is True, "trading panel not visible")
        )
        checks.append(self._check("price", state.price_visible is True, "price not visible"))
        checks.append(
            self._check("buy_sell_controls", state.buy_sell_visible is True, "buy/sell controls not visible")
        )
        checks.append(
            self._check(
                "positions_panel",
                state.positions_panel_visible is True,
                "positions panel not visible",
            )
        )
        checks.append(self._check("not_loading", not state.loading, "loading overlay visible"))
        checks.append(self._check("not_maintenance", not state.maintenance, "maintenance page detected"))
        checks.append(self._check("no_error_modal", not state.error_modal, "error modal visible"))
        checks.append(self._check("session_active", not state.session_expired, "session expired banner"))

        failed = [c for c in checks if not c.ok]
        result = ValidationResult(ok=not failed, state=state, checks=checks)
        if failed:
            for item in failed:
                state.issues.append(f"{item.name}: {item.detail}")
            path = await self.screenshots.capture(
                page, "page_validation_failed", symbol=self.settings.symbol
            )
            log.error(
                "page_validation_failed",
                failures=[f"{c.name}:{c.detail}" for c in failed],
                screenshot=str(path) if path else None,
                url=page.url,
            )
            self.kill_switch.pause_new_trades(
                "page validation failed: " + "; ".join(f"{c.name}" for c in failed)
            )
        else:
            log.info("page_validation_ok", symbol=state.symbol, price=state.price_text)
        return result

    async def assert_ready_for_new_trade(self, page: Page) -> ValidationResult:
        result = await self.validate(page, require_symbol=True)
        if result.state.session_expired:
            raise SessionExpired("Session expired. Refusing new trades.")
        if not result.ok:
            symbol_fail = any(c.name == "symbol" for c in result.failures)
            if symbol_fail:
                raise SymbolMismatch(
                    f"Displayed symbol '{result.state.symbol}' does not match "
                    f"'{self.settings.symbol}'."
                )
            raise PageValidationError(
                "Trading page is not in the expected state: "
                + "; ".join(f"{c.name} ({c.detail})" for c in result.failures)
            )
        return result

    def _check(self, name: str, ok: bool, detail: str) -> CheckResult:
        return CheckResult(name=name, ok=ok, detail="" if ok else detail)

    async def _visible(self, page: Page, name: str) -> bool:
        locator = self.registry.optional_locator(page, name)
        if locator is None:
            return False
        try:
            return await locator.first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    async def _text(self, page: Page, name: str) -> str | None:
        locator = self.registry.optional_locator(page, name)
        if locator is None:
            return None
        try:
            text = (await locator.first.inner_text()).strip()
            return text or None
        except Exception:  # noqa: BLE001
            return None


async def _safe_title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:  # noqa: BLE001
        return ""
