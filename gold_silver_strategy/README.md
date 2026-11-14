# GSMS - Gold & Silver Multi-Strategy

A TradingView indicator and companion strategy (Pine Script v6) for XAUUSD and XAGUSD that
combine four modules, each with its own on/off switch:

1. EMA crossover (or EMA trend filter)
2. Liquidity sweeps (swing highs/lows, previous day and previous week high/low)
3. Supply and demand zones
4. Candlestick patterns (engulfing, pin bar, morning/evening star, piercing line/dark cloud)

plus a Python research framework that back-tests the same rules on 17 years of 1-minute data.

**Read this first.** The research found no statistically validated edge. Parameter sets that
looked good on 2010-2021 data mostly lost money on 2022-2026 data, and the six presets shipped
with the scripts are no exception (5 of 6 lost out of sample). The scripts are correct and
tested; the trading idea, as specified, does not beat its costs. Details: [REPORT.md](REPORT.md).

| File | What it is |
|---|---|
| `pine/GSMS_Indicator.pine` | The indicator: zones, sweeps, patterns, signals, stop/target, dashboard, alerts |
| `pine/GSMS_Strategy.pine` | The same logic with orders, for TradingView's Strategy Tester |
| `SPEC.md` | The trading rules in plain language |
| `REPORT.md` | Research results: timeframes, modules, presets, out-of-sample, walk-forward, TradingView runs |
| `PROJECT_PROGRESS.md` | What was done, stage by stage, and what could not be done |
| `results/` | Every table behind the report (CSV, Excel, TradingView screenshots and trade lists) |
| `python/gsms/` | Data pipeline, back-test engine, optimisation, validation, report generation |
| `tools/pinets/` | Runs the Pine scripts locally (PineTS) for the trade-by-trade parity check |
| `tools/tv/` | Drives the logged-in TradingView tab to run the Strategy Tester test matrix |

## Using the scripts in TradingView

