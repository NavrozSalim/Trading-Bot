"""Closed-candle poll → strategy → size → dry-run or MT5 demo."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from trading_bot.config import Settings
from trading_bot.database.database import Database
from trading_bot.exceptions import DuplicateSignalError, KillSwitchActive, RiskLimitError
from trading_bot.market.chart_prices import zoom_action
from trading_bot.market.kkc_vision import ColorMemory
from trading_bot.monitoring.health_monitor import HealthMonitor
from trading_bot.monitoring.logger import get_logger, log_action
from trading_bot.safety.duplicate_protection import DuplicateProtection
from trading_bot.safety.kill_switch import KillSwitch
from trading_bot.strategy.signals import Signal, SignalType
from trading_bot.strategy.strategy import SweepBreakoutStrategy
from trading_bot.trading.executor import OrderRequest, OrderResult, TradingExecutor
from trading_bot.trading.mt5_terminal import Mt5Terminal
from trading_bot.trading.position_manager import PositionManager
from trading_bot.trading.position_sizer import calculate_lot
from trading_bot.trading.trade_excel import TradeExcel
from trading_bot.trading.risk_manager import RiskManager

log = get_logger("trading_bot.engine")


class _ExpiredSetups:
    """Yellow and blue candles that already closed at a stop or target."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._times: set[str] = set()
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                self._times = {str(item) for item in loaded}
            except (OSError, json.JSONDecodeError, TypeError):
                self._times = set()

    def contains(self, signal: Signal) -> bool:
        anchor = str(signal.extra.get("anchor_time", ""))
        return bool(anchor) and anchor in self._times

    def add(self, anchor: str) -> None:
        if not anchor or anchor in self._times:
            return
        self._times.add(anchor)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self._times)), encoding="utf-8")


