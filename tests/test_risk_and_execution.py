from __future__ import annotations

import pytest

from trading_bot.exceptions import ExecutionNotEnabled, OrderVerificationError, RiskLimitError
from trading_bot.trading.executor import NullExecutor, OrderRequest
from trading_bot.trading.order_validator import OrderValidator, TicketSnapshot
from trading_bot.trading.playwright_executor import PlaywrightExecutor
from trading_bot.trading.risk_manager import RiskManager


def test_null_executor_does_not_claim_success() -> None:
    import asyncio

    order = OrderRequest(
        symbol="BTCUSD",
        direction="BUY",
        quantity=0.01,
        stop_loss=67900,
        take_profit=68800,
        signal_id="BTCUSD_5M_2026-09-21T12:30_BUY",
    )
    result = asyncio.run(NullExecutor().open_long(order))
    assert result.accepted is False
    assert result.dry_run is True
    assert result.verified is False


@pytest.mark.asyncio
async def test_playwright_executor_refuses_phase_1_2() -> None:
    order = OrderRequest(
        symbol="BTCUSD",
        direction="BUY",
        quantity=0.01,
        stop_loss=1,
        take_profit=2,
        signal_id="x",
    )
    with pytest.raises(ExecutionNotEnabled):
        await PlaywrightExecutor().open_long(order)


def test_risk_rejects_unknown_size(settings) -> None:  # type: ignore[no-untyped-def]
    risk = RiskManager(settings)
    with pytest.raises(RiskLimitError):
        risk.assert_can_open(None)
    with pytest.raises(RiskLimitError):
        risk.assert_can_open(0)


def test_risk_daily_and_count_limits(settings) -> None:  # type: ignore[no-untyped-def]
    settings.max_open_trades = 1
    settings.max_trades_per_day = 1
    settings.max_daily_loss = 10
    settings.max_consecutive_losses = 1
    risk = RiskManager(settings)
    risk.assert_can_open(0.01)
    risk.record_open()
    with pytest.raises(RiskLimitError, match="MAX_OPEN_TRADES"):
        risk.assert_can_open(0.01)
    risk.record_close(-11)
    with pytest.raises(RiskLimitError):
        risk.assert_can_open(0.01)


def test_ticket_mismatch_blocks_confirm() -> None:
    validator = OrderValidator()
    expected = OrderRequest(
        symbol="BTCUSD",
        direction="BUY",
        quantity=0.01,
        stop_loss=67900,
        take_profit=68800,
        signal_id="id",
    )
    ticket = TicketSnapshot(
        side="SELL",
        symbol="BTCUSD",
        quantity="0.01",
        stop_loss="67900",
        take_profit="68800",
    )
    with pytest.raises(OrderVerificationError, match="side"):
        validator.ticket_matches(expected, ticket)


def test_ticket_missing_fields_block_confirm() -> None:
    validator = OrderValidator()
    expected = OrderRequest(
        symbol="BTCUSD",
        direction="BUY",
        quantity=0.01,
        stop_loss=67900,
        take_profit=68800,
        signal_id="id",
    )
    ticket = TicketSnapshot(side="BUY", symbol="BTCUSD", quantity="0.01", stop_loss=None, take_profit=None)
    with pytest.raises(OrderVerificationError, match="missing"):
        validator.ticket_matches(expected, ticket)
