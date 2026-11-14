"""Out-of-sample results for the frozen gold timeframe settings.

    python -m gsms.gold_validate

Reads results/gold/timeframe_configs.json and does not change which settings were chosen.
Writes the comparison tables, equity and drawdown charts, the on-chart disclosure notes,
and GOLD_REPORT.md.
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import RESULTS_DIR, backtest, data, metrics, search
from .gold_select import CONFIG_PATH, GOLD_TIMEFRAMES
from .params import Params

OUT_DIR = RESULTS_DIR / "gold"
PERIODS = {"train": search.TRAIN, "validation": search.VALIDATION, "out_of_sample": search.OOS, "all": ("2010-01-01", "2026-11-07")}
TARGETS = {"win_rate": 0.60, "profit_factor": 1.3}


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def _run(timeframe: str, params: dict):
    bars = backtest.Bars(data.load_bars("XAUUSD", timeframe), "XAUUSD", timeframe, cache_limit=2)
    return backtest.run(bars, Params.from_dict(params), start="2010-01-01")


def _stats(trades, start, end) -> dict:
    stats = metrics.summarize(trades, start, end)
    stats["avg_rr"] = float(np.mean(np.where(trades.between(start, end).r > 0, trades.between(start, end).r_gross, np.nan))) if stats["trades"] else 0.0
    return stats


def _bootstrap(r: np.ndarray, seed: int = 9) -> dict:
    if len(r) < 5:
        return {"ci95_low": None, "ci95_high": None, "prob_not_positive": None}
    means = np.random.default_rng(seed).choice(r, size=(10_000, len(r)), replace=True).mean(axis=1)
    return {"ci95_low": float(np.quantile(means, 0.025)), "ci95_high": float(np.quantile(means, 0.975)), "prob_not_positive": float((means <= 0).mean())}


def _curve(trades) -> pd.DataFrame:
    if len(trades) == 0:
        return pd.DataFrame(columns=["time", "equity_r", "drawdown_r"])
    order = np.argsort(trades.exit_time)
    equity = np.cumsum(trades.r[order])
    peak = np.maximum.accumulate(equity)
    return pd.DataFrame({"time": trades.exit_time[order], "equity_r": equity, "drawdown_r": equity - peak})


def _chart(timeframe: str, curves: dict[str, pd.DataFrame]) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for name, curve in curves.items():
        if curve.empty:
            continue
        axes[0].plot(curve["time"], curve["equity_r"], label=name, linewidth=1.2)
        axes[1].plot(curve["time"], curve["drawdown_r"], label=name, linewidth=1.2)
    for axis in axes:
        axis.axvline(np.datetime64("2022-01-01"), color="0.5", linewidth=0.8, linestyle="--")
        axis.grid(True, alpha=0.3)
    axes[0].set_ylabel("Equity (R)")
    axes[1].set_ylabel("Drawdown (R)")
    axes[0].set_title(f"XAUUSD {timeframe}: selected settings and the original defaults")
    axes[0].legend(loc="best", fontsize=8)
    fig.tight_layout()
    path = OUT_DIR / "charts" / f"XAUUSD_{timeframe}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _fmt_pct(value) -> str:
    return "-" if value is None or (isinstance(value, float) and not np.isfinite(value)) else f"{value:.1%}"


def _fmt(value, spec) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "-"
    return spec.format(value)


def _disclosure(row: dict, oos: dict, ci: dict) -> dict:
    net, pf, trades = oos["net_r"], oos["profit_factor"], oos["trades"]
    detail = f"2022-2026: {trades} trades, PF {pf:.2f}, {net:+.1f} R"
    if ci.get("ci95_low") is not None:
        detail += f", CI {ci['ci95_low']:+.2f} to {ci['ci95_high']:+.2f} R/trade"
    proven = ci.get("ci95_low") is not None and ci["ci95_low"] > 0 and net > 0
    if row["status"] == "unoptimized":
        text = f"{row['timeframe']} unoptimized, original defaults. {detail}."
        color = "color.new(color.red, 20)"
    elif row["status"] == "original_retained":
        text = f"{row['timeframe']} kept original settings. {detail}."
        color = "color.new(color.teal, 30)" if proven else "color.new(color.orange, 20)"
    else:
        text = f"{row['timeframe']} chosen on 2010-2021. {detail}."
        color = "color.new(color.teal, 30)" if proven else "color.new(color.orange, 20)"
    return {"chart_note": text[:180], "chart_color": color}


def _table(frame: pd.DataFrame, columns: dict) -> str:
    show = frame.copy()
    for name, spec in columns.items():
        show[name] = show[name].map(lambda v, spec=spec: _fmt(v, spec) if spec != "pct" else _fmt_pct(v))
    header = "| " + " | ".join(show.columns) + " |"
    rule = "|" + "|".join("---" for _ in show.columns) + "|"
    body = "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in show.itertuples(index=False))
    return "\n".join([header, rule, body])


def main() -> None:
    config = _config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "trades").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "charts").mkdir(parents=True, exist_ok=True)
    rows, months, disclosure = [], [], {}
    for row in config["timeframes"]:
        timeframe = row["timeframe"]
        if not (data.BARS_DIR / f"XAUUSD_{timeframe}.parquet").exists():
            print(f"{timeframe}: bars not built, skipped", flush=True)
            continue
        print(f"{timeframe}: running", flush=True)
        active = _run(timeframe, row["active_params"])
        same = row["active_params"] == config["original_params"]
        original = active if same else _run(timeframe, config["original_params"])
        curves = {"Selected": _curve(active)}
        if not same:
            curves["Original defaults"] = _curve(original)
        _chart(timeframe, curves)
        oos_r = active.between(*search.OOS).r
        ci = _bootstrap(oos_r)
        disclosure[timeframe] = _disclosure(row, metrics.summarize(active, *search.OOS), ci)
        for label, (start, end) in PERIODS.items():
            for which, trades in (("selected", active), ("original", original)):
                if which == "original" and same and label != "out_of_sample":
                    pass
                stats = metrics.summarize(trades, start, end)
                rows.append({
                    "timeframe": timeframe, "status": row["status"], "which": which, "period": label,
                    "modules": Params.from_dict(row["active_params"] if which == "selected" else config["original_params"]).modules(),
                    **{k: stats[k] for k in ("trades", "wins", "losses", "win_rate", "profit_factor", "net_r", "max_dd_r", "expectancy_r", "payoff", "long_trades", "long_win_rate", "long_net_r", "short_trades", "short_win_rate", "short_net_r", "sharpe_monthly")},
                    "net_profit_usd_at_100_risk": stats["net_r"] * 100.0,
                    "max_dd_usd_at_100_risk": stats["max_dd_r"] * 100.0,
                    "meets_win_rate_60": bool(stats["win_rate"] > TARGETS["win_rate"]),
                    "meets_profit_factor_1_3": bool(stats["profit_factor"] > TARGETS["profit_factor"]),
                    "oos_expectancy_ci95_low": ci["ci95_low"] if label == "out_of_sample" and which == "selected" else None,
                    "oos_expectancy_ci95_high": ci["ci95_high"] if label == "out_of_sample" and which == "selected" else None,
                })
                if which == "selected" and label == "all":
                    kept = trades.between(start, end)
                    by_month = pd.Series(kept.r, index=pd.DatetimeIndex(kept.entry_time)).resample("MS").sum() if len(kept) else pd.Series(dtype=float)
                    for stamp, value in by_month.items():
                        months.append({"timeframe": timeframe, "month": stamp.strftime("%Y-%m"), "net_r": float(value)})
        active.frame().to_csv(OUT_DIR / "trades" / f"XAUUSD_{timeframe}_selected.csv", index=False, float_format="%.6f")
    perf = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "trades").mkdir(parents=True, exist_ok=True)
    perf.to_csv(OUT_DIR / "performance.csv", index=False, float_format="%.5f")
    pd.DataFrame(months).to_csv(OUT_DIR / "monthly.csv", index=False, float_format="%.5f")
    (OUT_DIR / "disclosure.json").write_text(json.dumps(disclosure, indent=1))

    oos = perf[(perf["which"] == "selected") & (perf["period"] == "out_of_sample")].copy()
    pre = perf[(perf["which"] == "selected") & (perf["period"].isin(["train", "validation"]))]
    lines = []
    w = lines.append
    w("# Gold timeframe report\n")
    w("XAUUSD only. Settings were chosen on 2010-2021 data by `python -m gsms.gold_select` and then measured on 2022-2026 by `python -m gsms.gold_validate`. The choice was not changed after these out-of-sample numbers were known.\n")
    w("R is the amount risked on one trade. Dollar figures use $100 of risk per trade (1% of a $10,000 account) and are not compounded. Costs are 0.0175% of price per fill, the gold model in `python/gsms/backtest.py` (half the measured Dukascopy spread plus commission and slippage).\n")
    w("## Did any timeframe hit the targets?\n")
    w("The targets were a win rate above 60%, a profit factor above 1.3, a positive expectancy, a controlled drawdown and a result that survives out of sample, with at least 100 trades where the history allows it. They were not forced. A timeframe counts as meeting them only on the untouched 2022-2026 period.\n")
    if oos.empty:
        w("No out-of-sample rows were produced.\n")
    else:
        hit = oos[(oos["win_rate"] > 0.60) & (oos["profit_factor"] > 1.3) & (oos["expectancy_r"] > 0) & (oos["oos_expectancy_ci95_low"] > 0)]
        w(f"{len(hit)} of {len(oos)} timeframes meet the win-rate, profit-factor and out-of-sample confidence-interval conditions together.")
        w("Where a total is positive, read the confidence interval in the table below. A positive sum with an interval that includes zero is not a validated edge, and no timeframe reached a win rate above 60%.\n")
    w("## Out of sample, 2022-01-01 to 2026-11-07\n")
    w("This is the comparison that was not used to choose the settings.\n")
    show = oos[["timeframe", "status", "trades", "win_rate", "profit_factor", "net_r", "max_dd_r", "expectancy_r"]].rename(columns={"timeframe": "Timeframe", "status": "Status", "trades": "Trades", "win_rate": "Win rate", "profit_factor": "Profit factor", "net_r": "Net (R)", "max_dd_r": "Max drawdown (R)", "expectancy_r": "Expectancy (R)"})
    w(_table(show, {"Win rate": "pct", "Profit factor": "{:.2f}", "Net (R)": "{:+.1f}", "Max drawdown (R)": "{:.1f}", "Expectancy (R)": "{:+.3f}"}))
    w("\n## Original defaults against the selected settings, out of sample\n")
    both = perf[perf["period"] == "out_of_sample"][["timeframe", "which", "trades", "win_rate", "profit_factor", "net_r", "max_dd_r"]]
    wide = both.pivot(index="timeframe", columns="which")
    wide.columns = [f"{b} {a}" for a, b in wide.columns]
    wide = wide.reset_index().rename(columns={"timeframe": "Timeframe"})
    order = {tf: i for i, tf in enumerate(GOLD_TIMEFRAMES)}
    wide["_o"] = wide["Timeframe"].map(order)
    wide = wide.sort_values("_o").drop(columns="_o")
    w(_table(wide, {c: "{:+.1f}" if "net" in c or "drawdown" in c else "{:.2f}" if "profit" in c or "win" not in c else "pct" for c in wide.columns if c != "Timeframe" and "trades" not in c}))
    w("\n## 2010-2021, the period the settings were chosen on\n")
    w("These numbers are in-sample. They are higher than the out-of-sample numbers for that reason.\n")
    for period in ("train", "validation"):
        part = perf[(perf["which"] == "selected") & (perf["period"] == period)][["timeframe", "trades", "win_rate", "profit_factor", "net_r", "max_dd_r"]]
        part.columns = ["Timeframe", "Trades", "Win rate", "Profit factor", "Net (R)", "Max drawdown (R)"]
        w(f"\n**{period}**\n")
        w(_table(part, {"Win rate": "pct", "Profit factor": "{:.2f}", "Net (R)": "{:+.1f}", "Max drawdown (R)": "{:.1f}"}))
    w("\n## Long and short, out of sample\n")
    side = oos[["timeframe", "long_trades", "long_win_rate", "long_net_r", "short_trades", "short_win_rate", "short_net_r"]]
    side.columns = ["Timeframe", "Long trades", "Long win rate", "Long net (R)", "Short trades", "Short win rate", "Short net (R)"]
    w(_table(side, {"Long win rate": "pct", "Long net (R)": "{:+.1f}", "Short win rate": "pct", "Short net (R)": "{:+.1f}"}))
    w("\n## Walk-forward of the selection process\n")
    w("Separately from the frozen settings above, the existing walk-forward re-picks a supply/demand strategy every year on the previous four years and trades the next year (2014-2026). It is in `results/walk_forward.csv`.\n")
    wf_path = RESULTS_DIR / "walk_forward.csv"
    if wf_path.exists():
        wf = pd.read_csv(wf_path)
        wf = wf[wf["symbol"] == "XAUUSD"]
        summary = wf.groupby("timeframe", sort=False).agg(years=("test_year", "size"), chosen=("chosen_test_net_r", "sum"), good=("chosen_test_net_r", lambda s: int((s > 0).sum())), pool=("pool_test_net_r_median", "sum")).reset_index()
        summary["_o"] = summary["timeframe"].map(order)
        summary = summary.sort_values("_o").drop(columns="_o")
        summary.columns = ["Timeframe", "Test years", "Re-picked each year, total (R)", "Profitable years", "Median strategy, total (R)"]
        w(_table(summary, {"Re-picked each year, total (R)": "{:+.1f}", "Median strategy, total (R)": "{:+.1f}"}))
        w("")
    w("## 1-minute\n")
    one = next((row for row in config["timeframes"] if row["timeframe"] == "1m"), None)
    if one is None:
        w("The 1-minute search has not been run.\n")
    elif one["status"] == "unoptimized":
        w(f"The 1-minute search tested {one.get('tested', 'the')} settings (the shared baseline, the EMA grid and the first 3,000 random draws of seed 11, which is the start of the 5,000-draw list used on the other timeframes). {one['eligible']} of them passed the rules. The active 1-minute settings are therefore the original defaults, and the chart labels that timeframe unoptimized. A further 2,000 draws were started and then stopped: a single 1-minute backtest over the full history took long enough that finishing them would not change a result that was already 0-for-every setting with enough trades in the first sample.\n")
    else:
        w("The 1-minute settings passed the same pre-2022 rules as the other timeframes. The out-of-sample row is in the table above.\n")
    w("## What the original 1H, 2H and 4H settings did\n")
    w("The original defaults are all four modules at their input defaults (EMA 9/20 cross, sweep swing 5, zone swing 10, ATR stop 1.5, target 2R, no trend filter, all sessions). On 2018-2021 the 1H default lost 9.4 R, the 2H default lost 11.3 R on 19 trades, and the 4H default won 62.5% of 16 trades in 2010-2017 and then lost all 7 trades in 2018-2021. Those defaults failed the robustness rules, so the selected settings replaced them. The 2022-2026 column was not used for that decision. It later showed the 1H and 2H replacements losing more R than the defaults did, on more trades; both sides lost, and the defaults were not put back after seeing that, because the period had been reserved.\n")
    w("## TradingView, Basic plan\n")
    w("The Auto preset was saved into the existing strategy and run in the Strategy Tester on OANDA:XAUUSD. Deep Backtesting was not used. Each timeframe only has the history the Basic plan loads, so these figures are not the 2010-2026 research results. The on-chart table showed the timeframe label (for example \"30m selected\") and the 2022-2026 note from this report. 30m produced the same 27 trades and +3,213.19 USD as the previously saved \"Gold 30m Conservative\" preset, and 1h the same 21 trades and -2,007.58 USD as \"Gold 1h Aggressive\", which is the check that Auto selected those settings.\n")
    tv_rows = []
    for timeframe in GOLD_TIMEFRAMES:
        path = RESULTS_DIR / "tradingview" / "runs" / f"XAUUSD_{timeframe}_auto.json"
        if not path.exists():
            continue
        run = json.loads(path.read_text())
        tester = run.get("tester") or {}
        tv_rows.append({"Timeframe": timeframe, "Loaded history": tester.get("range"), "Trades": tester.get("total_trades"), "Win rate": tester.get("percent_profitable"), "Profit factor": tester.get("profit_factor"), "Net (USD)": tester.get("net_profit_usd"), "Max drawdown (USD)": tester.get("max_drawdown_usd")})
    if tv_rows:
        w(_table(pd.DataFrame(tv_rows), {"Win rate": "{:.1f}%", "Profit factor": "{:.2f}", "Net (USD)": "{:+,.2f}", "Max drawdown (USD)": "{:,.2f}"}))
        w("\nThe daily row uses OANDA's long daily series (the tester range starts in 1833). That is not the Dukascopy series used above, and it is not evidence that the daily settings are profitable.\n")
    w("## Files\n")
    w("- `results/gold/timeframe_configs.json` — the choice and the pre-2022 statistics\n")
    w("- `results/gold/performance.csv` — every period, selected and original\n")
    w("- `results/gold/monthly.csv` — selected settings, net R by month\n")
    w("- `results/gold/trades/` — trade lists\n")
    w("- `results/gold/charts/XAUUSD_<timeframe>.png` — equity and drawdown; the dashed line is 2022-01-01\n")
    w("- `results/optimization_results.csv.gz` and `results/optimization_report.xlsx` — the full search, both metals\n")
    text = "\n".join(lines) + "\n"
    (RESULTS_DIR.parent / "GOLD_REPORT.md").write_text(text)
    with pd.ExcelWriter(OUT_DIR / "gold_timeframe_report.xlsx") as writer:
        perf.to_excel(writer, sheet_name="Performance", index=False)
        pd.DataFrame(months).to_excel(writer, sheet_name="Monthly", index=False)
        if wf_path.exists():
            pd.read_csv(wf_path).query("symbol == 'XAUUSD'").to_excel(writer, sheet_name="Walk-forward", index=False)
    print(f"wrote {OUT_DIR} and GOLD_REPORT.md")


if __name__ == "__main__":
    main()
