# Gold timeframe report

XAUUSD only. Settings were chosen on 2010-2021 data by `python -m gsms.gold_select` and then measured on 2022-2026 by `python -m gsms.gold_validate`. The choice was not changed after these out-of-sample numbers were known.

R is the amount risked on one trade. Dollar figures use $100 of risk per trade (1% of a $10,000 account) and are not compounded. Costs are 0.0175% of price per fill, the gold model in `python/gsms/backtest.py` (half the measured Dukascopy spread plus commission and slippage).

## Did any timeframe hit the targets?

The targets were a win rate above 60%, a profit factor above 1.3, a positive expectancy, a controlled drawdown and a result that survives out of sample, with at least 100 trades where the history allows it. They were not forced. A timeframe counts as meeting them only on the untouched 2022-2026 period.

0 of 8 timeframes meet the win-rate, profit-factor and out-of-sample confidence-interval conditions together.
Where a total is positive, read the confidence interval in the table below. A positive sum with an interval that includes zero is not a validated edge, and no timeframe reached a win rate above 60%.

## Out of sample, 2022-01-01 to 2026-11-07

This is the comparison that was not used to choose the settings.

| Timeframe | Status | Trades | Win rate | Profit factor | Net (R) | Max drawdown (R) | Expectancy (R) |
|---|---|---|---|---|---|---|---|
| 1m | unoptimized | 1555 | 31.1% | 0.28 | -1439.8 | 1444.4 | -0.926 |
| 5m | passed_pre_oos_checks | 77 | 28.6% | 1.32 | +20.8 | 21.5 | +0.270 |
| 15m | passed_pre_oos_checks | 103 | 26.2% | 1.27 | +22.4 | 12.0 | +0.218 |
| 30m | passed_pre_oos_checks | 138 | 26.1% | 0.92 | -8.5 | 24.5 | -0.061 |
| 1h | passed_pre_oos_checks | 144 | 18.1% | 0.81 | -24.2 | 49.0 | -0.168 |
| 2h | passed_pre_oos_checks | 94 | 27.7% | 0.70 | -21.4 | 37.5 | -0.228 |
| 4h | passed_pre_oos_checks | 166 | 35.5% | 1.01 | +1.1 | 28.7 | +0.007 |
| 1D | passed_pre_oos_checks | 68 | 19.1% | 0.92 | -4.5 | 12.9 | -0.066 |

## Original defaults against the selected settings, out of sample

| Timeframe | original trades | selected trades | original win_rate | selected win_rate | original profit_factor | selected profit_factor | original net_r | selected net_r | original max_dd_r | selected max_dd_r |
|---|---|---|---|---|---|---|---|---|---|---|
| 1m | 1555 | 1555 | 31.1% | 31.1% | 0.28 | 0.28 | -1439.8 | -1439.8 | 1444.38 | 1444.38 |
| 5m | 396 | 77 | 31.6% | 28.6% | 0.57 | 1.32 | -154.8 | +20.8 | 155.86 | 21.52 |
| 15m | 179 | 103 | 32.4% | 26.2% | 0.74 | 1.27 | -36.9 | +22.4 | 47.53 | 12.01 |
| 30m | 96 | 138 | 33.3% | 26.1% | 0.84 | 0.92 | -11.4 | -8.5 | 22.64 | 24.51 |
| 1h | 43 | 144 | 32.6% | 18.1% | 0.86 | 0.81 | -4.4 | -24.2 | 14.36 | 49.02 |
| 2h | 24 | 94 | 25.0% | 27.7% | 0.61 | 0.70 | -7.4 | -21.4 | 12.03 | 37.51 |
| 4h | 4 | 166 | 0.0% | 35.5% | 0.00 | 1.01 | -4.2 | +1.1 | 4.17 | 28.68 |
| 1D | 1 | 68 | 0.0% | 19.1% | 0.00 | 0.92 | -1.0 | -4.5 | 1.03 | 12.94 |

## 2010-2021, the period the settings were chosen on

These numbers are in-sample. They are higher than the out-of-sample numbers for that reason.


**train**

| Timeframe | Trades | Win rate | Profit factor | Net (R) | Max drawdown (R) |
|---|---|---|---|---|---|
| 1m | 1897 | 33.5% | 0.27 | -1842.2 | 1845.0 |
| 5m | 116 | 26.7% | 1.22 | +21.8 | 13.8 |
| 15m | 152 | 28.9% | 1.37 | +46.3 | 15.0 |
| 30m | 183 | 36.1% | 1.42 | +55.6 | 15.2 |
| 1h | 257 | 24.5% | 1.15 | +33.0 | 23.7 |
| 2h | 161 | 41.0% | 1.27 | +26.8 | 10.9 |
| 4h | 265 | 38.9% | 1.15 | +25.4 | 17.1 |
| 1D | 88 | 23.9% | 1.16 | +11.6 | 10.0 |

**validation**

| Timeframe | Trades | Win rate | Profit factor | Net (R) | Max drawdown (R) |
|---|---|---|---|---|---|
| 1m | 1224 | 30.1% | 0.22 | -1331.0 | 1331.5 |
| 5m | 51 | 33.3% | 1.62 | +25.0 | 8.8 |
| 15m | 104 | 26.0% | 1.17 | +15.2 | 18.5 |
| 30m | 108 | 31.5% | 1.15 | +12.4 | 15.8 |
| 1h | 125 | 27.2% | 1.32 | +32.5 | 18.2 |
| 2h | 92 | 43.5% | 1.39 | +21.7 | 10.1 |
| 4h | 136 | 42.6% | 1.34 | +28.3 | 16.4 |
| 1D | 54 | 22.2% | 1.15 | +6.3 | 15.6 |

