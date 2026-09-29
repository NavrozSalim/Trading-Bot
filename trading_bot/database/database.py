"""SQLite (async) bootstrap. PostgreSQL can replace DATABASE_URL later."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading_bot.config import PROJECT_ROOT, Settings
from trading_bot.database.models import Base, EventRow, SignalRow, TradeRow
from trading_bot.monitoring.logger import get_logger
from trading_bot.strategy.signals import Signal

log = get_logger("trading_bot.database")


def _normalize_sqlite_url(url: str) -> str:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return url
    rest = url[len(prefix) :]
    if rest.startswith("./"):
        path = (PROJECT_ROOT / rest[2:]).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return prefix + path.as_posix()
    Path(rest).parent.mkdir(parents=True, exist_ok=True)
    return url


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None

    async def start(self) -> None:
        url = _normalize_sqlite_url(self.settings.database_url)
        self.engine = create_async_engine(url, echo=False, future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("database_ready", url=url)

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self.session_factory is None:
            raise RuntimeError("Database.start() was not called.")
        async with self.session_factory() as session:
            yield session

    async def log_event(
        self,
        event_type: str,
        message: str,
        screenshot_path: str | None = None,
    ) -> None:
        if self.session_factory is None:
            log.warning("event_not_persisted_db_not_started", type=event_type)
            return
        async with self.session_factory() as session:
            session.add(
                EventRow(type=event_type, message=message, screenshot_path=screenshot_path)
            )
            await session.commit()

    async def save_signal(self, signal: Signal) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as session:
            session.add(
                SignalRow(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    timeframe=signal.timeframe,
                    candle_timestamp=signal.candle_timestamp,
                    signal=signal.signal.value,
                    reason=signal.reason,
                    executed=False,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def mark_signal_executed(self, signal_id: str) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as session:
            row = await session.scalar(select(SignalRow).where(SignalRow.signal_id == signal_id))
            if row is None:
                return
            row.executed = True
            await session.commit()

    async def save_trade(
        self,
        trade_id: str,
        signal: Signal,
        quantity: float,
        entry_price: float | None,
        mode: str,
    ) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as session:
            session.add(
                TradeRow(
                    trade_id=trade_id,
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    direction=signal.signal.value,
                    quantity=quantity,
                    entry_price=entry_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    entry_time=datetime.now(timezone.utc),
                    mode=mode,
                )
            )
            await session.commit()
