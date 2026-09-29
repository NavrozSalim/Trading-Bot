# Trading bot — TradingView chart + MetaTrader 5 demo

Python bot that reads closed XAUUSD M1 candles from a running **MetaTrader 5** terminal, evaluates a fixed sweep/breakout strategy, and (when not in dry-run) sends a **market order then attaches SL/TP** on the Promax demo account.

TradingView is **chart-only**. It never places orders. The strategy module does not import Playwright or MetaTrader5.

Defaults stay fail-closed: `TRADING_MODE=DEMO`, `DRY_RUN=true`. Live trading stays blocked unless you set both live flags.

---

## What it does

1. Connects to the already-open MT5 terminal (Algo Trading ON).
2. Optionally opens TradingView on XAUUSD 1m from Chrome Profile 52 (copied into `./browser_profile`).
3. On each **new closed** M1 candle, evaluates the yellow/blue sweep + 2-candle breakout rules.
4. Sizes the lot from `RISK_PER_TRADE_PCT` and the 80-cent stop, then caps at `MAX_POSITION_SIZE`.
5. With `DRY_RUN=true`, prints `WOULD_ENTER` and does not send an order.
6. With `DRY_RUN=false` on a **demo** account: market order first, then `TRADE_ACTION_SLTP`. No martingale. No invented take-profit (`TP_RR=0`).

Yellow/blue are the sweep candles from OHLC (not scraped pixel colors):

- **BUY** — 2 reds attached to the **last** yellow (a run of yellows is allowed). The cluster sweeps those lows; last yellow closes back above the swept low; then 2 greens with last close above that yellow close. SL = **last** yellow low − 0.80.
- **SELL** — 2 greens attached to the **last** blue (a run of blues is allowed). The cluster sweeps those highs; last blue closes back below the swept high; then 2 reds with last close below that blue close. SL = **last** blue high + 0.80.

The first poll after start only **syncs** the last closed candle so a stale setup does not fire immediately.

---

## Quick start

Python **3.12+** on Windows. Leave the Promax **demo** MT5 terminal open with **Algo Trading** enabled and XAUUSD M1 visible.

```bash
cd "g:\All Tools Created\Trading bot"
.\.venv\Scripts\activate
pip install -r requirements.txt
playwright install chrome
```

`.env` is already pointed at this machine’s TradingView Chrome profile and XAUUSD. Keep:

```
TRADING_MODE=DEMO
DRY_RUN=true
ALLOW_LIVE_TRADING=
EXECUTION_BACKEND=MT5
SYMBOL=XAUUSD
TIMEFRAME=1M
SL_OFFSET=0.80
TP_RR=0
MAX_POSITION_SIZE=1.0
```

If Chrome Profile 52 is not copied yet:

```bash
python main.py --copy-chrome-profile
```

Chrome must be fully closed for that copy. Then:

```bash
python main.py --run
```

You should see `MT5 connected. Balance: …` and then `Bot running (DRY RUN)`. On a new closed M1 bar that matches the pattern it prints:

```
SIGNAL: BUY
Would enter XAUUSD
Quantity: …
SL: …
TP: none
```

Ctrl+C stops. Positions are not flattened on shutdown (`CLOSE_POSITIONS_ON_CRASH=false`).

When you are ready for **demo fills only**, set `DRY_RUN=false`. Do not set `TRADING_MODE=LIVE`. A live MT5 account is rejected while mode is DEMO.

Optional status UI (does not enable live trading):

```bash
python main.py --dashboard
```

http://127.0.0.1:8000

```bash
pytest
```

---

## Safety

| Setting | Default | Meaning |
| --- | --- | --- |
| `TRADING_MODE` | `DEMO` | Demo/paper only |
| `ALLOW_LIVE_TRADING` | empty | LIVE also requires the exact phrase `YES_I_UNDERSTAND` |
| `DRY_RUN` | `true` | Logs the trade; MT5 `order_send` is not called |
| `MAX_POSITION_SIZE` | `1.0` in `.env` | Caps the 2% gold size (uncapped would be ~12 lots on ~$50k) |
| `TP_RR` | `0` | No take-profit is attached |

---

## Architecture

```
trading_bot/
  strategy/strategy.py     # SweepBreakoutStrategy — no Playwright, no MT5
  trading/position_sizer.py
  trading/mt5_terminal.py  # candles + contract + order_send
  trading/mt5_executor.py  # market, then SL/TP
  trading/engine.py        # closed-candle poll → size → dry-run or submit
  bot.py                   # optional TradingView + required MT5 loop
  browser/                 # Playwright for the chart only
```

Orders go through `TradingExecutor`. `PlaywrightExecutor` still refuses to click Buy/Sell.

---

## Chrome / TradingView

Chrome blocks remote debugging on the live User Data folder. The bot uses a **copy** of Profile 52 (“Trading View”) under `./browser_profile`. If the chart window fails to open, the MT5 loop still runs.

---

## Selectors

`--setup` / `--test-selectors` still exist for the chart page. They are not required for MT5 execution. Do not bind random toast text (a `NAME: span` binding from a cookie banner was removed).
