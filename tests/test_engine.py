from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_bot.database.database import Database
from trading_bot.market.candle_reader import Candle
from trading_bot.market.chart_prices import ChartMarket
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


class ChartReader:
    ready = True

    def __init__(self, market: ChartMarket) -> None:
        self.market = market

    async def read_market(self) -> ChartMarket:
        return self.market


class ExplodingTerminal(FakeTerminal):
    def closed_candles(self, symbol: str, timeframe: str, count: int = 50) -> list[Candle]:
        raise AssertionError("the setup must not use MetaTrader candles")

    def forming_candle(self, symbol: str, timeframe: str) -> Candle | None:
        raise AssertionError("the setup must not use the MetaTrader forming candle")


@pytest.mark.asyncio
async def test_setup_uses_chart_prices_not_mt5_candles(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    db = Database(settings)
    await db.start()
    closed = [_c(minute, 10.0, 11.0, 9.0, 10.2) for minute in range(6)]
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 12, 6, tzinfo=timezone.utc),
        open=10.2,
        high=10.4,
        low=10.1,
        close=4122.325,
        is_closed=False,
    )
    engine = SignalEngine(
        settings,
        strategy=SweepBreakoutStrategy(sl_offset=0.80),
        executor=FakeExecutor(),
        terminal=ExplodingTerminal([[]]),  # type: ignore[arg-type]
        risk=RiskManager(settings),
        duplicates=DuplicateProtection(),
        positions=PositionManager(FakeExecutor(), KillSwitch()),
        kill_switch=KillSwitch(),
        health=HealthMonitor(),
        database=db,
    )
    engine.color_reader = ChartReader(ChartMarket(closed, forming, [""] * 6))
    await engine.poll_once()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "4122.325" in text
    assert "would send" not in text


class SequenceChart:
    ready = True

    def __init__(self, markets: list[ChartMarket]) -> None:
        self.markets = list(markets)

    async def read_market(self) -> ChartMarket:
        return self.markets.pop(0)


def _engine(settings, reader) -> tuple[SignalEngine, Database]:  # type: ignore[no-untyped-def]
    db = Database(settings)
    executor = FakeExecutor()
    engine = SignalEngine(
        settings,
        strategy=SweepBreakoutStrategy(sl_offset=0.80),
        executor=executor,
        terminal=FakeTerminal([[]]),  # type: ignore[arg-type]
        risk=RiskManager(settings),
        duplicates=DuplicateProtection(),
        positions=PositionManager(executor, KillSwitch()),
        kill_switch=KillSwitch(),
        health=HealthMonitor(),
        database=db,
    )
    engine.color_reader = reader
    return engine, db


@pytest.mark.asyncio
async def test_a_remembered_blue_does_not_sell_when_the_picture_has_no_blue(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    sell = [
        _c(0, 10.0, 10.20, 9.90, 10.15),
        _c(1, 10.15, 10.40, 10.10, 10.30),
        _c(2, 10.30, 10.90, 10.20, 10.25),
        _c(3, 10.25, 11.20, 10.15, 10.40),
        _c(4, 10.40, 10.45, 10.20, 10.22),
        _c(5, 10.22, 10.24, 10.05, 10.10),
    ]
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 12, 6, tzinfo=timezone.utc),
        open=10.10,
        high=10.30,
        low=10.15,
        close=10.22,
        is_closed=False,
    )
    sync = ChartMarket(
        [_c(20 + minute, 10.0, 10.1, 9.9, 10.0) for minute in range(6)],
        forming,
        [""] * 6,
        fresh_colors=[""] * 6,
    )
    remembered = ChartMarket(sell, forming, ["", "", "blue", "", "", ""], fresh_colors=[""] * 6)
    engine, db = _engine(settings, SequenceChart([sync, remembered]))
    await db.start()
    await engine.poll_once()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "CANDLE CUT OFF" not in text
    assert "COLOR NOT ON THIS PICTURE" in text
    assert "would send" not in text
    assert engine._pending is None
    engine._pending = type("Pending", (), {"extra": {"pattern": "sell_blue_sweep"}})()
    engine._note_fresh_colors([""] * 6)
    assert engine._pending is not None
    engine._note_fresh_colors([""] * 6)
    assert engine._pending is None


class ZoomReader(ChartReader):
    def __init__(self, market: ChartMarket) -> None:
        super().__init__(market)
        self.actions: list[str] = []

    async def adjust_zoom(self, action: str) -> bool:
        self.actions.append(action)
        return True


@pytest.mark.asyncio
async def test_too_many_candles_zoom_in_and_do_not_trade(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    closed = [_c(minute % 60, 10.0, 11.0, 9.0, 10.2) for minute in range(80)]
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc),
        open=10.2,
        high=10.4,
        low=10.1,
        close=10.2,
        is_closed=False,
    )
    reader = ZoomReader(ChartMarket(closed, forming, [""] * 80, price_span=32))
    engine, db = _engine(settings, reader)
    await db.start()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert reader.actions == ["time-in"]
    assert "CHART ZOOM" in text
    assert "would send" not in text


@pytest.mark.asyncio
async def test_a_cut_off_candle_does_not_trade(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    closed = [_c(minute, 10.0, 11.0, 9.0, 10.2) for minute in range(6)]
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 12, 6, tzinfo=timezone.utc),
        open=10.2,
        high=10.4,
        low=10.1,
        close=10.2,
        is_closed=False,
    )
    engine, db = _engine(
        settings,
        ChartReader(ChartMarket(closed, forming, [""] * 6, clipped=True)),
    )
    await db.start()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "CANDLE CUT OFF" in text
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
