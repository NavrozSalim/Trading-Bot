"""Signal types and IDs. Strategy code returns these — it never clicks."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"
    NO_TRADE = "NO_TRADE"


def format_candle_timestamp(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M")
    text = str(value).strip().replace(" ", "T")
    if len(text) >= 16:
        return text[:16]
    return text


def build_signal_id(
    symbol: str,
    timeframe: str,
    candle_time: datetime | str,
    signal_type: str | SignalType,
) -> str:
    """BTCUSD_5M_2026-09-21T12:30_BUY"""
    kind = signal_type.value if isinstance(signal_type, SignalType) else str(signal_type)
    stamp = format_candle_timestamp(candle_time)
    return f"{symbol}_{timeframe}_{stamp}_{kind}"


class Signal(BaseModel):
    signal: SignalType = SignalType.NO_TRADE
    reason: str = "unconfigured_strategy"
    symbol: str
    timeframe: str
    candle_timestamp: str
    signal_id: str = ""
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    extra: dict[str, str] = Field(default_factory=dict)

    @field_validator("candle_timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: object) -> object:
        if isinstance(value, datetime):
            return format_candle_timestamp(value)
        return value

    def model_post_init(self, __context: object) -> None:
        if not self.signal_id:
            self.signal_id = build_signal_id(
                self.symbol, self.timeframe, self.candle_timestamp, self.signal
            )
