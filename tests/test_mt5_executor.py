from __future__ import annotations

from types import SimpleNamespace

import pytest

from trading_bot.exceptions import ExecutionNotEnabled
from trading_bot.trading.executor import OrderRequest
from trading_bot.trading.mt5_executor import Mt5Executor, mt5_order_comment
from trading_bot.trading.mt5_terminal import Mt5Terminal


class FakeMt5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    TRADE_RETCODE_DONE = 10009
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TIMEFRAME_M1 = 1

    def __init__(self, *, trade_mode: int = 0) -> None:
        self.trade_mode = trade_mode
        self.sent: list[dict[str, object]] = []
        self._positions: list[SimpleNamespace] = []
        self.reject_sltp = False

    def initialize(self, **_kwargs: object) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self) -> tuple[int, str]:
        return (1, "err")

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            login=123,
            server="Promax-Demo",
            balance=49708.88,
            trade_mode=self.trade_mode,
            trade_allowed=True,
            trade_expert=True,
        )

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(trade_allowed=True)

    def symbol_select(self, _symbol: str, _enable: bool) -> bool:
        return True

    def symbol_info(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(
            visible=True,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_contract_size=100.0,
            filling_mode=2,
            point=0.01,
        )

    def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
        return SimpleNamespace(ask=4309.80, bid=4309.70)

    def copy_rates_from_pos(
        self, _symbol: str, _tf: int, _start: int, count: int
    ) -> list[dict[str, float | int]]:
        rows: list[dict[str, float | int]] = []
        for i in range(count):
            rows.append(
                {
                    "time": 1_000_000 + i * 60,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "tick_volume": 1,
                }
            )
        return rows

    def order_send(self, request: dict[str, object]) -> SimpleNamespace:
        self.sent.append(dict(request))
        if request["action"] == self.TRADE_ACTION_SLTP and self.reject_sltp:
            return SimpleNamespace(retcode=10016, comment="invalid stops")
        if request["action"] == self.TRADE_ACTION_DEAL:
            buy = request["type"] == self.ORDER_TYPE_BUY
            self._positions = [
                SimpleNamespace(
                    ticket=1001,
                    type=self.POSITION_TYPE_BUY if buy else self.POSITION_TYPE_SELL,
                    symbol=request["symbol"],
                    volume=request["volume"],
                    price_open=request["price"],
                    sl=0.0,
                    tp=0.0,
                    time=1,
                    profit=0.0,
                    magic=request["magic"],
                    comment=request.get("comment", ""),
                )
            ]
            return SimpleNamespace(
                retcode=self.TRADE_RETCODE_DONE, order=1001, deal=2001, comment="ok"
            )
        if self._positions:
            self._positions[0].sl = request.get("sl", 0.0)
            self._positions[0].tp = request.get("tp", 0.0)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="ok")

    def positions_get(self, symbol: str | None = None) -> list[SimpleNamespace]:
        if symbol:
            return [row for row in self._positions if row.symbol == symbol]
        return list(self._positions)


def _order() -> OrderRequest:
    return OrderRequest(
        symbol="XAUUSD",
        direction="BUY",
        quantity=0.10,
        stop_loss=4308.99,
        take_profit=None,
        signal_id="XAUUSD_1M_2026-09-23T12:04_BUY",
        expected_price=4309.80,
    )


@pytest.mark.asyncio
async def test_dry_run_does_not_send(settings) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = True
    api = FakeMt5()
    executor = Mt5Executor(settings, terminal=Mt5Terminal(api=api))
    result = await executor.open_long(_order())
    assert result.dry_run is True
    assert result.accepted is False
    assert api.sent == []


@pytest.mark.asyncio
async def test_demo_refuses_live_account(settings) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = False
    settings.trading_mode = type(settings.trading_mode).DEMO
    api = FakeMt5(trade_mode=FakeMt5.ACCOUNT_TRADE_MODE_REAL)
    executor = Mt5Executor(settings, terminal=Mt5Terminal(api=api))
    with pytest.raises(ExecutionNotEnabled, match="not DEMO"):
        await executor.start()