def _sizing_extreme(signal: Signal) -> float | None:
    """Lowest low after the yellow, or highest high after the blue. Not the 0.80 stop."""
    key = "sweep_low" if signal.signal is SignalType.BUY else "sweep_high"
    raw = signal.extra.get(key)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class SignalEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        strategy: SweepBreakoutStrategy,
        executor: TradingExecutor,
        terminal: Mt5Terminal | None,
        risk: RiskManager,
        duplicates: DuplicateProtection,
        positions: PositionManager,
        kill_switch: KillSwitch,
        health: HealthMonitor,
        database: Database,
    ) -> None:
        self.settings = settings
        self.strategy = strategy
        self.executor = executor
        self.terminal = terminal
        self.risk = risk
        self.duplicates = duplicates
        self.positions = positions
        self.kill_switch = kill_switch
        self.health = health
        self.database = database
        self._last_closed_ts: str | None = None
        self._seen_pnl: dict[str, float] = {}
        self._pending: Signal | None = None
        self._expired_setups = _ExpiredSetups(settings.logs_dir.parent / "data" / "expired_setups.json")
        self._ticket_anchor: dict[str, str] = {}
        self._stage_anchor = ""
        self._fresh_misses = 0
        self.color_reader = None
        self._color_memory = ColorMemory()
        self.journal = TradeExcel(settings.logs_dir.parent / "data" / "trades.xlsx")

    def _shown_reason(self, signal: Signal) -> str:
        """Stage checklist for the terminal. Reset when a new yellow or blue starts."""
        text = signal.extra.get("stage_log") or signal.reason
        anchor = str(signal.extra.get("anchor_time", ""))
        if anchor and self._stage_anchor and anchor != self._stage_anchor:
            text = "RESET | " + text
        if anchor:
            self._stage_anchor = anchor
        return text

    def _expire_anchor(self, signal: Signal) -> None:
        anchor = str(signal.extra.get("anchor_time", ""))
        if anchor:
            self._expired_setups.add(anchor)

    @staticmethod
    def _forming_reached_target(forming, target: float, buy: bool) -> bool:
        if buy:
            return float(forming.high) >= float(target)
        return float(forming.low) <= float(target)

    async def poll_once(self) -> None:
        if self.terminal is None:
            log.warning("engine_skipped_no_mt5_terminal")
            return
        if self.kill_switch.should_stop_loop():
            return
        symbol = self.settings.mt5_symbol
        chart_colors: list[str] | None = None
        fresh_colors: list[str] | None = None
        if self.color_reader is not None and hasattr(self.color_reader, "read_market"):
            market = await self.color_reader.read_market()
            if market is None:
                if await self._zoom_chart(symbol, int(getattr(self.color_reader, "last_bars", 0) or 0), None):
                    return
                await self._log_unreadable_chart(symbol)
                return
            if market.clipped:
                fit = getattr(self.color_reader, "fit_price_scale", None)
                if fit is not None:
                    await fit()
                await self._log_cut_off_candle(symbol)
                return
            if await self._zoom_chart(symbol, len(market.closed), market.price_span):
                return
            candles = market.closed
            forming = market.forming
            chart_colors = list(market.colors)
            fresh_colors = list(market.fresh_colors or market.colors)
            price = float(forming.close)
        else:
            candles = await asyncio.to_thread(
                self.terminal.closed_candles, symbol, self.settings.timeframe, 80
            )
            if not candles:
                return
            price = candles[-1].close
            forming = await self._forming_candle(symbol)
        if not candles:
            return
        last_ts = str(candles[-1].timestamp)
        self.health.update(current_price=str(price), current_symbol=symbol)
        if fresh_colors is not None:
            self._note_fresh_colors(fresh_colors)
        if await self._try_retest_entry(forming, symbol):
            return
        if self._last_closed_ts is None:
            self._last_closed_ts = last_ts
            log.info("engine_synced_to_last_closed_candle", timestamp=last_ts)
            return
        if last_ts == self._last_closed_ts:
            await self._review_same_candle(candles, symbol, price, chart_colors)
            return
        self._last_closed_ts = last_ts
        colors = chart_colors if chart_colors is not None else await self._chart_colors(candles)
        signal = self.strategy.evaluate(
            candles, symbol=symbol, timeframe=self.settings.timeframe, colors=colors
        )
        self.health.update(latest_signal=signal.signal.value)
        await self.database.save_signal(signal)
        await self.database.log_event("signal", f"{signal.signal.value} {signal.reason}")
        account = await self._account_view()
        if signal.extra.get("missed_target") == "1":
            self._expire_anchor(signal)
            self._pending = None
            log_action(
                symbol=symbol,
                timeframe=self.settings.timeframe,
                signal=signal.extra.get("pattern", "NO_TRADE"),
                signal_reason=self._shown_reason(signal),
                current_price=price,
                trade_action="NO_TRADE",
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                **account,
            )
            return
        if signal.extra.get("await_retest") == "1":
            if self._expired_setups.contains(signal):
                self._pending = None
                log_action(
                    symbol=symbol,
                    timeframe=self.settings.timeframe,
                    signal=signal.extra.get("pattern", "NO_TRADE"),
                    signal_reason="this yellow or blue already finished at the stop or target",
                    current_price=price,
                    trade_action="NO_TRADE",
                    **account,
                )
                return
            reason = self._shown_reason(signal)
            if fresh_colors is not None and not self._fresh_shows(fresh_colors, signal):
                self._pending = None
                reason = reason.replace(
                    "SETUP COMPLETE — WAITING FOR RETEST",
                    "SETUP COMPLETE — COLOR NOT ON THIS PICTURE",
                )
            else:
                self._pending = signal
                if await self._try_retest_entry(forming, symbol):
                    return
            log_action(
                symbol=symbol,
                timeframe=self.settings.timeframe,
                signal=signal.extra.get("pattern", "NO_TRADE"),
                signal_reason=reason,
                current_price=price,
                trade_action="NO_TRADE",
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                **account,
            )
            return
        self._pending = None
        if signal.signal is SignalType.NO_TRADE:
            log_action(
                symbol=symbol,
                timeframe=self.settings.timeframe,
                signal=signal.signal.value,
                signal_reason=self._shown_reason(signal),
                current_price=price,
                trade_action="NO_TRADE",
                **account,
            )
            return
        await self._handle_entry(signal, price, account)

    async def _chart_colors(self, candles: list) -> list[str] | None:
        reader = self.color_reader
        if reader is None:
            return None
        timestamps = [str(candle.timestamp) for candle in candles]
        try:
            fresh = await reader.colors_for(len(candles))
        except Exception as exc:  # noqa: BLE001
            log.warning("tv_colors_unavailable", error=str(exc))
            fresh = []
        return self._color_memory.apply(timestamps, fresh)

    async def _forming_candle(self, symbol: str):
        if self.terminal is None or not hasattr(self.terminal, "forming_candle"):
            return None
        try:
            return await asyncio.to_thread(
                self.terminal.forming_candle, symbol, self.settings.timeframe
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("forming_candle_unavailable", error=str(exc))
            return None

    def _fresh_shows(self, fresh: list[str], signal) -> bool:
        color = "yellow" if str(signal.extra.get("pattern", "")).startswith("buy") else "blue"
        return color in fresh

    def _note_fresh_colors(self, fresh: list[str]) -> None:
        pending = self._pending
        if pending is None:
            return
        if self._fresh_shows(fresh, pending):
            self._fresh_misses = 0
            return
        self._fresh_misses += 1
        if self._fresh_misses >= 2:
            log.info("pending_cleared_color_left_the_chart")
            self._pending = None
            self._fresh_misses = 0

    async def _try_retest_entry(self, forming, symbol: str) -> bool:
        """Enter as soon as a later candle touches the yellow or blue. Do not wait for its close."""
        pending = self._pending
        if pending is None or forming is None:
            return False
        if str(forming.timestamp) == str(pending.candle_timestamp):
            return False
        level = float(pending.extra.get("retest_level", "0"))
        buy = str(pending.extra.get("pattern", "")).startswith("buy")
        if pending.take_profit is not None and self._forming_reached_target(forming, pending.take_profit, buy):
            self._expire_anchor(pending)
            self._pending = None
            account = await self._account_view()
            log_action(
                symbol=symbol,
                timeframe=self.settings.timeframe,
                signal=pending.extra.get("pattern", "NO_TRADE"),
                signal_reason=(
                    "yellow_target_reached_without_retest"
                    if buy
                    else "blue_target_reached_without_retest"
                ),
                current_price=forming.close,
                trade_action="NO_TRADE",
                stop_loss=pending.stop_loss,
                take_profit=pending.take_profit,
                **account,
            )
            return True
        touched = forming.low <= level if buy else forming.high >= level
        if not touched:
            return False
        account = await self._account_view()
        if self._expired_setups.contains(pending):
            self._pending = None
            return False
        side = SignalType.BUY if buy else SignalType.SELL
        entry = float(forming.close)
        order = Signal(
            signal=side,
            reason="retest_touch",
            symbol=symbol,
            timeframe=self.settings.timeframe,
            candle_timestamp=str(forming.timestamp),
            price=entry,
            stop_loss=pending.stop_loss,
            take_profit=pending.take_profit,
            extra=pending.extra,
        )
        self._pending = None
        await self._handle_entry(order, entry, account)
        return True

    async def _zoom_chart(self, symbol: str, bars: int, price_span: float | None) -> bool:
        action = zoom_action(bars, price_span)
        adjust = getattr(self.color_reader, "adjust_zoom", None)
        if action is None or adjust is None:
            return False
        moved = await adjust(action)
        if not moved:
            return False
        self._pending = None
        await self._log_chart_zoom(symbol)
        return True

    async def _log_chart_zoom(self, symbol: str) -> None:
        account = await self._account_view()
        log_action(
            symbol=symbol,
            timeframe=self.settings.timeframe,
            signal="NO_TRADE",
            signal_reason=(
                "CHART ZOOM — setting the chart to about 45 candles and 14 points of price. "
                "No trade on this check."
            ),
            trade_action="NO_TRADE",
            **account,
        )

    async def _log_cut_off_candle(self, symbol: str) -> None:
        account = await self._account_view()
        log_action(
            symbol=symbol,
            timeframe=self.settings.timeframe,
            signal="NO_TRADE",
            signal_reason=(
                "CANDLE CUT OFF AT THE EDGE — the price scale was reset. "
                "No trade until the next picture shows the full candle."
            ),
            trade_action="NO_TRADE",
            **account,
        )

    async def _log_unreadable_chart(self, symbol: str) -> None:
        account = await self._account_view()
        log_action(
            symbol=symbol,
            timeframe=self.settings.timeframe,
            signal="NO_TRADE",
            signal_reason=(
                "CHART PRICES NOT READ — hide the price tag and keep the full numbers, "
                "like 4,160, inside the chart box"
            ),
            trade_action="NO_TRADE",
            **account,
        )

    async def _review_same_candle(
        self,
        candles: list,
        symbol: str,
        price: float,
        colors: list[str] | None = None,
    ) -> None:
        """Print a check on the candle already seen. Do not send another order."""
        if colors is None:
            colors = await self._chart_colors(candles)
        signal = self.strategy.evaluate(
            candles, symbol=symbol, timeframe=self.settings.timeframe, colors=colors
        )
        self.health.update(latest_signal=signal.signal.value)
        account = await self._account_view()
        if signal.extra.get("await_retest") == "1" and self._expired_setups.contains(signal):
            reason = "this yellow or blue already finished at the stop or target"
        elif signal.extra.get("stage_log"):
            reason = self._shown_reason(signal)
        elif signal.signal is not SignalType.NO_TRADE:
            reason = "same closed candle, entry already checked"
        else:
            reason = signal.reason
        log_action(
            symbol=symbol,
            timeframe=self.settings.timeframe,
            signal=SignalType.NO_TRADE.value,
            signal_reason=reason,
            current_price=price,
            trade_action="NO_TRADE",
            **account,
        )

    async def _account_view(self) -> dict[str, float | int]:
        """Live open-trade count plus floating and today's closed profit/loss."""
        positions = []
        try:
            positions = await self.executor.get_positions()
        except Exception as exc:  # noqa: BLE001
            log.warning("positions_unavailable", error=str(exc))
        current = {p.broker_id: float(p.unrealized_pnl or 0) for p in positions}
        for ticket, last_pnl in self._seen_pnl.items():
            if ticket in current:
                continue
            realized = last_pnl
            if self.terminal is not None:
                try:
                    looked = await asyncio.to_thread(
                        self.terminal.position_close_profit, int(ticket)
                    )
                except (TypeError, ValueError, Exception) as exc:  # noqa: BLE001
                    log.warning("close_profit_unavailable", ticket=ticket, error=str(exc))
                    looked = None
                if looked is not None:
                    realized = looked
            self.risk.record_close(realized)
            await self._record_excel_close(ticket, realized)
            anchor = self._ticket_anchor.pop(str(ticket), "")
            if anchor:
                self._expired_setups.add(anchor)
                if self._pending is not None and str(self._pending.extra.get("anchor_time", "")) == anchor:
                    self._pending = None
                log.info("setup_expired_after_close", anchor=anchor, ticket=str(ticket))
        self._seen_pnl = current
        self.risk.set_open_trades(len(positions))
        return {
            "open_trades": len(positions),
            "max_open_trades": self.settings.max_open_trades,
            "floating_pnl": sum(current.values()),
            "realized_pnl": self.risk.state.realized_pnl_today,
        }

    async def _handle_entry(self, signal: Signal, price: float, account: dict) -> None:
        try:
            self.kill_switch.assert_can_open_new_trades()
            self.duplicates.assert_new(signal)
        except (KillSwitchActive, DuplicateSignalError) as exc:
            log.warning("entry_blocked", error=str(exc), signal_id=signal.signal_id)
            return
        if signal.stop_loss is None:
            log.error("signal_missing_stop", signal_id=signal.signal_id)
            return
        try:
            balance = await self.executor.get_account_balance()
            if balance is None:
                raise RiskLimitError("Account balance is unavailable.")
            if self.terminal is None:
                raise RiskLimitError("MT5 terminal is not connected.")
            contract = await asyncio.to_thread(self.terminal.contract, signal.symbol)
            sized = calculate_lot(
                balance=balance,
                risk_pct=self.settings.risk_per_trade_pct,
                entry=float(signal.price or price),
                stop_loss=signal.stop_loss,
                direction=signal.signal.value,
                contract=contract,
                max_position_size=self.settings.max_position_size,
                sizing_extreme=_sizing_extreme(signal),
            )
            self.risk.assert_can_open(sized.quantity)
        except (RiskLimitError, Exception) as exc:
            log.error("size_or_risk_blocked", error=str(exc), signal_id=signal.signal_id)
            log_action(
                symbol=signal.symbol,
                signal=signal.signal.value,
                signal_reason=signal.reason,
                current_price=price,
                trade_action="BLOCKED",
                error=str(exc),
                order_result="NOT_SENT",
                **account,
            )
            return

        order = OrderRequest(
            symbol=signal.symbol,
            direction=signal.signal.value,
            quantity=sized.quantity,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            signal_id=signal.signal_id,
            expected_price=float(signal.price or price),
        )
        if self.settings.dry_run:
            self._remember_anchor(signal, f"DRY-{signal.signal_id}")
            self.duplicates.mark_executed(signal)
            await self.database.mark_signal_executed(signal.signal_id)
            await self._record_excel_open(
                signal, price, sized.quantity, balance, ticket=f"DRY-{signal.signal_id}"
            )
            log_action(
                symbol=signal.symbol,
                timeframe=self.settings.timeframe,
                signal=signal.signal.value,
                signal_reason=signal.reason,
                current_price=price,
                trade_action="DRY_RUN",
                position_size=sized.quantity,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                order_result="WOULD_ENTER",
                **account,
            )
            return

        result = await self._submit(order)
        if result.accepted and result.position is not None:
            self._seen_pnl[result.position.broker_id] = float(
                result.position.unrealized_pnl or 0
            )
            account = {
                **account,
                "open_trades": len(self._seen_pnl),
                "floating_pnl": sum(self._seen_pnl.values()),
            }
        log_action(
            symbol=signal.symbol,
            timeframe=self.settings.timeframe,
            signal=signal.signal.value,
            signal_reason=signal.reason,
            current_price=price,
            trade_action="MT5_MARKET_THEN_SLTP",
            position_size=sized.quantity,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            order_result=result.message,
            **account,
        )
        if result.accepted:
            if result.position:
                self._remember_anchor(signal, result.position.broker_id)
            self.duplicates.mark_executed(signal)
            await self.database.mark_signal_executed(signal.signal_id)
            self.risk.record_open()
            if result.position:
                self.positions.remember_bot_position(result.position.broker_id)
                await self.database.save_trade(
                    result.position.broker_id,
                    signal,
                    sized.quantity,
                    result.position.entry_price,
                    self.settings.trading_mode.value,
                )
                await self._record_excel_open(
                    signal,
                    result.position.entry_price or price,
                    sized.quantity,
                    balance,
                    ticket=result.position.broker_id,
                )
        else:
            await self.database.log_event("order_not_verified", result.message)

    async def _record_excel_open(
        self,
        signal: Signal,
        price: float,
        quantity: float,
        balance: float,
        *,
        ticket: str,
    ) -> None:
        extra = signal.extra
        buy = signal.signal is SignalType.BUY
        setup = "yellow" if buy or extra.get("yellow_low") else "blue"
        low = extra.get("yellow_low") or extra.get("blue_low") or ""
        high = extra.get("yellow_high") or extra.get("blue_high") or ""
        sweep = extra.get("sweep_low") if buy else extra.get("sweep_high")
        extreme = _sizing_extreme(signal)
        points = ""
        if extreme is not None and self.terminal is not None:
            try:
                contract = await asyncio.to_thread(self.terminal.contract, signal.symbol)
                if contract.tick_size > 0:
                    points = round(abs(float(price) - extreme) / contract.tick_size, 1)
            except Exception as exc:  # noqa: BLE001
                log.warning("trade_excel_points_unavailable", error=str(exc))
        risk_money = round(balance * (self.settings.risk_per_trade_pct / 100.0), 2)
        entry_type = "retest touch" if signal.reason == "retest_touch" else "candle close"
        mode = "DRY RUN" if self.settings.dry_run else self.settings.trading_mode.value
        row = {
            "Entry time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "Symbol": signal.symbol,
            "Timeframe": signal.timeframe,
            "Buy or sell": signal.signal.value,
            "Setup": setup,
            "Setup low": low,
            "Setup high": high,
            "Sweep price": sweep or "",
            "Breakout price": signal.price if signal.price is not None else price,
            "Entry type": entry_type,
            "Entry price": price,
            "Stop": signal.stop_loss,
            "Target": signal.take_profit,
            "Points": points,
            "Balance": balance,
            "Risk percent": self.settings.risk_per_trade_pct,
            "Risk amount": risk_money,
            "Lot": quantity,
            "Ticket": ticket,
            "Mode": mode,
        }
        try:
            await asyncio.to_thread(self.journal.record_open, row)
        except Exception as exc:  # noqa: BLE001
            log.warning("trade_excel_open_failed", error=str(exc))

    async def _record_excel_close(self, ticket: str, profit: float) -> None:
        exit_price: float | None = None
        if self.terminal is not None:
            try:
                exit_price, looked = await asyncio.to_thread(
                    self.terminal.position_close_details, int(ticket)
                )
                if looked is not None:
                    profit = looked
            except (TypeError, ValueError, Exception) as exc:  # noqa: BLE001
                log.warning("trade_excel_close_details_unavailable", ticket=ticket, error=str(exc))
        try:
            await asyncio.to_thread(
                self.journal.record_close,
                str(ticket),
                exit_price=exit_price,
                profit=profit,
                exit_reason="closed",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("trade_excel_close_failed", ticket=ticket, error=str(exc))

    def _remember_anchor(self, signal: Signal, ticket: str) -> None:
        anchor = str(signal.extra.get("anchor_time", ""))
        if anchor:
            self._ticket_anchor[str(ticket)] = anchor

    async def _submit(self, order: OrderRequest) -> OrderResult:
        if order.direction == "BUY":
            return await self.executor.open_long(order)
        return await self.executor.open_short(order)
