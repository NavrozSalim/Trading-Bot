from __future__ import annotations

import pytest

from trading_bot.config import LIVE_CONFIRMATION_PHRASE, Settings
from trading_bot.exceptions import LiveTradingBlocked


def test_defaults_are_demo_and_dry_run() -> None:
    settings = Settings(_env_file=None)
    assert settings.trading_mode.value == "DEMO"
    assert settings.dry_run is True
    assert settings.headless is False
    assert settings.is_live_allowed() is False
    settings.assert_execution_allowed()


def test_live_without_confirmation_is_blocked() -> None:
    settings = Settings(_env_file=None, trading_mode="LIVE", allow_live_trading="")
    with pytest.raises(LiveTradingBlocked):
        settings.assert_execution_allowed()


def test_live_with_wrong_phrase_is_blocked() -> None:
    settings = Settings(_env_file=None, trading_mode="LIVE", allow_live_trading="yes")
    with pytest.raises(LiveTradingBlocked):
        settings.assert_execution_allowed()


def test_live_requires_both_flags() -> None:
    settings = Settings(
        _env_file=None,
        trading_mode="LIVE",
        allow_live_trading=LIVE_CONFIRMATION_PHRASE,
    )
    assert settings.is_live_allowed() is True
    settings.assert_execution_allowed()


def test_password_is_secret() -> None:
    settings = Settings(_env_file=None, broker_password="super-secret")
    assert "super-secret" not in str(settings.broker_password)
    assert settings.broker_password.get_secret_value() == "super-secret"


def test_mode_is_normalized() -> None:
    settings = Settings(_env_file=None, trading_mode="demo")
    assert settings.trading_mode.value == "DEMO"


def test_chrome_profile_directory_optional() -> None:
    settings = Settings(_env_file=None, chrome_profile_directory="Profile 52")
    assert settings.chrome_profile_directory == "Profile 52"


def test_mt5_symbol_defaults_to_symbol() -> None:
    settings = Settings(_env_file=None, symbol="XAUUSD", mt5_symbol="")
    assert settings.mt5_symbol == "XAUUSD"


def test_execution_backend_normalized() -> None:
    settings = Settings(_env_file=None, execution_backend="mt5")
    assert settings.execution_backend == "MT5"
