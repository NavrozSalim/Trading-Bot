"""Safety package."""

from trading_bot.safety.duplicate_protection import DuplicateProtection
from trading_bot.safety.kill_switch import KillSwitch, KillSwitchState
from trading_bot.safety.page_validator import PageValidator, ValidationResult
from trading_bot.safety.stale_data import StaleDataGuard

__all__ = [
    "DuplicateProtection",
    "KillSwitch",
    "KillSwitchState",
    "PageValidator",
    "StaleDataGuard",
    "ValidationResult",
]
