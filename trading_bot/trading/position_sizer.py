"""Position size from account risk and stop distance. No martingale."""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_bot.exceptions import RiskLimitError
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.sizer")


@dataclass(frozen=True)
class SymbolContract:
    volume_min: float
    volume_max: float
    volume_step: float
    tick_size: float
    tick_value: float  # money lost per tick per 1.0 lot


@dataclass(frozen=True)
class SizeResult:
    quantity: float
    risk_money: float
    sl_distance: float
    capped: bool
    reason: str


def _round_nearest_to_step(volume: float, step: float) -> float:
    if step <= 0:
        return volume
    steps = math.floor((volume / step) + 0.5 + 1e-12)
    raw = steps * step
    decimals = max(0, round(-math.log10(step))) if step < 1 else 0
    return round(raw, decimals + 2)


def calculate_lot(
    *,
    balance: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
    direction: str,
    contract: SymbolContract,
    max_position_size: float,
    sizing_extreme: float | None = None,
) -> SizeResult:
    if balance <= 0 or risk_pct <= 0:
        raise RiskLimitError("Balance or RISK_PER_TRADE_PCT is not usable.")
    if entry <= 0 or stop_loss <= 0:
        raise RiskLimitError("Entry or stop loss is missing.")

    side = direction.upper()
    if side == "BUY" and stop_loss >= entry:
        raise RiskLimitError("BUY stop loss must be below entry.")
    if side == "SELL" and stop_loss <= entry:
        raise RiskLimitError("SELL stop loss must be above entry.")

    # Lot uses the points from the setup extreme to the entry.
    # The order stop is farther out by the 0.80 offset and is not part of this count.
    measure = sizing_extreme if sizing_extreme is not None else stop_loss
    if side == "BUY" and measure >= entry:
        raise RiskLimitError("BUY point count must start below the entry.")
    if side == "SELL" and measure <= entry:
        raise RiskLimitError("SELL point count must start above the entry.")
    sl_distance = abs(entry - measure)
    if sl_distance <= 0:
        raise RiskLimitError("Stop distance is zero.")
    if contract.tick_size <= 0 or contract.tick_value <= 0:
        raise RiskLimitError("Symbol tick size/value could not be read.")

    ticks = sl_distance / contract.tick_size
    money_per_lot = ticks * contract.tick_value
    if money_per_lot <= 0:
        raise RiskLimitError("Loss per lot is not positive.")

    risk_money = balance * (risk_pct / 100.0)
    raw_lot = risk_money / money_per_lot
    capped = False
    if raw_lot > max_position_size > 0:
        raw_lot = max_position_size
        capped = True

    if raw_lot > contract.volume_max:
        raw_lot = contract.volume_max
        capped = True

    quantity = _round_nearest_to_step(raw_lot, contract.volume_step)
    if quantity < contract.volume_min:
        raise RiskLimitError(
            f"Computed lot {quantity} is below broker minimum {contract.volume_min}."
        )
    log.info(
        "lot_calculated",
        quantity=quantity,
        risk_money=round(risk_money, 2),
        sl_distance=sl_distance,
        capped=capped,
    )
    return SizeResult(
        quantity=quantity,
        risk_money=risk_money,
        sl_distance=sl_distance,
        capped=capped,
        reason="capped_to_max" if capped else "risk_pct",
    )
