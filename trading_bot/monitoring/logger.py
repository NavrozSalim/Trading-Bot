"""Structured logging. File = JSON lines. Console = human-readable."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from structlog.stdlib import BoundLogger


def setup_logging(logs_dir: Path, level: str = "INFO") -> BoundLogger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = logs_dir / f"bot_{day}.log"

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()),
        ],
        foreign_pre_chain=shared_processors,
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(json_formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(_QuietMachineLines())
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logger = structlog.get_logger("trading_bot")
    logger.info("logging_initialized", log_file=str(log_file), level=level.upper())
    return logger


class _QuietMachineLines(logging.Filter):
    """Keep the JSON file log. Hide duplicate machine lines from the terminal."""

    _DROP = ("bot_action", "lot_calculated", "size_or_risk_blocked")

    def filter(self, record: logging.LogRecord) -> bool:
        event = ""
        if isinstance(record.msg, dict):
            event = str(record.msg.get("event", ""))
        else:
            event = record.getMessage()
        return not any(token in event for token in self._DROP)


def get_logger(name: str | None = None) -> BoundLogger:
    return structlog.get_logger(name or "trading_bot")


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}"


def _plain_reason(reason: str | None, error: str | None) -> str:
    text = (error or reason or "").strip()
    if "MAX_OPEN_TRADES" in text:
        return "already at the maximum number of open trades"
    if "MAX_DAILY_LOSS" in text:
        return "daily loss limit reached"
    if "MAX_TRADES_PER_DAY" in text:
        return "daily trade limit reached"
    if "MAX_CONSECUTIVE_LOSSES" in text:
        return "too many losses in a row"
    labels = {
        "no_sweep_breakout_pattern": "this candle did not match the entry pattern",
        "no_yellow_or_blue_on_chart": "no yellow or blue candle on the TradingView chart",
        "blue_on_chart_no_breakout": "blue candle on the chart, sell is not ready",
        "yellow_on_chart_no_breakout": "yellow candle on the chart, buy is not ready",
        "not_enough_candles": "not enough candles yet",
        "yellow_low_sweep_two_green_breakout": "buy setup: low sweep, then two green candles",
        "blue_high_sweep_two_red_breakout": "sell setup: high sweep, then two red candles",
        "yellow_target_reached_without_retest": "price already reached the buy target without a retest",
        "blue_target_reached_without_retest": "price already reached the sell target without a retest",
        "await_yellow_retest": "SETUP COMPLETE — WAITING FOR RETEST",
        "await_blue_retest": "SETUP COMPLETE — WAITING FOR RETEST",
        "retest_touch": "RETEST DETECTED — ENTRY CONFIRMED",
        "strategy_not_configured": "strategy is not configured",
    }
    return labels.get(text, text.replace("_", " ") or "no extra detail")


def format_action_text(
    *,
    symbol: str | None = None,
    signal: str | None = None,
    signal_reason: str | None = None,
    current_price: str | float | None = None,
    trade_action: str | None = None,
    position_size: str | float | None = None,
    stop_loss: str | float | None = None,
    take_profit: str | float | None = None,
    order_result: str | None = None,
    error: str | None = None,
    open_trades: int | None = None,
    max_open_trades: int | None = None,
    floating_pnl: float | None = None,
    realized_pnl: float | None = None,
) -> str:
    """One readable block for the terminal."""
    clock = datetime.now(timezone.utc).strftime("%H:%M:%S")
    side = (signal or trade_action or "CHECK").upper()
    if trade_action == "NO_TRADE" or side == "NO_TRADE":
        headline = f"{clock}  {symbol or '-'}  no trade"
    elif trade_action == "BLOCKED" or order_result == "NOT_SENT":
        headline = f"{clock}  {symbol or '-'}  {side} skipped"
    elif order_result in ("EXECUTED", "WOULD_ENTER"):
        verb = "would send" if order_result == "WOULD_ENTER" else "filled"
        headline = f"{clock}  {symbol or '-'}  {side} {verb}"
    else:
        headline = f"{clock}  {symbol or '-'}  {side}"

    lines = [headline, f"  Why: {_plain_reason(signal_reason, error)}"]
    if current_price is not None:
        lines.append(f"  Price: {current_price}")
    if position_size is not None:
        lines.append(f"  Size: {position_size}")
    if stop_loss is not None:
        lines.append(f"  Stop: {stop_loss}")
    if take_profit is not None:
        lines.append(f"  Target: {take_profit}")
    if open_trades is not None:
        cap = f"/{max_open_trades}" if max_open_trades is not None else ""
        lines.append(f"  Open trades: {open_trades}{cap}")
    if floating_pnl is not None:
        lines.append(f"  Open profit/loss: {_money(floating_pnl)}")
    if realized_pnl is not None:
        lines.append(f"  Closed today: {_money(realized_pnl)}")
    return "\n".join(lines)


def log_action(
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    signal: str | None = None,
    signal_reason: str | None = None,
    current_price: str | float | None = None,
    trade_action: str | None = None,
    position_size: str | float | None = None,
    stop_loss: str | float | None = None,
    take_profit: str | float | None = None,
    browser_status: str | None = None,
    order_result: str | None = None,
    error: str | None = None,
    open_trades: int | None = None,
    max_open_trades: int | None = None,
    floating_pnl: float | None = None,
    realized_pnl: float | None = None,
    **extra: Any,
) -> None:
    """One structured event, plus a plain block on the terminal."""
    logger = get_logger("trading_bot.action")
    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "signal": signal,
        "signal_reason": signal_reason,
        "current_price": current_price,
        "trade_action": trade_action,
        "position_size": position_size,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "browser_status": browser_status,
        "order_result": order_result,
        "error": error,
        "open_trades": open_trades,
        "max_open_trades": max_open_trades,
        "floating_pnl": floating_pnl,
        "realized_pnl": realized_pnl,
        **extra,
    }
    logger.info("bot_action", **{k: v for k, v in payload.items() if v is not None})
    text = format_action_text(
        symbol=symbol,
        signal=signal,
        signal_reason=signal_reason,
        current_price=current_price,
        trade_action=trade_action,
        position_size=position_size,
        stop_loss=stop_loss,
        take_profit=take_profit,
        order_result=order_result,
        error=error,
        open_trades=open_trades,
        max_open_trades=max_open_trades,
        floating_pnl=floating_pnl,
        realized_pnl=realized_pnl,
    )
    print(f"\n{text}\n", flush=True)
    logger.info("bot_action_text", text=text.replace("\n", " | "))
