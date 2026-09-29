from __future__ import annotations

from trading_bot.browser.selectors import SELECTORS, REQUIRED_FOR_TRADING, SelectorRegistry


def test_all_documented_keys_exist() -> None:
    for key in (
        "buy_button",
        "sell_button",
        "quantity_input",
        "stop_loss_input",
        "take_profit_input",
        "confirm_order",
        "open_positions",
        "close_position",
    ):
        assert key in SELECTORS
        assert SELECTORS[key] == ""


def test_empty_selector_is_not_configured(tmp_path) -> None:  # type: ignore[no-untyped-def]
    overlay = tmp_path / "selectors.yaml"
    overlay.write_text("buy_button: \"[data-testid='buy']\"\n", encoding="utf-8")
    registry = SelectorRegistry(overlay)
    assert registry.is_configured("buy_button") is True
    assert registry.require("buy_button") == "[data-testid='buy']"
    assert registry.is_configured("sell_button") is False
    missing = registry.missing(REQUIRED_FOR_TRADING)
    assert "sell_button" in missing
    assert "buy_button" not in missing


def test_overlay_save_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "out.yaml"
    registry = SelectorRegistry()
    registry.set("symbol_display", "[data-testid='symbol']")
    registry.save_overlay(path)
    loaded = SelectorRegistry(path)
    assert loaded.get_raw("symbol_display") == "[data-testid='symbol']"