## Long and short, out of sample

| Timeframe | Long trades | Long win rate | Long net (R) | Short trades | Short win rate | Short net (R) |
|---|---|---|---|---|---|---|
| 1m | 805 | 30.6% | -769.8 | 750 | 31.7% | -670.0 |
| 5m | 45 | 28.9% | +14.1 | 32 | 28.1% | +6.8 |
| 15m | 52 | 28.8% | +19.1 | 51 | 23.5% | +3.4 |
| 30m | 82 | 24.4% | -10.8 | 56 | 28.6% | +2.3 |
| 1h | 71 | 21.1% | +0.0 | 73 | 15.1% | -24.2 |
| 2h | 46 | 37.0% | +2.3 | 48 | 18.8% | -23.7 |
| 4h | 88 | 42.0% | +17.8 | 78 | 28.2% | -16.7 |
| 1D | 32 | 25.0% | +7.1 | 36 | 13.9% | -11.6 |

## Walk-forward of the selection process

Separately from the frozen settings above, the existing walk-forward re-picks a supply/demand strategy every year on the previous four years and trades the next year (2014-2026). It is in `results/walk_forward.csv`.

| Timeframe | Test years | Re-picked each year, total (R) | Profitable years | Median strategy, total (R) |
|---|---|---|---|---|
| 5m | 13 | -70.7 | 6 | -42.0 |
| 15m | 13 | -68.7 | 3 | -8.5 |
| 30m | 13 | -105.9 | 2 | -1.4 |
| 1h | 13 | -24.0 | 7 | -0.3 |
| 2h | 13 | -12.3 | 5 | +0.0 |
| 4h | 13 | +32.7 | 6 | +0.0 |
| 1D | 13 | -0.4 | 0 | +0.0 |

## 1-minute

The 1-minute search tested 3061 settings (the shared baseline, the EMA grid and the first 3,000 random draws of seed 11, which is the start of the 5,000-draw list used on the other timeframes). 0 of them passed the rules. The active 1-minute settings are therefore the original defaults, and the chart labels that timeframe unoptimized. A further 2,000 draws were started and then stopped: a single 1-minute backtest over the full history took long enough that finishing them would not change a result that was already 0-for-every setting with enough trades in the first sample.

## What the original 1H, 2H and 4H settings did

The original defaults are all four modules at their input defaults (EMA 9/20 cross, sweep swing 5, zone swing 10, ATR stop 1.5, target 2R, no trend filter, all sessions). On 2018-2021 the 1H default lost 9.4 R, the 2H default lost 11.3 R on 19 trades, and the 4H default won 62.5% of 16 trades in 2010-2017 and then lost all 7 trades in 2018-2021. Those defaults failed the robustness rules, so the selected settings replaced them. The 2022-2026 column was not used for that decision. It later showed the 1H and 2H replacements losing more R than the defaults did, on more trades; both sides lost, and the defaults were not put back after seeing that, because the period had been reserved.

## TradingView, Basic plan

The Auto preset was saved into the existing strategy and run in the Strategy Tester on OANDA:XAUUSD. Deep Backtesting was not used. Each timeframe only has the history the Basic plan loads, so these figures are not the 2010-2026 research results. The on-chart table showed the timeframe label (for example "30m selected") and the 2022-2026 note from this report. 30m produced the same 27 trades and +3,213.19 USD as the previously saved "Gold 30m Conservative" preset, and 1h the same 21 trades and -2,007.58 USD as "Gold 1h Aggressive", which is the check that Auto selected those settings.

| Timeframe | Loaded history | Trades | Win rate | Profit factor | Net (USD) | Max drawdown (USD) |
|---|---|---|---|---|---|---|
| 1m | Nov 8, 2026 — Nov 13, 2026 | 10 | 50.0% | 1.92 | +6,945.47 | 2,272.33 |
| 5m | Oct 18, 2026 — Nov 13, 2026 | 2 | 0.0% | 0.00 | -2,232.84 | 2,232.84 |
| 15m | Aug 2, 2026 — Nov 13, 2026 | 10 | 30.0% | 1.57 | +4,290.49 | 6,059.17 |
| 30m | Jan 1, 2026 — Nov 13, 2026 | 27 | 29.6% | 1.16 | +3,213.19 | 9,503.43 |
| 1h | Jan 1, 2026 — Nov 13, 2026 | 21 | 19.1% | 0.89 | -2,007.58 | 12,848.31 |
| 2h | Jan 1, 2025 — Nov 13, 2026 | 32 | 34.4% | 0.98 | -495.23 | 10,739.55 |
| 4h | Jan 2, 2023 — Nov 13, 2026 | 124 | 30.6% | 0.81 | -17,625.39 | 23,656.74 |
| 1D | Jan 6, 1833 — Nov 12, 2026 | 631 | 20.3% | 1.02 | +9,895.52 | 84,040.34 |

The daily row uses OANDA's long daily series (the tester range starts in 1833). That is not the Dukascopy series used above, and it is not evidence that the daily settings are profitable.

## Files

- `results/gold/timeframe_configs.json` — the choice and the pre-2022 statistics

- `results/gold/performance.csv` — every period, selected and original

- `results/gold/monthly.csv` — selected settings, net R by month

- `results/gold/trades/` — trade lists

- `results/gold/charts/XAUUSD_<timeframe>.png` — equity and drawdown; the dashed line is 2022-01-01

- `results/optimization_results.csv.gz` and `results/optimization_report.xlsx` — the full search, both metals

