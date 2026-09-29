"""Guarded locator access. Never click by coordinates. Never click blindly."""

from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Locator, Page

from trading_bot.browser.selectors import SelectorRegistry
from trading_bot.exceptions import PageValidationError, SelectorNotFound
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.click_guard")


@dataclass(frozen=True)
class GuardedElement:
    name: str
    locator: Locator
    selector: str
    visible: bool
    enabled: bool
    count: int


class ClickGuard:
    """Resolve a named selector only when it is visible, enabled, and unique enough."""

    def __init__(self, registry: SelectorRegistry) -> None:
        self.registry = registry

    async def resolve(
        self,
        page: Page,
        name: str,
        *,
        require_enabled: bool = True,
        allow_multiple: bool = False,
        timeout_ms: int = 5_000,
    ) -> GuardedElement:
        selector = self.registry.require(name)
        locator = page.locator(selector)
        try:
            await locator.first.wait_for(state="visible", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise SelectorNotFound(
                f"Selector '{name}' ({selector}) was not visible: {exc}"
            ) from exc

        count = await locator.count()
        if count == 0:
            raise SelectorNotFound(f"Selector '{name}' matched 0 elements.")
        if count > 1 and not allow_multiple:
            raise PageValidationError(
                f"Selector '{name}' matched {count} elements. "
                "Refusing to click an ambiguous control."
            )

        target = locator.first
        visible = await target.is_visible()
        enabled = True
        try:
            enabled = await target.is_enabled()
        except Exception:  # noqa: BLE001
            enabled = True
        if not visible:
            raise SelectorNotFound(f"Selector '{name}' exists but is not visible.")
        if require_enabled and not enabled:
            raise PageValidationError(f"Selector '{name}' is visible but disabled.")

        log.info(
            "guarded_element_resolved",
            name=name,
            selector=selector,
            count=count,
            enabled=enabled,
        )
        return GuardedElement(
            name=name,
            locator=target,
            selector=selector,
            visible=visible,
            enabled=enabled,
            count=count,
        )

    async def highlight(self, page: Page, name: str, color: str = "#ff00aa") -> bool:
        """Visual aid for --setup / --test-selectors. Does not click."""
        try:
            element = await self.resolve(
                page, name, require_enabled=False, allow_multiple=True, timeout_ms=2_000
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("highlight_failed", name=name, error=str(exc))
            return False
        await element.locator.evaluate(
            """(el, color) => {
                el.style.outline = `3px solid ${color}`;
                el.style.outlineOffset = '2px';
            }""",
            color,
        )
        return True
