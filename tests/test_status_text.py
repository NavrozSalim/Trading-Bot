from trading_bot.monitoring.logger import format_action_text


def test_status_block_shows_profit_and_plain_reason() -> None:
    text = format_action_text(
        symbol="XAUUSD",
        signal="SELL",
        signal_reason="blue_high_sweep_two_red_breakout",
        current_price=4260.036,
        trade_action="BLOCKED",
        order_result="NOT_SENT",
        error="MAX_OPEN_TRADES reached.",
        open_trades=1,
        max_open_trades=1,
        floating_pnl=4.2,
        realized_pnl=-1.5,
    )
    assert "XAUUSD  SELL skipped" in text
    assert "already at the maximum number of open trades" in text
    assert "Open trades: 1/1" in text
    assert "Open profit/loss: +4.20" in text
    assert "Closed today: -1.50" in text


def test_no_trade_uses_plain_pattern_reason() -> None:
    text = format_action_text(
        symbol="XAUUSD",
        signal="NO_TRADE",
        signal_reason="no_sweep_breakout_pattern",
        current_price=4260.0,
        trade_action="NO_TRADE",
        open_trades=0,
        max_open_trades=1,
        floating_pnl=0,
        realized_pnl=0,
    )
    assert "no trade" in text
    assert "did not match the entry pattern" in text
    assert "Open profit/loss: +0.00" in text
