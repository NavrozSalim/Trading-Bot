"""MetaTrader 5 terminal adapter. Windows + running MT5 terminal required."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from trading_bot.exceptions import ConfigurationError, ExecutionNotEnabled
from trading_bot.market.candle_reader import Candle
from trading_bot.monitoring.logger import get_logger
from trading_bot.trading.position_sizer import SymbolContract

log = get_logger("trading_bot.mt5")

_TF_MAP = {
    "1": "M1",
    "1M": "M1",
    "3M": "M3",
    "5M": "M5",
    "15M": "M15",
    "30M": "M30",
    "1H": "H1",
    "4H": "H4",
    "1D": "D1",
    "D": "D1",
    "1W": "W1",
    "W": "W1",
}


class Mt5Api(Protocol):
    def initialize(self, *args: Any, **kwargs: Any) -> bool: ...
    def shutdown(self) -> None: ...
    def last_error(self) -> Any: ...
    def account_info(self) -> Any: ...
    def terminal_info(self) -> Any: ...
    def symbol_select(self, symbol: str, enable: bool) -> bool: ...
    def symbol_info(self, symbol: str) -> Any: ...
    def symbol_info_tick(self, symbol: str) -> Any: ...
    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int) -> Any: ...
    def order_send(self, request: dict[str, Any]) -> Any: ...
    def positions_get(self, *args: Any, **kwargs: Any) -> Any: ...
    def history_deals_get(self, *args: Any, **kwargs: Any) -> Any: ...


def import_mt5() -> Any:
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ExecutionNotEnabled(
            "The MetaTrader5 package is not installed. pip install MetaTrader5"
        ) from exc
    return mt5


def mt5_timeframe_constant(mt5: Any, timeframe: str) -> int:
    key = _TF_MAP.get(timeframe.strip().upper(), timeframe.strip().upper())
    attr = f"TIMEFRAME_{key}"
    if not hasattr(mt5, attr):
        raise ConfigurationError(f"Unsupported MT5 timeframe: {timeframe}")
    return int(getattr(mt5, attr))


class Mt5Terminal:
    def __init__(self, api: Any | None = None) -> None:
        self.api = api
        self._connected = False

    def connect(self, *, path: str = "", login: int = 0, password: str = "", server: str = "") -> None:
        if self.api is None:
            self.api = import_mt5()
        kwargs: dict[str, Any] = {}
        if path:
            kwargs["path"] = path
        if login and password and server:
            kwargs.update({"login": login, "password": password, "server": server})
        if not self.api.initialize(**kwargs):
            raise ConfigurationError(f"MT5 initialize failed: {self.api.last_error()}")
        info = self.api.account_info()
        if info is None:
            self.api.shutdown()
            raise ConfigurationError(f"MT5 has no account info: {self.api.last_error()}")
        trade_mode = getattr(info, "trade_mode", None)
        log.info(
            "mt5_connected",
            login=getattr(info, "login", None),
            server=getattr(info, "server", None),
            balance=getattr(info, "balance", None),
            trade_mode=trade_mode,
            trade_allowed=getattr(info, "trade_allowed", None),
            trade_expert=getattr(info, "trade_expert", None),
        )
        self._connected = True

    def disconnect(self) -> None:
        if self.api is not None and self._connected:
            try:
                self.api.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self._connected = False

    def account_balance(self) -> float:
        info = self._require().account_info()
        if info is None:
            raise ConfigurationError("MT5 account_info is empty.")
        return float(info.balance)

    def is_demo_account(self) -> bool:
        info = self._require().account_info()
        if info is None:
            return False
        mode = getattr(info, "trade_mode", None)
        mt5 = self.api
        demo = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        return mode == demo

    def algo_trading_enabled(self) -> bool:
        """Toolbar Algo Trading button (client)."""
        api = self._require()
        term = api.terminal_info() if hasattr(api, "terminal_info") else None
        if term is not None and hasattr(term, "trade_allowed") and not bool(term.trade_allowed):
            return False
        return True

    def server_algo_allowed(self) -> bool:
        """Broker flag. False means the server blocks Expert Advisors (retcode 10026)."""
        info = self._require().account_info()
        if info is None or not hasattr(info, "trade_expert"):
            return True
        return bool(info.trade_expert)

    def ensure_symbol(self, symbol: str) -> Any:
        api = self._require()
        if not api.symbol_select(symbol, True):
            raise ConfigurationError(f"MT5 could not select symbol {symbol}: {api.last_error()}")
        info = api.symbol_info(symbol)
        if info is None or not info.visible:
            raise ConfigurationError(f"MT5 symbol {symbol} is not available.")
        return info

    def contract(self, symbol: str) -> SymbolContract:
        info = self.ensure_symbol(symbol)
        tick_size = float(info.trade_tick_size or info.point or 0)
        tick_value = float(info.trade_tick_value or 0)
        if tick_value <= 0 and float(getattr(info, "trade_contract_size", 0) or 0) > 0:
            tick_value = float(info.trade_contract_size) * tick_size
        return SymbolContract(
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            tick_size=tick_size,
            tick_value=tick_value,
        )

    def tick(self, symbol: str) -> Any:
        tick = self._require().symbol_info_tick(symbol)
        if tick is None:
            raise ConfigurationError(f"No tick for {symbol}.")
        return tick

    def closed_candles(self, symbol: str, timeframe: str, count: int = 50) -> list[Candle]:
        api = self._require()
        tf = mt5_timeframe_constant(api, timeframe)
        raw = api.copy_rates_from_pos(symbol, tf, 0, count)
        if raw is None:
            raise ConfigurationError(f"MT5 returned no candles for {symbol}: {api.last_error()}")
        candles: list[Candle] = []
        for row in raw:
            names = getattr(getattr(row, "dtype", None), "names", None)
            if isinstance(row, dict):
                ts_raw = int(row["time"])
                open_ = float(row["open"])
                high = float(row["high"])
                low = float(row["low"])
                close = float(row["close"])
                volume = float(row["tick_volume"]) if "tick_volume" in row else None
            else:
                ts_raw = int(row["time"])
                open_ = float(row["open"])
                high = float(row["high"])
                low = float(row["low"])
                close = float(row["close"])
                volume = float(row["tick_volume"]) if names and "tick_volume" in names else None
            ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            candles.append(
                Candle(
                    timestamp=ts,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    is_closed=True,
                )
            )
        if len(candles) < 2:
            return []
        return candles[:-1]

    def forming_candle(self, symbol: str, timeframe: str) -> Candle | None:
        """The bar that is still building. A retest can touch this before it closes."""
        api = self._require()
        tf = mt5_timeframe_constant(api, timeframe)
        raw = api.copy_rates_from_pos(symbol, tf, 0, 2)
        if raw is None or len(raw) == 0:
            return None
        row = raw[-1]
        ts = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)
        return Candle(
            timestamp=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            is_closed=False,
        )

    def send(self, request: dict[str, Any]) -> Any:
        result = self._require().order_send(request)
        if result is None:
            raise ConfigurationError(f"MT5 order_send returned None: {self.api.last_error()}")
        return result

    def positions(self, symbol: str | None = None, magic: int | None = None) -> list[Any]:
        api = self._require()
        rows = api.positions_get(symbol=symbol) if symbol else api.positions_get()
        if rows is None:
            return []
        if magic is None:
            return list(rows)
        return [row for row in rows if int(getattr(row, "magic", 0)) == magic]

    def position_close_profit(self, position_id: int) -> float | None:
        """Realized profit + swap + commission for a position closed today."""
        api = self._require()
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        deals = api.history_deals_get(start, datetime.now(timezone.utc))
        if not deals:
            return None
        total = 0.0
        found = False
        for deal in deals:
            if int(getattr(deal, "position_id", 0) or 0) != int(position_id):
                continue
            if int(getattr(deal, "entry", -1)) not in (1, 3):
                continue
            total += float(getattr(deal, "profit", 0) or 0)
            total += float(getattr(deal, "swap", 0) or 0)
            total += float(getattr(deal, "commission", 0) or 0)
            found = True
        return total if found else None

    def position_close_details(self, position_id: int) -> tuple[float | None, float | None]:
        """Exit price and realized profit for a position closed today."""
        api = self._require()
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        deals = api.history_deals_get(start, datetime.now(timezone.utc))
        if not deals:
            return None, None
        price: float | None = None
        total = 0.0
        found = False
        for deal in deals:
            if int(getattr(deal, "position_id", 0) or 0) != int(position_id):
                continue
            if int(getattr(deal, "entry", -1)) not in (1, 3):
                continue
            price = float(getattr(deal, "price", 0) or 0) or price
            total += float(getattr(deal, "profit", 0) or 0)
            total += float(getattr(deal, "swap", 0) or 0)
            total += float(getattr(deal, "commission", 0) or 0)
            found = True
        if not found:
            return None, None
        return price, total

    def filling_mode(self, symbol: str) -> int:
        api = self._require()
        info = self.ensure_symbol(symbol)
        filling = int(getattr(info, "filling_mode", 0))
        ioc = getattr(api, "ORDER_FILLING_IOC", 1)
        fok = getattr(api, "ORDER_FILLING_FOK", 0)
        ret = getattr(api, "ORDER_FILLING_RETURN", 2)
        if filling & 2:
            return ioc
        if filling & 1:
            return fok
        return ret

    def _require(self) -> Any:
        if self.api is None or not self._connected:
            raise ConfigurationError("MT5 is not connected.")
        return self.api
