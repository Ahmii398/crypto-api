# GSMS project progress

Gold & Silver Multi-Strategy indicator (XAUUSD / XAGUSD): EMA crossover, liquidity sweeps,
supply/demand zones and candlestick patterns, with a companion strategy, a Python research
framework and validation.

This file is updated at the end of every stage. Figures quoted here come from files in
`results/`; nothing is estimated.

## Stage 1 - Inspection of the existing project (done)

State found when work resumed (nothing had been committed yet; it is committed now):

| Item | Status | Where |
|---|---|---|
| Pine Script version | v6 | `pine/GSMS_Indicator.pine`, `pine/GSMS_Strategy.pine` |
| Indicator | written, 799 lines | generated from `pine/src/gsms.template.pine` |
| Companion strategy | written, 702 lines | same template, so identical signal logic |
| Dashboard | implemented (indicator, top right) | modules state, position, trades, win %, net R, PF, max DD |
| Individual strategy ON/OFF | implemented | input group "1 - Strategy modules" |
| Supply/demand zones | implemented, drawn as boxes | module 3 |
| BUY/SELL signals | implemented, bar-close only, entry next bar open | `goLong` / `goShort`, alerts |
| TP/SL tracking and statistics | implemented | virtual broker in the indicator, real orders in the strategy |
| Python backtest engine | implemented | `python/gsms/engine.py`, `backtest.py`, `metrics.py` |
| Data | 1-minute history 2009-2026 for both metals, 5m-1D bars | `python/gsms/data.py`, `results/data_quality.md` |
| Optimisation scripts | parameter space existed, search not yet written | `python/gsms/space.py` |
| TradingView test | not possible at that time (no logged-in session) | - |

Where the previous run stopped: the Pine scripts had been checked against the Python engine
in PineTS (an open-source Pine runtime), and the next step, the parameter search, had not
been started. No errors or incomplete functions were found in the existing code.

## Stage 2 - Missing development (done)

- Parameter search written (`python/gsms/search.py`).
- Selection, validation and report scripts: see stages 5-9.

## Stage 3 - Error fixing and signal-logic validation

Done so far:

- PineTS parity (`python -m gsms.parity`): the generated Pine source is executed bar by bar
  and compared with the Python engine on the same bars, for random settings covering all 15
  module combinations and all 7 timeframes.
  - Strategy: 126 of 126 runs identical, 1,289 trades (entry bar, exit bar, prices, size).
  - Indicator (its own TP/SL ledger behind the dashboard): 56 of 56 runs identical, 480 trades.
  - PineTS is not TradingView. It shows that the Pine code and the Python engine implement
    the same rules; the TradingView compile and Strategy Tester check is stage 4.
