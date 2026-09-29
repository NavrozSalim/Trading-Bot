"""CLI: python main.py --setup | --test-selectors | --run | --dashboard"""

from __future__ import annotations

import argparse
import asyncio
import sys

from trading_bot.config import Settings, get_settings
from trading_bot.exceptions import ConfigurationError
from trading_bot.monitoring.health_monitor import HealthMonitor
from trading_bot.monitoring.logger import setup_logging
from trading_bot.safety.kill_switch import KillSwitch


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Browser trading bot (DEMO/paper first). Never places live orders by default."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--setup", action="store_true", help="Open browser for manual login and selector binding")
    group.add_argument(
        "--copy-chrome-profile",
        action="store_true",
        help="Copy Chrome Profile 52 into ./browser_profile so debugging can attach",
    )
    group.add_argument("--test-selectors", action="store_true", help="Probe BUY/SELL/qty/SL/TP controls without trading")
    group.add_argument(
        "--run",
        action="store_true",
        help="Start the strategy loop (TradingView chart optional, MT5 demo execution)",
    )
    group.add_argument("--dashboard", action="store_true", help="Serve the local status dashboard")
    parser.add_argument("--headless", action="store_true", help="Override HEADLESS=true for this process")
    return parser.parse_args(argv)


async def _run_setup(settings: Settings) -> None:
    from trading_bot.browser.setup_tool import build_setup_stack

    wizard, browser = build_setup_stack(settings)
    try:
        await wizard.run_setup()
    finally:
        await browser.close()


def _copy_chrome_profile(settings: Settings) -> int:
    from trading_bot.browser.chrome_profile import assert_chrome_unlocked, copy_named_profile

    name = settings.chrome_profile_directory.strip() or "Default"
    source = settings.chrome_source_user_data_dir
    if source is None:
        print("Set CHROME_SOURCE_USER_DATA_DIR in .env first.")
        return 1
    try:
        assert_chrome_unlocked(require_closed=True)
        dest = copy_named_profile(source, settings.browser_profile_dir, name, force=True)
    except ConfigurationError as exc:
        print(exc)
        return 1
    print(f"Copied to {dest}")
    print("Next: python main.py --setup")
    return 0


async def _run_test_selectors(settings: Settings) -> None:
    from trading_bot.browser.setup_tool import build_setup_stack

    wizard, browser = build_setup_stack(settings)
    try:
        await wizard.run_test_selectors()
    finally:
        await browser.close()


async def _run_bot(settings: Settings) -> None:
    from trading_bot.bot import TradingBot, build_context

    ctx = build_context(settings)
    bot = TradingBot(ctx)
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(2, lambda: bot.request_stop("SIGINT"))  # SIGINT
    except (NotImplementedError, AttributeError):
        # Windows: signal handlers are limited; KeyboardInterrupt still works.
        pass
    await bot.run()


def _run_dashboard(settings: Settings) -> None:
    import uvicorn

    from trading_bot.dashboard.api import create_app

    health = HealthMonitor()
    health.update(
        bot_status="dashboard_only",
        trading_mode=settings.trading_mode.value,
        dry_run=settings.dry_run,
        configured_symbol=settings.symbol,
        extra={"note": "Start the bot with python main.py --run in another terminal."},
    )
    app = create_app(health, KillSwitch())
    uvicorn.run(app, host=settings.dashboard_host, port=settings.dashboard_port, log_level="info")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    if args.headless:
        settings.headless = True
    setup_logging(settings.logs_dir, settings.log_level)
    settings.assert_execution_allowed()

    if args.setup:
        asyncio.run(_run_setup(settings))
        return 0
    if args.copy_chrome_profile:
        return _copy_chrome_profile(settings)
    if args.test_selectors:
        asyncio.run(_run_test_selectors(settings))
        return 0
    if args.dashboard:
        _run_dashboard(settings)
        return 0
    if args.run:
        try:
            asyncio.run(_run_bot(settings))
        except KeyboardInterrupt:
            return 0
        return 0

    print("Specify one of: --setup  --copy-chrome-profile  --test-selectors  --run  --dashboard")
    print("See README.md for run instructions. Keep DRY_RUN=true until you want demo fills.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
