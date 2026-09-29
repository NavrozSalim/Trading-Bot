"""Browser automation package."""

from trading_bot.browser.browser_manager import BrowserManager
from trading_bot.browser.login import AuthStatus, LoginManager
from trading_bot.browser.navigation import Navigator, normalize_symbol
from trading_bot.browser.page_state import PageState
from trading_bot.browser.selectors import SELECTORS, SelectorRegistry

__all__ = [
    "AuthStatus",
    "BrowserManager",
    "LoginManager",
    "Navigator",
    "PageState",
    "SELECTORS",
    "SelectorRegistry",
    "normalize_symbol",
]
