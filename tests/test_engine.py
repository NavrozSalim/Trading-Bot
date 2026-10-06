from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trading_bot.database.database import Database
from trading_bot.market.candle_reader import Candle
from trading_bot.market.chart_prices import ChartMarket
from trading_bot.monitoring.health_monitor import HealthMonitor
from trading_bot.safety.duplicate_protection import DuplicateProtection
from trading_bot.safety.kill_switch import KillSwitch
from trading_bot.strategy.signals import Signal, SignalType
from trading_bot.strategy.strategy import SweepBreakoutStrategy
from trading_bot.trading.engine import SignalEngine, price_on_retest
from trading_bot.trading.executor import NullExecutor, Position
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
        self.bid: float | None = None
        self.ask: float | None = None

    def tick(self, symbol: str) -> SimpleNamespace | None:
        if self.bid is None or self.ask is None:
            return None
        return SimpleNamespace(bid=self.bid, ask=self.ask)

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


class Mt5PriceTerminal(FakeTerminal):
    def forming_candle(self, symbol: str, timeframe: str) -> Candle:
        return Candle(
            timestamp=datetime(2026, 9, 23, 12, 6, tzinfo=timezone.utc),
            open=4150.0,
            high=4151.0,
            low=4149.0,
            close=4150.5,
            is_closed=False,
        )


