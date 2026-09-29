"""One Excel row per trade, updated when the position closes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.trade_excel")

HEADERS = [
    "Entry time",
    "Exit time",
    "Symbol",
    "Timeframe",
    "Buy or sell",
    "Setup",
    "Setup low",
    "Setup high",
    "Sweep price",
    "Breakout price",
    "Entry type",
    "Entry price",
    "Stop",
    "Target",
    "Points",
    "Balance",
    "Risk percent",
    "Risk amount",
    "Lot",
    "Ticket",
    "Exit price",
    "Profit or loss",
    "Exit reason",
    "Mode",
]

_TICKET_COL = HEADERS.index("Ticket") + 1


def _exit_label(exit_price: float | None, stop: object, target: object, fallback: str) -> str:
    if exit_price is None:
        return fallback
    try:
        if stop not in ("", None) and abs(float(exit_price) - float(stop)) <= 0.05:
            return "stop"
        if target not in ("", None) and abs(float(exit_price) - float(target)) <= 0.05:
            return "target"
    except (TypeError, ValueError):
        return fallback
    return fallback


class TradeExcel:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record_open(self, row: dict[str, object]) -> None:
        sheet = self._sheet()
        values = [row.get(name, "") for name in HEADERS]
        sheet.append(values)
        self._save(sheet.parent)

    def record_close(
        self,
        ticket: str,
        *,
        exit_price: float | None,
        profit: float | None,
        exit_reason: str,
        exit_time: datetime | None = None,
    ) -> None:
        sheet = self._sheet()
        stamp = (exit_time or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
        for cells in sheet.iter_rows(min_row=2):
            if str(cells[_TICKET_COL - 1].value or "") != str(ticket):
                continue
            cells[HEADERS.index("Exit time")].value = stamp
            cells[HEADERS.index("Exit price")].value = exit_price
            cells[HEADERS.index("Profit or loss")].value = profit
            cells[HEADERS.index("Exit reason")].value = _exit_label(
                exit_price,
                cells[HEADERS.index("Stop")].value,
                cells[HEADERS.index("Target")].value,
                exit_reason,
            )
            self._save(sheet.parent)
            return
        log.warning("trade_excel_ticket_missing", ticket=ticket)

    def _sheet(self) -> Worksheet:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            book = load_workbook(self.path)
            return book.active
        book = Workbook()
        sheet = book.active
        sheet.title = "Trades"
        sheet.append(HEADERS)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        return sheet

    def _save(self, book: Workbook) -> None:
        try:
            book.save(self.path)
        except PermissionError:
            log.warning("trade_excel_locked", path=str(self.path))
