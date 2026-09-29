from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_bot.database.database import Database
from trading_bot.market.candle_reader import Candle
from trading_bot.monitoring.health_monitor import HealthMonitor
from trading_bot.safety.duplicate_protection import DuplicateProtection
from trading_bot.safety.kill_switch import KillSwitch
from trading_bot.strategy.strategy import SweepBreakoutStrategy
from trading_bot.trading.engine import SignalEngine
from trading_bot.trading.executor import NullExecutor
from trading_bot.trading.position_manager import PositionManager
from trading_bot.trading.position_sizer import SymbolContract
from trading_bot.trading.risk_manager import RiskManager


def _c(minute: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        timestamp=datetime(2026, 9, 23, 12, minute, tzinfo=timezone.utc),
        open=o,
        high=h,
        low=l,
        close=c,
        is_closed=True,
    )


BUY_PATTERN = [
    _c(0, 10.0, 11.0, 9.50, 9.60),
    _c(1, 9.60, 10.0, 9.40, 9.50),
    _c(2, 9.50, 9.70, 9.20, 9.55),
    _c(3, 9.55, 9.65, 9.00, 9.60),
    _c(4, 9.40, 9.60, 9.30, 9.55),
    _c(5, 9.50, 10.20, 9.40, 9.85),
]


class FakeExecutor(NullExecutor):
    async def get_account_balance(self) -> float | None:
        return 49708.88


class FakeTerminal:
    def __init__(self, batches: list[list[Candle]]) -> None:
        self.batches = batches
        self.i = 0

    def closed_candles(self, symbol: str, timeframe: str, count: int = 50) -> list[Candle]:
        batch = self.batches[min(self.i, len(self.batches) - 1)]
        self.i += 1
        return batch

    def contract(self, symbol: str) -> SymbolContract:
        return SymbolContract(0.01, 50.0, 0.01, 0.01, 1.0)


@pytest.mark.asyncio
async def test_engine_dry_run_logs_would_enter(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.symbol = "XAUUSD"
    settings.mt5_symbol = "XAUUSD"
    settings.timeframe = "1M"
    settings.max_position_size = 1.0
    settings.risk_per_trade_pct = 2.0
    db = Database(settings)
    await db.start()
    executor = FakeExecutor()
    terminal = FakeTerminal([BUY_PATTERN[:4], BUY_PATTERN])
    engine = SignalEngine(
        settings,
        strategy=SweepBreakoutStrategy(sl_offset=0.80),
        executor=executor,
        terminal=terminal,  # type: ignore[arg-type]
        risk=RiskManager(settings),
        duplicates=DuplicateProtection(),
        positions=PositionManager(executor, KillSwitch()),
        kill_switch=KillSwitch(),
        health=HealthMonitor(),
        database=db,
    )
    await engine.poll_once()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "WAITING FOR RETEST" in text
    assert "XAUUSD" in text
    assert "would send" not in text


@pytest.mark.asyncio
async def test_engine_first_poll_syncs_without_trading(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    db = Database(settings)
    await db.start()
    executor = FakeExecutor()
    terminal = FakeTerminal([BUY_PATTERN])
    engine = SignalEngine(
        settings,
        strategy=SweepBreakoutStrategy(sl_offset=0.80),
        executor=executor,
        terminal=terminal,  # type: ignore[arg-type]
        risk=RiskManager(settings),
        duplicates=DuplicateProtection(),
        positions=PositionManager(executor, KillSwitch()),
        kill_switch=KillSwitch(),
        health=HealthMonitor(),
        database=db,
    )
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "would send" not in text


@pytest.mark.asyncio
async def test_engine_reviews_same_candle_without_new_order(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    db = Database(settings)
    await db.start()
    executor = FakeExecutor()
    terminal = FakeTerminal([BUY_PATTERN, BUY_PATTERN])
    engine = SignalEngine(
        settings,
        strategy=SweepBreakoutStrategy(sl_offset=0.80),
        executor=executor,
        terminal=terminal,  # type: ignore[arg-type]
        risk=RiskManager(settings),
        duplicates=DuplicateProtection(),
        positions=PositionManager(executor, KillSwitch()),
        kill_switch=KillSwitch(),
        health=HealthMonitor(),
        database=db,
    )
    await engine.poll_once()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "no trade" in text
    assert "would send" not in text


@pytest.mark.asyncio
async def test_engine_skips_without_terminal(settings) -> None:  # type: ignore[no-untyped-def]
    db = Database(settings)
    await db.start()
    executor = FakeExecutor()
    engine = SignalEngine(
        settings,
        strategy=SweepBreakoutStrategy(),
        executor=executor,
        terminal=None,
        risk=RiskManager(settings),
        duplicates=DuplicateProtection(),
        positions=PositionManager(executor, KillSwitch()),
        kill_switch=KillSwitch(),
        health=HealthMonitor(),
        database=db,
    )
    await engine.poll_once()
    await db.close()
