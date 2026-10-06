"""MT5 executor: market order first, then SL/TP on the open position."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from trading_bot.config import Settings, TradingMode
from trading_bot.exceptions import ConfigurationError, ExecutionNotEnabled, OrderVerificationError
from trading_bot.monitoring.logger import get_logger
from trading_bot.trading.executor import OrderRequest, OrderResult, Position, TradingExecutor
from trading_bot.trading.mt5_terminal import Mt5Terminal

log = get_logger("trading_bot.mt5_executor")

MT5_COMMENT_MAX = 31


def mt5_order_comment(signal_id: str | None) -> str:
    """MT5 rejects comments with ':' and similar; max 31 chars."""
    raw = signal_id or "bot"
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "" for ch in raw)
    cleaned = cleaned[:MT5_COMMENT_MAX].strip("_")
    return cleaned or "bot"


class Mt5Executor(TradingExecutor):
    def __init__(self, settings: Settings, terminal: Mt5Terminal | None = None) -> None:
        self.settings = settings
        self.terminal = terminal or Mt5Terminal()
        self._started = False

    async def start(self) -> None:
        await asyncio.to_thread(
            self.terminal.connect,
            path=self.settings.mt5_terminal_path,
            login=self.settings.mt5_login,
            password=self.settings.mt5_password.get_secret_value() if self.settings.mt5_password else "",
            server=self.settings.mt5_server,
        )
        if self.settings.trading_mode is TradingMode.DEMO and not self.terminal.is_demo_account():
            self.terminal.disconnect()
            raise ExecutionNotEnabled(
                "MT5 account is not DEMO. TRADING_MODE=DEMO refuses a live terminal."
            )
        if self.settings.trading_mode is TradingMode.LIVE and not self.settings.is_live_allowed():
            self.terminal.disconnect()
            raise ExecutionNotEnabled("Live MT5 is blocked by safety flags.")
        self.terminal.ensure_symbol(self.settings.mt5_symbol)
        if not self.terminal.algo_trading_enabled():
            log.error("mt5_algo_trading_off")
            print(
                "WARNING: Algo Trading is OFF in MT5. "
                "Click the Algo Trading button (it must be green) or every order will be rejected."
            )
        if not self.terminal.server_algo_allowed():
            log.error("mt5_server_disables_algo")
            print(
                "WARNING: Promax has Expert Advisors OFF on this account. "
                "The green Algo Trading button is not enough. Enable algorithmic "
                "trading for this login in the Promax client area, then restart MT5."
            )
        self._started = True

    async def stop(self) -> None:
        await asyncio.to_thread(self.terminal.disconnect)
        self._started = False

    async def open_long(self, order: OrderRequest) -> OrderResult:
        return await self._open(order, buy=True)

    async def open_short(self, order: OrderRequest) -> OrderResult:
        return await self._open(order, buy=False)

    async def _open(self, order: OrderRequest, *, buy: bool) -> OrderResult:
        if self.settings.dry_run:
            log.info(
                "dry_run_skip_mt5",
                side="BUY" if buy else "SELL",
                quantity=order.quantity,
                sl=order.stop_loss,
                tp=order.take_profit,
            )
            return OrderResult(
                accepted=False,
                verified=False,
                message="DRY_RUN: would send MT5 market order then attach SL/TP",
                dry_run=True,
                extra={"side": "BUY" if buy else "SELL", "quantity": order.quantity},
            )
        self._assert_can_send()
        if not await asyncio.to_thread(self.terminal.algo_trading_enabled):
            log.error("mt5_algo_trading_off")
            return OrderResult(
                accepted=False,
                verified=False,
                message=(
                    "Algo Trading is OFF in MT5. Click Algo Trading (green) "
                    "and wait for the next signal."
                ),
                dry_run=False,
            )
        if not await asyncio.to_thread(self.terminal.server_algo_allowed):
            log.error("mt5_server_disables_algo")
            return OrderResult(
                accepted=False,
                verified=False,
                message=(
                    "Promax server blocks Expert Advisors on this account. "
                    "Enable algorithmic trading for this login in the Promax client area, "
                    "then restart MT5. The green button alone will not send orders."
                ),
                dry_run=False,
            )
        api = self.terminal.api
        tick = await asyncio.to_thread(self.terminal.tick, order.symbol)
        price = float(tick.ask if buy else tick.bid)
        order_type = api.ORDER_TYPE_BUY if buy else api.ORDER_TYPE_SELL
        filling = await asyncio.to_thread(self.terminal.filling_mode, order.symbol)
        request = {
            "action": api.TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": float(order.quantity),
            "type": order_type,
            "price": price,
            "deviation": self.settings.mt5_deviation,
            "magic": self.settings.mt5_magic,
            "comment": mt5_order_comment(order.signal_id),
            "type_time": api.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        try:
            result = await asyncio.to_thread(self.terminal.send, request)
        except ConfigurationError as exc:
            log.error("mt5_order_send_failed", error=str(exc))
            return OrderResult(
                accepted=False,
                verified=False,
                message=str(exc),
                dry_run=False,
            )
        retcode = int(getattr(result, "retcode", -1))
        if retcode != api.TRADE_RETCODE_DONE:
            comment = str(getattr(result, "comment", ""))
            log.error("mt5_order_rejected", retcode=retcode, comment=comment)
            message = f"MT5 rejected market order retcode={retcode}"
            if retcode == getattr(api, "TRADE_RETCODE_CLIENT_DISABLES_AT", 10027):
                message = (
                    "Algo Trading is OFF in MT5. Click Algo Trading (green) "
                    "and wait for the next signal."
                )
            if retcode == getattr(api, "TRADE_RETCODE_SERVER_DISABLES_AT", 10026):
                message = (
                    "Promax server blocks Expert Advisors on this account. "
                    "Enable algorithmic trading for this login in the Promax client area, "
                    "then restart MT5. The green button alone will not send orders."
                )
            return OrderResult(
                accepted=False,
                verified=False,
                message=message,
                dry_run=False,
                extra={"result": str(result)},
            )
        ticket = str(getattr(result, "order", "") or getattr(result, "deal", ""))
        position = await self._wait_for_position(order.symbol, ticket)
        if position is None:
            return OrderResult(
                accepted=True,
                verified=False,
                message="MT5 accepted the deal but the position was not found; not retrying.",
                dry_run=False,
            )
        sl_result = await self._attach_stops(position, order)
        verified = sl_result.verified
        return OrderResult(
            accepted=True,
            verified=verified,
            message=sl_result.message,
            position=position,
            dry_run=False,
        )

    async def _attach_stops(self, position: Position, order: OrderRequest) -> OrderResult:
        api = self.terminal.api
        sl = float(order.stop_loss) if order.stop_loss is not None else 0.0
        tp = float(order.take_profit) if order.take_profit is not None else 0.0
        request = {
            "action": api.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": int(position.broker_id),
            "sl": sl,
            "tp": tp,
        }
        result = await asyncio.to_thread(self.terminal.send, request)
        retcode = int(getattr(result, "retcode", -1))
        if retcode != api.TRADE_RETCODE_DONE:
            log.error("mt5_sltp_failed", retcode=retcode, ticket=position.broker_id)
            closed = await self.close_position(position)
            message = f"Position opened but SL/TP modify failed retcode={retcode}"
            if closed.accepted:
                message += " The position was closed because the stop and target were not accepted."
            else:
                message += f" Close also failed: {closed.message}"
            return OrderResult(
                accepted=True,
                verified=False,
                message=message,
                position=position,
                dry_run=False,
            )
        refreshed = await self._position_by_ticket(position.broker_id, position.symbol)
        if refreshed is None:
            return OrderResult(
                accepted=True,
                verified=False,
                message="SL/TP sent but position could not be re-read.",
                position=position,
                dry_run=False,
            )
        if sl and (
            refreshed.stop_loss is None or abs(refreshed.stop_loss - sl) > 0.05
        ):
            raise OrderVerificationError("Attached stop loss does not match the intended value.")
        return OrderResult(
            accepted=True,
            verified=True,
            message="EXECUTED",
            position=refreshed,
            dry_run=False,
        )

    async def close_position(self, position: Position) -> OrderResult:
        self._assert_can_send()
        api = self.terminal.api
        tick = await asyncio.to_thread(self.terminal.tick, position.symbol)
        buy = position.direction.upper() == "BUY"
        request = {
            "action": api.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": float(position.quantity),
            "type": api.ORDER_TYPE_SELL if buy else api.ORDER_TYPE_BUY,
            "position": int(position.broker_id),
            "price": float(tick.bid if buy else tick.ask),
            "deviation": self.settings.mt5_deviation,
            "magic": self.settings.mt5_magic,
            "comment": "bot_close",
            "type_time": api.ORDER_TIME_GTC,
            "type_filling": await asyncio.to_thread(self.terminal.filling_mode, position.symbol),
        }
        result = await asyncio.to_thread(self.terminal.send, request)
        retcode = int(getattr(result, "retcode", -1))
        ok = retcode == api.TRADE_RETCODE_DONE
        return OrderResult(
            accepted=ok,
            verified=ok,
            message="CLOSED" if ok else f"close failed retcode={retcode}",
            dry_run=False,
        )

    async def close_all_positions(self) -> list[OrderResult]:
        positions = await self.get_positions()
        results: list[OrderResult] = []
        for pos in positions:
            if pos.opened_by_bot:
                results.append(await self.close_position(pos))
        return results

    async def set_stop_loss(self, position: Position, stop_loss: float) -> OrderResult:
        dummy = OrderRequest(
            symbol=position.symbol,
            direction=position.direction,
            quantity=position.quantity,
            stop_loss=stop_loss,
            take_profit=position.take_profit,
            signal_id=position.signal_id or "",
        )
        return await self._attach_stops(position, dummy)

    async def set_take_profit(self, position: Position, take_profit: float) -> OrderResult:
        dummy = OrderRequest(
            symbol=position.symbol,
            direction=position.direction,
            quantity=position.quantity,
            stop_loss=position.stop_loss,
            take_profit=take_profit,
            signal_id=position.signal_id or "",
        )
        return await self._attach_stops(position, dummy)

    async def get_positions(self) -> list[Position]:
        rows = await asyncio.to_thread(
            self.terminal.positions, self.settings.mt5_symbol, self.settings.mt5_magic
        )
        return [self._to_position(row) for row in rows]

    async def get_account_balance(self) -> float | None:
        return await asyncio.to_thread(self.terminal.account_balance)

    async def _wait_for_position(self, symbol: str, ticket: str) -> Position | None:
        for _ in range(10):
            positions = await asyncio.to_thread(
                self.terminal.positions, symbol, self.settings.mt5_magic
            )
            mapped = [self._to_position(row) for row in positions]
            for pos in mapped:
                if pos.broker_id == ticket or not ticket:
                    return pos
            if mapped:
                return mapped[-1]
            await asyncio.sleep(0.2)
        return None

    async def _position_by_ticket(self, ticket: str, symbol: str) -> Position | None:
        rows = await asyncio.to_thread(self.terminal.positions, symbol, self.settings.mt5_magic)
        for row in rows:
            pos = self._to_position(row)
            if pos.broker_id == ticket:
                return pos
        return None

    def _to_position(self, row: object) -> Position:
        api = self.terminal.api
        pos_type = int(getattr(row, "type", -1))
        buy_type = getattr(api, "POSITION_TYPE_BUY", 0)
        direction = "BUY" if pos_type == buy_type else "SELL"
        opened = getattr(row, "time", None)
        opened_at = None
        if opened:
            opened_at = datetime.fromtimestamp(int(opened), tz=timezone.utc).isoformat()
        magic = int(getattr(row, "magic", 0))
        return Position(
            broker_id=str(getattr(row, "ticket")),
            signal_id=str(getattr(row, "comment", "") or None),
            symbol=str(getattr(row, "symbol")),
            direction=direction,
            quantity=float(getattr(row, "volume")),
            entry_price=float(getattr(row, "price_open")),
            stop_loss=float(getattr(row, "sl") or 0) or None,
            take_profit=float(getattr(row, "tp") or 0) or None,
            opened_at=opened_at,
            unrealized_pnl=float(getattr(row, "profit", 0) or 0),
            opened_by_bot=magic == self.settings.mt5_magic,
        )

    def _assert_can_send(self) -> None:
        if not self._started:
            raise ExecutionNotEnabled("MT5 executor is not started.")
        if self.settings.trading_mode is TradingMode.LIVE and not self.settings.is_live_allowed():
            raise ExecutionNotEnabled("Live MT5 is blocked.")
