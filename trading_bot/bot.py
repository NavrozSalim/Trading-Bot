"""Runtime: TradingView chart (optional) + MT5 demo execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from trading_bot.browser.browser_manager import BrowserManager
from trading_bot.browser.login import AuthStatus, LoginManager
from trading_bot.browser.navigation import Navigator
from trading_bot.browser.selectors import SelectorRegistry
from trading_bot.config import Settings
from trading_bot.database.database import Database
from trading_bot.monitoring.health_monitor import HealthMonitor
from trading_bot.monitoring.logger import get_logger
from trading_bot.market.kkc_vision import ScreenColorReader
from trading_bot.monitoring.screenshots import ScreenshotManager
from trading_bot.safety.duplicate_protection import DuplicateProtection
from trading_bot.safety.kill_switch import KillSwitch
from trading_bot.safety.page_validator import PageValidator
from trading_bot.safety.stale_data import StaleDataGuard
from trading_bot.strategy.strategy import get_strategy
from trading_bot.trading.engine import SignalEngine
from trading_bot.trading.executor import NullExecutor, TradingExecutor
from trading_bot.trading.mt5_executor import Mt5Executor
from trading_bot.trading.position_manager import PositionManager
from trading_bot.trading.risk_manager import RiskManager

log = get_logger("trading_bot.runtime")


@dataclass
class BotContext:
    settings: Settings
    registry: SelectorRegistry
    screenshots: ScreenshotManager
    health: HealthMonitor
    kill_switch: KillSwitch
    browser: BrowserManager
    login: LoginManager
    navigator: Navigator
    validator: PageValidator
    stale: StaleDataGuard
    database: Database
    executor: TradingExecutor
    positions: PositionManager
    risk: RiskManager
    duplicates: DuplicateProtection
    engine: SignalEngine


def build_context(settings: Settings) -> BotContext:
    settings.ensure_runtime_dirs()
    screenshots = ScreenshotManager(settings.screenshots_dir)
    health = HealthMonitor()
    kill_switch = KillSwitch()
    registry = SelectorRegistry(settings.selectors_file)
    browser = BrowserManager(settings, screenshots)
    login = LoginManager(settings, registry, screenshots)
    navigator = Navigator(settings, registry)
    validator = PageValidator(settings, registry, navigator, screenshots, kill_switch)
    stale = StaleDataGuard(settings.max_price_staleness_seconds)
    database = Database(settings)
    duplicates = DuplicateProtection()
    if settings.execution_backend == "MT5":
        executor: TradingExecutor = Mt5Executor(settings)
        terminal = executor.terminal  # type: ignore[attr-defined]
    else:
        executor = NullExecutor()
        terminal = None
    positions = PositionManager(executor, kill_switch)
    risk = RiskManager(settings)
    strategy = get_strategy(sl_offset=settings.sl_offset, tp_rr=settings.tp_rr)
    engine = SignalEngine(
        settings,
        strategy=strategy,
        executor=executor,
        terminal=terminal,
        risk=risk,
        duplicates=duplicates,
        positions=positions,
        kill_switch=kill_switch,
        health=health,
        database=database,
    )
    health.update(
        bot_status="constructed",
        trading_mode=settings.trading_mode.value,
        dry_run=settings.dry_run,
        configured_symbol=settings.symbol,
    )
    return BotContext(
        settings=settings,
        registry=registry,
        screenshots=screenshots,
        health=health,
        kill_switch=kill_switch,
        browser=browser,
        login=login,
        navigator=navigator,
        validator=validator,
        stale=stale,
        database=database,
        executor=executor,
        positions=positions,
        risk=risk,
        duplicates=duplicates,
        engine=engine,
    )


class TradingBot:
    def __init__(self, ctx: BotContext) -> None:
        self.ctx = ctx
        self._stop = asyncio.Event()

    def request_stop(self, reason: str = "operator_stop") -> None:
        self.ctx.kill_switch.stop_bot(reason)
        self._stop.set()

    async def run(self) -> None:
        ctx = self.ctx
        settings = ctx.settings
        settings.assert_execution_allowed()
        await ctx.database.start()
        await ctx.database.log_event("bot_start", "strategy + MT5 runtime starting")
        ctx.health.update(bot_status="starting")

        if isinstance(ctx.executor, Mt5Executor):
            print("Connecting to MetaTrader 5 (leave the terminal open, Algo Trading ON)...")
            await ctx.executor.start()
            balance = await ctx.executor.get_account_balance()
            print(f"MT5 connected. Balance: {balance}")
            if not ctx.executor.terminal.algo_trading_enabled():
                print(
                    "WARNING: Algo Trading is OFF. Click Algo Trading in MT5 "
                    "(green) or the bot cannot place orders."
                )
            if not ctx.executor.terminal.server_algo_allowed():
                print(
                    "WARNING: Promax has Expert Advisors OFF on this account "
                    "(trade_expert=false). Enable algorithmic trading for this "
                    "login in the Promax client area, then restart MT5."
                )

        if settings.open_tradingview:
            try:
                page = await ctx.browser.start()
                ctx.health.update(browser_status="connected")
                await ctx.navigator.open_broker(page)
                auth = await ctx.login.ensure_logged_in(page)
                ctx.health.update(logged_in=auth is AuthStatus.LOGGED_IN)
            except Exception as exc:  # noqa: BLE001
                log.warning("tradingview_optional_failed", error=str(exc))
                print(f"TradingView chart not opened ({exc}). MT5 strategy loop will still run.")
                ctx.health.update(browser_status="disconnected", last_error=str(exc))

        ctx.engine.color_reader = ScreenColorReader()
        ctx.health.update(bot_status="running")
        mode = "DRY RUN" if settings.dry_run else settings.trading_mode.value
        print(f"\nBot running ({mode}). Strategy: yellow/blue sweep + 2-candle breakout.")
        print("Orders go to MT5. The setup high, low, and close are read from the TradingView chart.")
        if not ctx.engine.color_reader.ready:
            print("Chart area is not calibrated, so no trade will be sent.")
            print("In Command Prompt run:  python calibrate.py")
        print("Ctrl+C to stop.\n")
        await ctx.database.log_event("bot_running", f"engine loop {mode}")

        try:
            await self._loop()
        finally:
            ctx.health.update(bot_status="stopping")
            if isinstance(ctx.executor, Mt5Executor):
                await ctx.executor.stop()
            await ctx.database.log_event("bot_stop", "runtime exiting")
            await ctx.browser.close()
            await ctx.database.close()
            ctx.health.update(bot_status="stopped", browser_status="disconnected")

    async def _loop(self) -> None:
        ctx = self.ctx
        while not self._stop.is_set() and not ctx.kill_switch.should_stop_loop():
            try:
                await ctx.engine.poll_once()
                await ctx.positions.sync()
                if ctx.kill_switch.close_all_requested and not ctx.settings.dry_run:
                    await ctx.executor.close_all_positions()
                    ctx.kill_switch.acknowledge_close_all()
            except Exception as exc:  # noqa: BLE001
                log.exception("engine_loop_error", error=str(exc))
                ctx.health.update(last_error=str(exc))
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=ctx.settings.candle_poll_seconds
                )
            except TimeoutError:
                continue
