"""Broker-agnostic execution interface.

Strategy code must depend on this ABC, never on Playwright.
Later: BrokerAPIExecutor(TradingExecutor) can replace PlaywrightExecutor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Position:
    broker_id: str
    signal_id: str | None
    symbol: str
    direction: str
    quantity: float
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    opened_at: str | None
    unrealized_pnl: float | None = None
    opened_by_bot: bool = False


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    direction: str  # BUY / SELL
    quantity: float
    stop_loss: float | None
    take_profit: float | None
    signal_id: str
    expected_price: float | None = None


@dataclass(frozen=True)
class OrderResult:
    accepted: bool
    verified: bool
    message: str
    position: Position | None = None
    dry_run: bool = True
    extra: dict[str, Any] | None = None


class TradingExecutor(ABC):
    @abstractmethod
    async def open_long(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def open_short(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def close_position(self, position: Position) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def close_all_positions(self) -> list[OrderResult]:
        raise NotImplementedError

    @abstractmethod
    async def set_stop_loss(self, position: Position, stop_loss: float) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def set_take_profit(self, position: Position, take_profit: float) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    async def get_account_balance(self) -> float | None:
        raise NotImplementedError


class NullExecutor(TradingExecutor):
    """Phase 1–2: records intent only. Never clicks Confirm."""

    async def open_long(self, order: OrderRequest) -> OrderResult:
        return OrderResult(
            accepted=False,
            verified=False,
            message="execution_disabled_phase_1_2",
            dry_run=True,
            extra={"order": order.__dict__},
        )

    async def open_short(self, order: OrderRequest) -> OrderResult:
        return await self.open_long(order)

    async def close_position(self, position: Position) -> OrderResult:
        return OrderResult(
            accepted=False,
            verified=False,
            message="execution_disabled_phase_1_2",
            dry_run=True,
        )

    async def close_all_positions(self) -> list[OrderResult]:
        return []

    async def set_stop_loss(self, position: Position, stop_loss: float) -> OrderResult:
        return OrderResult(accepted=False, verified=False, message="execution_disabled_phase_1_2")

    async def set_take_profit(self, position: Position, take_profit: float) -> OrderResult:
        return OrderResult(accepted=False, verified=False, message="execution_disabled_phase_1_2")

    async def get_positions(self) -> list[Position]:
        return []

    async def get_account_balance(self) -> float | None:
        return None
