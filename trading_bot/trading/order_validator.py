"""Compare intended order vs what the ticket / positions panel actually shows."""

from __future__ import annotations

from dataclasses import dataclass

from trading_bot.exceptions import OrderVerificationError
from trading_bot.trading.executor import OrderRequest, Position


@dataclass(frozen=True)
class TicketSnapshot:
    side: str | None
    symbol: str | None
    quantity: str | None
    stop_loss: str | None
    take_profit: str | None


def _norm(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip().upper().replace(",", "")


class OrderValidator:
    def ticket_matches(self, expected: OrderRequest, ticket: TicketSnapshot) -> None:
        checks = {
            "side": (_norm(expected.direction), _norm(ticket.side)),
            "symbol": (_norm(expected.symbol), _norm(ticket.symbol)),
            "quantity": (_norm(expected.quantity), _norm(ticket.quantity)),
            "stop_loss": (_norm(expected.stop_loss), _norm(ticket.stop_loss)),
            "take_profit": (_norm(expected.take_profit), _norm(ticket.take_profit)),
        }
        mismatches = [name for name, (want, got) in checks.items() if got and want != got]
        missing = [name for name, (_want, got) in checks.items() if not got]
        if missing:
            raise OrderVerificationError(
                "Order ticket is missing fields; refusing Confirm: " + ", ".join(missing)
            )
        if mismatches:
            raise OrderVerificationError(
                "Order ticket does not match expected values: " + ", ".join(mismatches)
            )

    def position_matches(self, expected: OrderRequest, position: Position) -> None:
        if _norm(position.symbol) != _norm(expected.symbol):
            raise OrderVerificationError("Opened position symbol mismatch.")
        if _norm(position.direction) != _norm(expected.direction):
            raise OrderVerificationError("Opened position direction mismatch.")
        if position.quantity != expected.quantity:
            raise OrderVerificationError("Opened position quantity mismatch.")