1. Open a chart of XAUUSD or XAGUSD (the research used spot quotes; OANDA's symbols were used for testing).
2. Open the Pine Editor, create a new script, paste the whole content of `pine/GSMS_Indicator.pine`
   (or `pine/GSMS_Strategy.pine`), then **Save** and **Add to chart**.
   A free (Basic) TradingView account can keep one saved script, so save one of the two and add
   the other without saving, or replace the saved one when switching.
3. Open the script's settings.

### Settings

| Group | What it controls |
|---|---|
| Preset | "Custom" uses the inputs below. A named preset replaces groups 1-8 with a researched configuration and warns on the chart when the symbol or timeframe is not the one it was tested on. |
| 1 - Strategy modules | The four on/off switches and the confluence window. Every enabled module must agree in direction within the window, and at least one must signal on the current bar. One module alone = that module's signals. |
| 2 - EMA | Fast and slow length; role "Cross" (a crossover is a signal) or "Trend" (direction filter only). |
| 3 - Liquidity sweep | Swing length, which levels count (swings, PDH/PDL, PWH/PWL), wick or close confirmation. |
| 4 - Supply / demand zones | Swing length, zone height and minimum departure in ATR, how many touches are traded, invalidation rule, touch or rejection-close entry, zone lifetime. |
| 5 - Candlestick patterns | Which patterns, strict or relaxed rules, optional confirmation candle. |
| 6 - Trade management | Stop method (ATR, structure, zone), stop limits, risk:reward target, cash risked per trade. |
| 7 - Filters | Long/short/both, trend EMA, session hours (UTC), date window. |
| 8 - Advanced | ATR length, warm-up bars. |
| 9 - Display | What is drawn; the indicator's dashboard and its trading-cost assumption. |

### What you see

- Supply zones (red) and demand zones (teal) as boxes; grey once used up or invalidated.
- BSL / SSL markers where buy-side or sell-side liquidity was swept.
- LONG / SHORT triangles on the signal bar. The entry is the next bar's open.
- Stop and target lines while a trade is open.
- Indicator dashboard (top right): preset, state of each module, current position, and the
  result of all signals on the loaded chart history after costs: trades, win rate, net R,
  profit factor, maximum drawdown. R is the amount risked per trade.
- Strategy results table (bottom right): tested span, trades, win rate, profit factor, net profit, net R, drawdown.

Signals never repaint: they are evaluated on closed bars only, swing points are used only once
confirmed, and previous day/week levels come from completed sessions.

### Alerts (indicator)

Create an alert on the indicator and choose one of:
- "GSMS long signal", "GSMS short signal", "GSMS trade closed", or
- "Any alert() function call" for one message per signal that includes the stop and target prices.

Use "Once per bar close".

### Strategy Tester

- Position size is `risk per trade / stop distance`; the default risks 1,000 on a 100,000 account (1 %).
- Commission is 0.0175 % per side, the gold figure. **On silver set Properties > Commission to 0.08 %.**
  The results table shows which value the research used.
- A free TradingView account loads roughly 5,000 bars per timeframe, so intraday tests cover
  weeks to months only. The long-history results come from the Python engine.

## Automatic gold settings

The first preset, **Auto (by timeframe)**, is the default. On an XAUUSD (or "GOLD") chart it loads the settings chosen for that timeframe. On any other symbol it keeps the manual inputs and says so on the chart. **Custom (inputs below)** is unchanged: every module switch still works.

The choice for each timeframe is in `results/gold/timeframe_configs.json` and the out-of-sample numbers are in [GOLD_REPORT.md](GOLD_REPORT.md). The banner under the chart states the 2022-2026 result. 1-minute is marked unoptimized: no tested setting passed the 2010-2021 rules, so Auto keeps the original defaults there. None of the eight timeframes has a statistically validated edge, and none reached a win rate above 60%. 1H, 2H and 4H kept the original defaults only if a candidate failed to beat them on 2010-2021 robustness; the candidates passed, so they replaced the defaults. See GOLD_REPORT.md for the comparison, including the cases where the replacement then lost more out of sample than the defaults.

`results/gold/charts/` has the equity and drawdown chart for each finished timeframe.

## Presets

| Preset | Modules | Notes |
|---|---|---|
| Gold 30m Conservative | EMA + supply/demand | Asian session, 3R target |
| Gold 2h Balanced | EMA + supply/demand | London/New York hours, 2R target |
| Gold 1h Aggressive | Liquidity sweep + supply/demand + candles | 4R target, low win rate |
| Silver 2h Conservative | EMA trend + supply/demand | 4R target |
| Silver 2h Balanced | Liquidity sweep + supply/demand | The only preset that was profitable out of sample; not statistically significant |
| Silver 30m Aggressive | EMA + liquidity sweep + supply/demand | Lost most out of sample |

Exact values: `results/final_configs.json`. Performance, including the losing out-of-sample
years: REPORT.md sections 6-8.

## Reproducing the research

```bash
cd python
pip install -r requirements.txt
python -m gsms.data            # download 1-minute history, build 5m-1D bars (about 400 MB)
python -m gsms.data_checks     # data-quality report and measured spreads
python -m pytest -q            # engine tests
python -m gsms.search          # 5,061 parameter sets x 14 datasets, train + validation only
python -m gsms.select          # timeframe/module comparison, pick the presets
python -m gsms.validate        # out-of-sample, walk-forward, cost sensitivity, regimes
python -m gsms.pine_build      # write the presets into both Pine scripts
python -m gsms.parity          # Pine (PineTS) vs Python, trade by trade; needs `npm ci` in tools/pinets
python -m gsms.tv_report       # collect the TradingView runs and cross-check them
python -m gsms.report          # REPORT.md and results/optimization_report.xlsx
```

The Pine scripts are generated: edit `pine/src/gsms.template.pine`, then run `python -m gsms.pine_build`.
