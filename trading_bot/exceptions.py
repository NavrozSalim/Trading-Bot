"""Domain errors. Fail closed: uncertain UI or config never becomes a trade."""


class TradingBotError(Exception):
    """Base error for this project."""


class LiveTradingBlocked(TradingBotError):
    """LIVE mode was requested without the explicit confirmation flags."""


class ConfigurationError(TradingBotError):
    """Missing or invalid settings."""


class SelectorNotConfigured(TradingBotError):
    """A required selector is empty or missing from the registry."""


class SelectorNotFound(TradingBotError):
    """A configured selector does not match any element on the page."""


class PageValidationError(TradingBotError):
    """The current page is not the expected trading page."""


class AuthenticationError(TradingBotError):
    """Login failed or session is not authenticated."""


class HumanActionRequired(TradingBotError):
    """CAPTCHA, OTP/2FA, or another step that must be completed by a human."""


class SessionExpired(TradingBotError):
    """The broker session is no longer valid."""


class SymbolMismatch(TradingBotError):
    """Displayed instrument does not match the configured SYMBOL."""


class StalePriceError(TradingBotError):
    """Price/candle data has not updated within the allowed window."""


class DuplicateSignalError(TradingBotError):
    """This signal ID was already executed."""


class RiskLimitError(TradingBotError):
    """A risk rule would be violated by opening this trade."""


class KillSwitchActive(TradingBotError):
    """The kill switch is blocking new entries or all automation."""


class OrderVerificationError(TradingBotError):
    """The order ticket or open-position panel did not match expected values."""


class ExecutionNotEnabled(TradingBotError):
    """Order clicks are disabled in this development phase / dry-run mode."""
