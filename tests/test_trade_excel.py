from trading_bot.trading.trade_excel import TradeExcel


def test_trade_excel_saves_open_and_close(tmp_path) -> None:  # type: ignore[no-untyped-def]
    book = TradeExcel(tmp_path / "trades.xlsx")
    book.record_open(
        {
            "Entry time": "2026-09-25 08:00:00",
            "Symbol": "XAUUSD",
            "Timeframe": "1M",
            "Buy or sell": "BUY",
            "Setup": "yellow",
            "Setup low": "4398.2",
            "Setup high": "4400",
            "Sweep price": "4398.2",
            "Breakout price": "4405",
            "Entry type": "candle close",
            "Entry price": 4405,
            "Stop": 4397.4,
            "Target": 4411.8,
            "Points": 680,
            "Balance": 500,
            "Risk percent": 2,
            "Risk amount": 10,
            "Lot": 0.02,
            "Ticket": "1001",
            "Mode": "DEMO",
        }
    )
    book.record_close("1001", exit_price=4411.8, profit=12.5, exit_reason="closed")
    from openpyxl import load_workbook

    sheet = load_workbook(book.path).active
    assert sheet["E2"].value == "BUY"
    assert sheet["S2"].value == 0.02
    assert sheet["T2"].value == "1001"
    assert sheet["U2"].value == 4411.8
    assert sheet["V2"].value == 12.5
    assert sheet["W2"].value == "target"
