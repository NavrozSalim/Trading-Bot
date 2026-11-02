from __future__ import annotations

from trading_bot.browser.navigation import (
    chart_url,
    page_is_configured_chart,
    page_matches_symbol,
    tradingview_interval,
)
from trading_bot.config import Settings


def test_tradingview_interval_1m() -> None:
    assert tradingview_interval("1M") == "1"
    assert tradingview_interval("5M") == "5"
    assert tradingview_interval("1H") == "60"


def test_chart_url_injects_xauusd(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        _env_file=None,
        broker_url="https://www.tradingview.com/chart/",
        trading_page_url="https://www.tradingview.com/chart/",
        symbol="XAUUSD",
        timeframe="1M",
        logs_dir=tmp_path / "logs",
        screenshots_dir=tmp_path / "shots",
        browser_profile_dir=tmp_path / "profile",
        selectors_file=tmp_path / "selectors.yaml",
    )
    url = chart_url(settings)
    assert "symbol=XAUUSD" in url
    assert "interval=1" in url


def test_saved_layout_is_not_any_xauusd_chart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        _env_file=None,
        broker_url="https://www.tradingview.com/chart/qCUHtEC4/?symbol=OANDA:XAUUSD",
        trading_page_url="https://www.tradingview.com/chart/qCUHtEC4/?symbol=OANDA:XAUUSD",
        symbol="XAUUSD",
        timeframe="1M",
        logs_dir=tmp_path / "logs",
        screenshots_dir=tmp_path / "shots",
        browser_profile_dir=tmp_path / "profile",
        selectors_file=tmp_path / "selectors.yaml",
    )
    url = chart_url(settings)
    assert "qCUHtEC4" in url
    assert "OANDA" in url
    assert "interval=1" in url
    oanda = "https://www.tradingview.com/chart/qCUHtEC4/?symbol=OANDA%3AXAUUSD&interval=1"
    forex = "https://www.tradingview.com/chart/other/?symbol=FOREXCOM%3AXAUUSD&interval=1"
    assert page_is_configured_chart(oanda, settings)
    assert not page_is_configured_chart(forex, settings)


def test_page_matches_symbol_from_title() -> None:
    assert page_matches_symbol(title="XAUUSD 2650.1", url="https://www.tradingview.com/chart/", symbol="XAUUSD")
    assert page_matches_symbol(title="GOLD 2650", url="", symbol="XAUUSD")
    assert not page_matches_symbol(title="AAPL 339.75 ▲ +0.23%", url="", symbol="XAUUSD")
