# GSMS project progress

Gold & Silver Multi-Strategy for XAUUSD and XAGUSD: EMA, liquidity sweeps, supply/demand zones
and candlestick patterns. One Pine v6 template builds the indicator and the companion strategy.
The Python engine implements the same rules. Figures below come from files in `results/`.

## Preserved work

The indicator and strategy were not rewritten. A copy of the scripts from before the gold
timeframe switch was added is in `backups/pre-gold-auto-2026-11-14/`. Module switches, zone
drawing, signals, stop and target lines, the dashboard, alerts and the named presets are still
there. The new preset is **Auto (by timeframe)**, which is the default and only changes
behaviour on XAUUSD.

## Stage 1–2 — Inspection and the research framework (done before this pass)

Pine v6 indicator and strategy generated from `pine/src/gsms.template.pine`. Python engine,
Dukascopy/HistData 1-minute history from 2009-03-15 to 2026-11-06, and bars from 5m to 1D.
Parameter search: 5,061 settings (after de-duplication the saved files hold the full list) on
each of 14 symbol/timeframe datasets, signals stopped at 2022-01-01 so the choice could not
see that period.

## Stage 3 — Signal logic matches (done)

- PineTS, strategy: 126/126 runs identical to Python, 1,289 trades.
- PineTS, indicator ledger: 56/56 runs, 480 trades.
- Shipped presets: 6/6 strategy and 6/6 indicator.
- Engine tests in `python/tests/test_engine.py`, including a no-look-ahead prefix test.
- The gold choice function is covered by `python/tests/test_gold_select.py`.

PineTS is not TradingView. It shows the Pine source and the Python engine implement the same rules.

## Stage 4 — TradingView (done)

The account is TradingView Basic. Deep Backtesting was not used. The Strategy Tester runs on
the history that plan loads.

An earlier matrix recorded the previous script on both metals (`results/tradingview/runs/`,
the files that are not named `auto`). The Auto preset was then saved into that same script
and run on XAUUSD at all eight intervals. On 30m the tester matched the earlier Gold 30m
Conservative run (27 trades, +3,213.19 USD). On 1h it matched Gold 1h Aggressive (21 trades,
-2,007.58 USD). The chart table read "30m selected" and showed the 2022-2026 note. The daily
OANDA series starts in 1833 and is not the research data. Numbers: `GOLD_REPORT.md`.

## Stage 5–7 — Search, choice, out of sample, walk-forward (done for 5m–1D gold)

Rules, fixed before the 2022–2026 results were read (`python/gsms/gold_select.py`):

- Supply/demand on, both directions.
- At least 80 trades in 2010–2017 and 40 in 2018–2021.
- Profitable after costs in both of those periods.
- At least 70% of one-step neighbours profitable before 2022.
- Ranked by the worse of the train and validation monthly Sharpe ratios, not by win rate.
- On 1h, 2h and 4h the original defaults stay unless a candidate passes those rules and
  scores higher. All three originals failed 2018–2021 (the 4h default's 62.5% win rate was
  16 trades, then 0 wins out of 7). The candidates replaced them. 2022–2026 was not used
  to put the defaults back.

Optuna was not used. The search is a fixed grid plus random draws with seed 11 (5,000 draws on 5m-1D, 3,000 on 1m).
A sampler that chases in-sample winners would fit noise: before costs, train and validation
ranks on gold 30m–4h are essentially uncorrelated.

Chosen settings and pre-2022 stats: `results/gold/timeframe_configs.json`.
Out-of-sample stats, monthly R, trade lists, equity and drawdown charts: `results/gold/`.
Narrative: `GOLD_REPORT.md`.

Out of sample (2022-01-01 to 2026-11-07), selected settings, costs included:

| Timeframe | Trades | Win rate | Profit factor | Net (R) | Max DD (R) | 95% CI of expectancy |
|---|---|---|---|---|---|---|
| 5m | 77 | 28.6% | 1.32 | +20.8 | 21.5 | −0.22 to +0.80 |
| 15m | 103 | 26.2% | 1.27 | +22.4 | 12.0 | −0.20 to +0.66 |
| 30m | 138 | 26.1% | 0.92 | −8.5 | 24.5 | −0.35 to +0.23 |
| 1h | 144 | 18.1% | 0.81 | −24.2 | 49.0 | −0.48 to +0.16 |
| 2h | 94 | 27.7% | 0.70 | −21.4 | 37.5 | −0.49 to +0.06 |
| 4h | 166 | 35.5% | 1.01 | +1.1 | 28.7 | −0.21 to +0.23 |
| 1m | 1555 | 31.1% | 0.28 | −1439.8 | 1444.4 | unoptimized, original defaults |
| 1D | 68 | 19.1% | 0.92 | −4.5 | 12.9 | −0.50 to +0.43 |

No timeframe met win rate above 60% and profit factor above 1.3 with an out-of-sample
interval above zero. 5m (+20.8 R, 77 trades) and 15m (+22.4 R, 103 trades) are the only
clearly positive totals, and both confidence intervals include zero. Walk-forward (re-pick
every year on the previous four years) lost money on every gold timeframe except 4h
(+32.7 R over 13 test years, median strategy in the pool about 0). That 4h path is the
selection process, not the frozen 4h settings, which were about flat out of sample (+1.1 R).

1-minute (`results/search/XAUUSD_1m.parquet`, 3,061 settings: baselines, EMA grid and the
first 3,000 random draws of the same seed): 614 had enough trades and none of those were
profitable in both 2010-2017 and 2018-2021. Median train result about -432 R. Status
**unoptimized**. Auto uses the original defaults and the chart says so. Out of sample those
defaults lost 1,439.8 R over 1,555 trades (profit factor 0.28). The other 2,000 random draws
were started and stopped; one full-history 1-minute backtest was slow enough that finishing
them would not change a sample that was already 0-for-614.

## Targets that were not met

Win rate above 60%, profit factor above 1.3, positive expectancy with a confidence interval
above zero, and a walk-forward that makes money. Forcing the win rate would have meant
fitting the 16-trade 4h default or a long-only filter that is mostly gold's upward drift.
Neither was done.

## Data

Source and cleaning: `results/data_quality.md`. Dukascopy BID 1-minute quotes, HistData used
where it matched and replaced where it was stale. No prices were filled in. Missing minutes
are absent rather than invented; higher timeframes are aggregated from the minutes that
exist, aligned to the 18:00 New York session. Gold cost is 0.0175% per fill (half the
measured spread plus commission and slippage). Silver, where the older study used it, is 0.08%.

## Status

Gold Auto switching is in the existing indicator and strategy. Eight timeframes have a
documented configuration: seven chosen on 2010-2021 data, and 1m explicitly unoptimized.
None has a statistically validated edge. Details and the TradingView table are in
`GOLD_REPORT.md`.
