"""Pydantic settings. Secrets come from the environment, never from source."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_bot.exceptions import LiveTradingBlocked


class TradingMode(str, Enum):
    DEMO = "DEMO"
    LIVE = "LIVE"


LIVE_CONFIRMATION_PHRASE = "YES_I_UNDERSTAND"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All runtime configuration. Defaults keep the bot in demo + dry-run."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Safety ---
    trading_mode: TradingMode = TradingMode.DEMO
    allow_live_trading: str = ""
    dry_run: bool = True
    headless: bool = False
    slow_mo_ms: int = Field(default=0, ge=0)
    browser_channel: Literal["chromium", "chrome", "msedge"] = "chromium"
    # Chrome named profile folder (Default, Profile 1, Profile 52, ...). Empty = Playwright Default.
    chrome_profile_directory: str = ""
    require_chrome_closed: bool = True
    chrome_debug_port: int = Field(default=9222, ge=1, le=65535)
    chrome_connect_timeout_seconds: int = Field(default=60, ge=10)
    chrome_path: str = ""
    chrome_source_user_data_dir: Path | None = None

    # --- Broker website ---
    broker_url: str = ""
    login_url: str = ""
    trading_page_url: str = ""
    account_name: str = ""
    broker_username: str = ""
    broker_password: SecretStr = SecretStr("")

    # --- Market ---
    symbol: str = "BTCUSD"
    timeframe: str = "5M"
    trade_on_candle_close: bool = True

    # --- Stale data ---
    max_price_staleness_seconds: int = Field(default=10, ge=1)

    # --- Risk (consumed from Phase 7 onward; validated here so config is ready) ---
    risk_per_trade_pct: float = Field(default=1.0, ge=0)
    max_position_size: float = Field(default=0.01, ge=0)
    max_open_trades: int = Field(default=1, ge=0)
    max_trades_per_day: int = Field(default=5, ge=0)
    max_daily_loss: float = Field(default=100.0, ge=0)
    max_consecutive_losses: int = Field(default=3, ge=0)
    sl_offset: float = Field(default=0.80, ge=0)
    tp_rr: float = Field(default=0.0, ge=0)

    execution_backend: Literal["MT5", "NULL"] = "MT5"
    open_tradingview: bool = True
    candle_poll_seconds: float = Field(default=3.0, ge=0.5)

    mt5_terminal_path: str = ""
    mt5_login: int = 0
    mt5_password: SecretStr = SecretStr("")
    mt5_server: str = ""
    mt5_symbol: str = ""
    mt5_magic: int = 26092301
    mt5_deviation: int = Field(default=30, ge=0)

    # --- Paths ---
    browser_profile_dir: Path = PROJECT_ROOT / "browser_profile"
    screenshots_dir: Path = PROJECT_ROOT / "screenshots"
    logs_dir: Path = PROJECT_ROOT / "logs"
    selectors_file: Path = PROJECT_ROOT / "config" / "selectors.yaml"
    database_url: str = "sqlite+aiosqlite:///./data/trading_bot.db"

    # --- Logging / dashboard ---
    log_level: str = "INFO"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = Field(default=8000, ge=1, le=65535)

    # --- Recovery ---
    close_positions_on_crash: bool = False

    # --- Human-pause timeouts (login CAPTCHA / 2FA) ---
    human_action_timeout_seconds: int = Field(default=600, ge=30)
    login_poll_interval_seconds: float = Field(default=2.0, ge=0.5)

    @field_validator("execution_backend", mode="before")
    @classmethod
    def _backend(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("symbol", "timeframe", "account_name", "chrome_profile_directory", mode="before")
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("mt5_login", mode="before")
    @classmethod
    def _empty_login(cls, value: object) -> object:
        if value is None or value == "":
            return 0
        return value

    @field_validator("chrome_source_user_data_dir", mode="before")
    @classmethod
    def _empty_optional_path(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @model_validator(mode="after")
    def _resolve_paths(self) -> Self:
        self.browser_profile_dir = _resolve_path(self.browser_profile_dir)
        self.screenshots_dir = _resolve_path(self.screenshots_dir)
        self.logs_dir = _resolve_path(self.logs_dir)
        self.selectors_file = _resolve_path(self.selectors_file)
        if self.chrome_source_user_data_dir is not None:
            self.chrome_source_user_data_dir = _resolve_path(self.chrome_source_user_data_dir)
        if not (self.mt5_symbol or "").strip():
            self.mt5_symbol = self.symbol
        return self

    @property
    def login_target_url(self) -> str:
        return self.login_url or self.broker_url

    @property
    def trading_target_url(self) -> str:
        return self.trading_page_url or self.broker_url

    @property
    def chrome_profile_path(self) -> Path:
        name = self.chrome_profile_directory.strip() or "Default"
        return self.browser_profile_dir / name

    def is_live_requested(self) -> bool:
        return self.trading_mode is TradingMode.LIVE

    def is_live_allowed(self) -> bool:
        return (
            self.trading_mode is TradingMode.LIVE
            and self.allow_live_trading.strip() == LIVE_CONFIRMATION_PHRASE
        )

    def assert_execution_allowed(self) -> None:
        """Block LIVE unless both explicit flags are present. Demo always ok."""
        if self.trading_mode is TradingMode.LIVE and not self.is_live_allowed():
            raise LiveTradingBlocked(
                "LIVE trading is blocked. Set TRADING_MODE=LIVE and "
                f"ALLOW_LIVE_TRADING={LIVE_CONFIRMATION_PHRASE} to enable it. "
                "Keep DRY_RUN=true until you want demo fills."
            )

    def ensure_runtime_dirs(self) -> None:
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
        self.selectors_file.parent.mkdir(parents=True, exist_ok=True)


def _resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
