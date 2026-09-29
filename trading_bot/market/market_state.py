"""Combined view of the selected instrument."""

from __future__ import annotations

from dataclasses import dataclass

from trading_bot.market.price_reader import PriceQuote


@dataclass
class MarketState:
    symbol: str
    timeframe: str
    quote: PriceQuote | None = None
    last_candle_timestamp: str | None = None