@pytest.mark.asyncio
async def test_setup_uses_mt5_prices_and_the_chart_only_for_color(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    db = Database(settings)
    await db.start()
    closed = [_c(minute, 10.0, 11.0, 9.0, 10.2) for minute in range(6)]
    chart_forming = Candle(
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
        terminal=Mt5PriceTerminal([BUY_PATTERN, BUY_PATTERN]),  # type: ignore[arg-type]
        risk=RiskManager(settings),
        duplicates=DuplicateProtection(),
        positions=PositionManager(FakeExecutor(), KillSwitch()),
        kill_switch=KillSwitch(),
        health=HealthMonitor(),
        database=db,
    )
    engine.color_reader = ChartReader(ChartMarket(closed, chart_forming, [""] * 6, price_span=14))
    await engine.poll_once()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "4150.5" in text
    assert "4122.325" not in text
    assert "would send" not in text


class SequenceChart:
    ready = True

    def __init__(self, markets: list[ChartMarket]) -> None:
        self.markets = list(markets)

    async def read_market(self) -> ChartMarket:
        return self.markets.pop(0)


def _engine(settings, reader, batches: list[list[Candle]] | None = None) -> tuple[SignalEngine, Database]:  # type: ignore[no-untyped-def]
    db = Database(settings)
    executor = FakeExecutor()
    engine = SignalEngine(
        settings,
        strategy=SweepBreakoutStrategy(sl_offset=0.80),
        executor=executor,
        terminal=FakeTerminal(batches or [[]]),  # type: ignore[arg-type]
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
    remembered = ChartMarket(sell, forming, [""] * 6, fresh_colors=[""] * 6)
    plain = [
        Candle(
            timestamp=datetime(2026, 9, 23, 11, 50 + minute, tzinfo=timezone.utc),
            open=10.0,
            high=10.1,
            low=9.9,
            close=10.0,
            is_closed=True,
        )
        for minute in range(6)
    ]
    engine, db = _engine(settings, SequenceChart([sync, remembered]), [plain, sell])
    await db.start()
    await engine.poll_once()
    stamps = [str(candle.timestamp) for candle in sell]
    for _ in range(2):
        engine._color_memory.apply(stamps, ["", "", "blue", "", "", ""])
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


class ColorsOnlyChart:
    """The price scale cannot be read. The paint can."""

    ready = True
    last_bars = 6

    def __init__(self, colors: list[str]) -> None:
        self.colors = colors
        self.actions: list[str] = []

    async def read_market(self) -> None:
        return None

    async def colors_for(self, count: int) -> list[str]:
        colors = list(self.colors)
        if len(colors) < count:
            colors = [""] * (count - len(colors)) + colors
        return colors[-count:]

    async def adjust_zoom(self, action: str) -> bool:
        self.actions.append(action)
        return True


class BlankChart:
    ready = True
    last_bars = 8

    def __init__(self) -> None:
        self.actions: list[str] = []

    async def read_market(self) -> None:
        return None

    async def adjust_zoom(self, action: str) -> bool:
        self.actions.append(action)
        return True


@pytest.mark.asyncio
async def test_a_bad_price_read_does_not_zoom(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    reader = BlankChart()
    engine, db = _engine(settings, reader)
    await db.start()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert reader.actions == []
    assert "CHART NOT READ" in text
    assert "CHART ZOOM" not in text


@pytest.mark.asyncio
async def test_a_failed_price_scale_still_reads_the_paint(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    colors = [""] * len(BUY_PATTERN)
    colors[2] = "yellow"
    reader = ColorsOnlyChart(colors)
    engine, db = _engine(settings, reader, [BUY_PATTERN[:2], BUY_PATTERN, BUY_PATTERN])
    await db.start()
    await engine.poll_once()
    await engine.poll_once()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert reader.actions == []
    assert "WAITING FOR RETEST" in text
    assert "would send" not in text


@pytest.mark.asyncio
async def test_a_yellow_already_on_the_chart_at_startup_is_not_used(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    colors = [""] * len(BUY_PATTERN)
    colors[2] = "yellow"
    reader = ColorsOnlyChart(colors)
    later = BUY_PATTERN + [_c(6, 9.90, 10.00, 9.80, 9.95)]
    engine, db = _engine(settings, reader, [BUY_PATTERN, later])
    await db.start()
    await engine.poll_once()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "YELLOW DETECTED" not in text
    assert "already on the chart at startup" in text
    assert "Read:" in text
    assert engine._pending is None


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


class ScaleReader(ZoomReader):
    def __init__(self, market: ChartMarket) -> None:
        super().__init__(market)
        self.fits = 0

    async def fit_price_scale(self) -> bool:
        self.fits += 1
        return True


@pytest.mark.asyncio
async def test_a_readable_chart_is_not_zoomed_or_reset(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    closed = [_c(minute % 60, 10.0, 11.0, 9.0, 10.2) for minute in range(54)]
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc),
        open=10.2,
        high=10.4,
        low=10.1,
        close=10.2,
        is_closed=False,
    )
    reader = ScaleReader(ChartMarket(closed, forming, [""] * 54, price_span=19, clipped=True))
    engine, db = _engine(settings, reader, [closed])
    await db.start()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert reader.actions == []
    assert reader.fits == 0
    assert "CHART ZOOM" not in text
    assert "CANDLE CUT OFF" not in text


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


def _pending_buy() -> Signal:
    return Signal(
        signal=SignalType.NO_TRADE,
        reason="await_yellow_retest",
        symbol="XAUUSD",
        timeframe="1M",
        candle_timestamp="2026-09-23T12:05",
        price=9.85,
        stop_loss=8.2,
        take_profit=20.0,
        extra={
            "pattern": "buy_yellow_sweep",
            "retest_level": "9.70",
            "sweep_low": "9.00",
            "yellow_high": "9.70",
            "await_retest": "1",
        },
    )


def test_live_price_on_the_retest_allows_the_spread_only() -> None:
    assert price_on_retest(4141.49, 4141.53, 4141.49, buy=True)
    assert price_on_retest(4141.49, 4141.49, 4141.49, buy=True)
    assert not price_on_retest(4141.53, 4141.57, 4141.49, buy=True)
    assert not price_on_retest(4142.023, 4142.063, 4141.49, buy=True)
    assert price_on_retest(4128.295, 4128.335, 4128.295, buy=False)
    assert not price_on_retest(4127.762, 4127.802, 4128.295, buy=False)


@pytest.mark.asyncio
async def test_retest_fires_only_when_the_live_price_is_on_the_level(settings) -> None:  # type: ignore[no-untyped-def]
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
    engine._pending = _pending_buy()
    terminal.bid = 9.30
    terminal.ask = 9.34
    away = Candle(
        timestamp=datetime(2026, 9, 23, 12, 7, tzinfo=timezone.utc),
        open=9.6,
        high=9.9,
        low=9.5,
        close=9.8,
        is_closed=False,
    )
    assert await engine._try_retest_entry(away, "XAUUSD") is False
    assert engine._pending is not None
    terminal.bid = 9.70
    terminal.ask = 9.74
    assert await engine._try_retest_entry(away, "XAUUSD") is True
    await db.close()


@pytest.mark.asyncio
async def test_the_retest_cancels_at_one_point_eight_not_at_the_target(settings) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    engine, db = _engine(settings, None, [BUY_PATTERN])
    engine.terminal.bid = 9.70  # type: ignore[attr-defined]
    engine.terminal.ask = 9.74  # type: ignore[attr-defined]
    await db.start()

    def pending() -> Signal:
        signal = _pending_buy()
        signal.take_profit = 10.55
        signal.extra = {**signal.extra, "cancel_price": "11.11"}
        return signal

    past_target = Candle(
        timestamp=datetime(2026, 9, 23, 12, 7, tzinfo=timezone.utc),
        open=9.8,
        high=10.80,
        low=9.60,
        close=9.9,
        is_closed=False,
    )
    engine._pending = pending()
    assert await engine._try_retest_entry(past_target, "XAUUSD") is True
    assert not engine._expired_setups.contains(pending())

    past_cancel = Candle(
        timestamp=datetime(2026, 9, 23, 12, 8, tzinfo=timezone.utc),
        open=9.8,
        high=11.20,
        low=9.60,
        close=9.9,
        is_closed=False,
    )
    engine._pending = pending()
    engine._pending.extra = {**engine._pending.extra, "anchor_time": "cancel-test"}
    assert await engine._try_retest_entry(past_cancel, "XAUUSD") is True
    assert engine._pending is None
    assert "cancel-test" in engine._expired_setups._times
    await db.close()


class OpenBook(FakeExecutor):
    async def get_positions(self) -> list[Position]:
        return [
            Position(
                broker_id="1",
                signal_id=None,
                symbol="XAUUSD",
                direction="BUY",
                quantity=0.02,
                entry_price=9.7,
                stop_loss=8.2,
                take_profit=11.0,
                opened_at=None,
            )
        ]


@pytest.mark.asyncio
async def test_an_open_trade_ignores_a_new_color(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    db = Database(settings)
    await db.start()
    executor = OpenBook()
    engine = SignalEngine(
        settings,
        strategy=SweepBreakoutStrategy(sl_offset=0.80),
        executor=executor,
        terminal=FakeTerminal([BUY_PATTERN, BUY_PATTERN]),  # type: ignore[arg-type]
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
    assert "A trade is open" in text
    assert "would send" not in text
    assert engine._pending is None


def _sided(minute: int, green: bool) -> Candle:
    return _c(minute, 10.0, 10.3, 9.7, 10.2) if green else _c(minute, 10.2, 10.3, 9.7, 10.0)


@pytest.mark.asyncio
async def test_a_picture_that_is_not_the_mt5_chart_does_not_trade(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    mt5 = [_sided(minute, minute % 2 == 0) for minute in range(20)]
    picture = [_sided(minute, minute % 3 == 0) for minute in range(20)]
    colors = [""] * 20
    colors[15] = "yellow"
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 12, 20, tzinfo=timezone.utc),
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        is_closed=False,
    )
    reader = ChartReader(ChartMarket(picture, forming, colors, fresh_colors=list(colors)))
    engine, db = _engine(settings, reader, [mt5])
    await db.start()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "CHART DOES NOT MATCH MT5" in text
    assert "YELLOW DETECTED" not in text


@pytest.mark.asyncio
async def test_a_picture_scrolled_back_still_dates_the_paint(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    import random

    rng = random.Random(3)
    sides = [rng.random() < 0.5 for _ in range(60)]
    mt5 = [_sided(minute, green) for minute, green in enumerate(sides)]
    # The picture shows minutes 0..39 but stamps its rightmost as minute 59.
    picture = [_sided(minute + 20, green) for minute, green in enumerate(sides[:40])]
    colors = [""] * 40
    colors[30] = "blue"
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 12, 59, tzinfo=timezone.utc),
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        is_closed=False,
    )
    reader = ChartReader(ChartMarket(picture, forming, colors, fresh_colors=list(colors)))
    engine, db = _engine(settings, reader, [mt5])
    await db.start()
    await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert "scrolled 20 minutes back" in text
    assert "Blue: 17:30" in text


@pytest.mark.asyncio
async def test_a_one_minute_clock_slide_does_not_copy_the_blue(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    import random
    from datetime import timedelta

    rng = random.Random(5)
    sides = [rng.random() < 0.5 for _ in range(20)]
    mt5 = [_sided(minute, green) for minute, green in enumerate(sides)]
    colors = [""] * 20
    colors[15] = "blue"
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 12, 20, tzinfo=timezone.utc),
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        is_closed=False,
    )
    exact = ChartMarket(list(mt5), forming, list(colors), fresh_colors=list(colors))
    late = [
        Candle(
            timestamp=candle.timestamp + timedelta(minutes=1),
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            is_closed=True,
        )
        for candle in mt5
    ]
    slid = ChartMarket(late, forming, list(colors), fresh_colors=list(colors))
    engine, db = _engine(settings, SequenceChart([exact, exact, slid]), [mt5])
    await db.start()
    for _ in range(3):
        await engine.poll_once()
    await db.close()
    assert list(engine._color_memory.confirmed.values()) == ["blue"]
    assert list(engine._color_memory.confirmed) == [str(mt5[15].timestamp)]


@pytest.mark.asyncio
async def test_a_one_minute_clock_slide_does_not_paint_a_yellow(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    import random
    from datetime import timedelta

    rng = random.Random(5)
    sides = [rng.random() < 0.5 for _ in range(20)]
    mt5 = [_sided(minute, green) for minute, green in enumerate(sides)]
    colors = [""] * 20
    colors[15] = "yellow"
    forming = Candle(
        timestamp=datetime(2026, 9, 23, 12, 20, tzinfo=timezone.utc),
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        is_closed=False,
    )
    late = [
        Candle(
            timestamp=candle.timestamp + timedelta(minutes=1),
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            is_closed=True,
        )
        for candle in mt5
    ]
    slid = ChartMarket(late, forming, list(colors), fresh_colors=list(colors))
    engine, db = _engine(settings, SequenceChart([slid, slid, slid]), [mt5])
    await db.start()
    for _ in range(3):
        await engine.poll_once()
    text = capsys.readouterr().out
    await db.close()
    assert engine._color_memory.confirmed == {}
    assert "Clock moved" in text
    assert "closed on TradingView" in text
    assert "YELLOW DETECTED" not in text


@pytest.mark.asyncio
async def test_an_unreadable_chart_is_written_to_excel(settings, capsys) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    settings.mt5_symbol = "XAUUSD"
    reader = BlankChart()
    engine, db = _engine(settings, reader)
    await db.start()
    await engine.poll_once()
    await db.close()
    book = settings.logs_dir.parent / "data" / "trades.xlsx"
    assert book.exists()
    import openpyxl

    sheet = openpyxl.load_workbook(book).active
    assert sheet["E2"].value == "SKIP"