@pytest.mark.asyncio
async def test_market_then_attach_sl(settings) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = False
    settings.mt5_symbol = "XAUUSD"
    settings.mt5_magic = 26092301
    api = FakeMt5()
    executor = Mt5Executor(settings, terminal=Mt5Terminal(api=api))
    await executor.start()
    result = await executor.open_long(_order())
    assert result.accepted is True
    assert result.verified is True
    assert result.message == "EXECUTED"
    assert len(api.sent) == 2
    assert api.sent[0]["action"] == FakeMt5.TRADE_ACTION_DEAL
    assert "sl" not in api.sent[0]
    assert api.sent[1]["action"] == FakeMt5.TRADE_ACTION_SLTP
    assert api.sent[1]["sl"] == 4308.99
    assert api.sent[1]["tp"] == 0.0
    comment = str(api.sent[0]["comment"])
    assert ":" not in comment
    assert len(comment) <= 31
    await executor.stop()


@pytest.mark.asyncio
async def test_algo_trading_off_does_not_send(settings) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = False
    settings.mt5_symbol = "XAUUSD"
    api = FakeMt5()
    api.terminal_info = lambda: SimpleNamespace(trade_allowed=False)  # type: ignore[method-assign]
    executor = Mt5Executor(settings, terminal=Mt5Terminal(api=api))
    await executor.start()
    result = await executor.open_long(_order())
    assert result.accepted is False
    assert "Algo Trading is OFF" in result.message
    assert api.sent == []
    await executor.stop()


@pytest.mark.asyncio
async def test_server_blocks_expert_advisors(settings) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = False
    settings.mt5_symbol = "XAUUSD"
    api = FakeMt5()

    def account() -> SimpleNamespace:
        return SimpleNamespace(
            login=123,
            server="Promax-Demo",
            balance=49708.88,
            trade_mode=0,
            trade_allowed=True,
            trade_expert=False,
        )

    api.account_info = account  # type: ignore[method-assign]
    executor = Mt5Executor(settings, terminal=Mt5Terminal(api=api))
    await executor.start()
    result = await executor.open_long(_order())
    assert result.accepted is False
    assert "Promax server blocks" in result.message
    assert api.sent == []
    await executor.stop()


def test_mt5_comment_strips_colon_timestamp() -> None:
    text = mt5_order_comment("XAUUSD_1M_2026-09-23T13:21_BUY")
    assert ":" not in text
    assert "-" not in text
    assert len(text) <= 31
    assert "BUY" in text


def test_closed_candles_drop_forming_bar() -> None:
    api = FakeMt5()
    terminal = Mt5Terminal(api=api)
    terminal.connect()
    candles = terminal.closed_candles("XAUUSD", "1M", 5)
    assert len(candles) == 4
    assert all(c.is_closed for c in candles)


@pytest.mark.asyncio
async def test_stops_follow_the_fill_and_a_rejected_stop_closes_the_sell(settings) -> None:  # type: ignore[no-untyped-def]
    settings.dry_run = False
    settings.mt5_symbol = "XAUUSD"
    settings.mt5_magic = 26092301
    api = FakeMt5()
    api.reject_sltp = True
    api.symbol_info_tick = lambda _symbol: SimpleNamespace(ask=4155.700, bid=4155.637)  # type: ignore[method-assign]
    executor = Mt5Executor(settings, terminal=Mt5Terminal(api=api))
    await executor.start()
    result = await executor.open_short(
        OrderRequest(
            symbol="XAUUSD",
            direction="SELL",
            quantity=0.35,
            stop_loss=4157.494,
            take_profit=4156.118,
            signal_id="XAUUSD_1M_2026-09-29T13:30_SELL",
            expected_price=4156.406,
        )
    )
    assert result.verified is False
    assert "closed because the stop and target were not accepted" in result.message
    assert api.sent[1]["action"] == FakeMt5.TRADE_ACTION_SLTP
    assert api.sent[1]["sl"] == 4156.725
    assert api.sent[1]["tp"] == 4155.349
    assert float(api.sent[1]["sl"]) > 4155.637 > float(api.sent[1]["tp"])
    assert api.sent[2]["action"] == FakeMt5.TRADE_ACTION_DEAL
    assert api.sent[2]["type"] == FakeMt5.ORDER_TYPE_BUY
    assert api.sent[2]["position"] == 1001
    await executor.stop()


def test_closed_candles_empty_when_only_forming() -> None:
    api = FakeMt5()
    original = api.copy_rates_from_pos

    def one_bar(*args: object, **kwargs: object) -> list[dict[str, float | int]]:
        return original(*args, **kwargs)[:1]  # type: ignore[misc]

    api.copy_rates_from_pos = one_bar  # type: ignore[method-assign]
    terminal = Mt5Terminal(api=api)
    terminal.connect()
    assert terminal.closed_candles("XAUUSD", "1M", 1) == []
