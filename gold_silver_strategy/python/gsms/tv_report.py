"""Collect the TradingView Strategy Tester runs and cross-check them against the Python engine.

    python -m gsms.tv_report

Input:  results/tradingview/runs/*.json   written by tools/tv/run_matrix.mjs (what TradingView showed)
Output: results/tradingview/strategy_tester.csv   one row per run, TradingView's figures + the cross-check
        results/tradingview/trades/*.csv          TradingView's list of trades per run
        results/tradingview/NOTES.md              the TradingView section of REPORT.md
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import RESULTS_DIR, SYMBOLS, TIMEFRAMES, tv_compare
from .params import Params

TV_DIR = RESULTS_DIR / "tradingview"
RISK_CASH = 1000.0  # "Risk per trade" input of the strategy: 1 R = 1,000 USD in the Strategy Tester
MODULE_FLAGS = {"EMA crossover": "use_ema", "Liquidity sweep": "use_ls", "Supply / demand zones": "use_sd", "Candlestick patterns": "use_cp"}
CONFIG_LABEL = {
    "default_all_modules": "Default inputs, all four modules", "ema_only": "EMA module only", "liquidity_sweep_only": "Liquidity sweep only",
    "supply_demand_only": "Supply/demand only", "candles_only": "Candlestick patterns only",
}  # fmt: skip


def _params(run: dict, presets: dict) -> dict:
    preset = run["applied"]["preset"]
    if preset in presets:
        return presets[preset]["params"]
    flags = {MODULE_FLAGS[label]: on for label, on in run["applied"]["modules"].items()}
    return Params().replace(**flags).to_dict()


def _label(run: dict) -> str:
    return CONFIG_LABEL.get(run["config"], "Preset: " + run["applied"]["preset"])


def collect() -> pd.DataFrame:
    presets = {p["name"]: p for p in json.loads((RESULTS_DIR / "final_configs.json").read_text())["presets"]}
    (TV_DIR / "trades").mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted((TV_DIR / "runs").glob("*.json")):
        run = json.loads(path.read_text())
        tester = run["tester"]
        start, end = [pd.to_datetime(part.strip(), format="%b %d, %Y") for part in tester["range"].split("—")]
        trades = tv_compare.tv_trades(path)
        trades.drop(columns=["direction"]).to_csv(TV_DIR / "trades" / f"{path.stem}.csv", index=False)
        py, data_end, _ = tv_compare.python_trades(run["symbol"], run["timeframe"], _params(run, presets), str(start.date()), run["applied"]["commission"])
        check = tv_compare.compare(trades, py, data_end)
        total = tester["total_trades"] or 0
        rows.append({
            "symbol": run["symbol"], "timeframe": run["timeframe"], "configuration": _label(run), "config_key": run["config"],
            "tradingview_symbol": run["tradingview_symbol"], "tested_from": start.date(), "tested_to": end.date(),
            "commission_pct_per_side": run["applied"]["commission"],
            "total_trades": total, "percent_profitable": tester["percent_profitable"], "profit_factor": tester["profit_factor"],
            "net_profit_usd": tester["net_profit_usd"] or 0.0, "net_profit_pct": tester["net_profit_pct"] or 0.0,
            "net_result_r": (tester["net_profit_usd"] or 0.0) / RISK_CASH,
            "max_drawdown_usd": tester["max_drawdown_usd"], "max_drawdown_r": (tester["max_drawdown_usd"] or 0.0) / RISK_CASH,
            "gross_profit_usd": tester["gross_profit_usd"], "gross_loss_usd": tester["gross_loss_usd"],
            "long_pnl_usd": tester["long_pnl_usd"], "short_pnl_usd": tester["short_pnl_usd"],
            "expectancy_usd": tester["expectancy_usd"], "trades_in_list": len(trades),
            "python_data_ends": data_end, **{f"check_{k}": v for k, v in check.items()},
            "screenshot": f"screenshots/{path.stem}.jpg", "recorded_utc": run["recorded_utc"],
        })  # fmt: skip
    frame = pd.DataFrame(rows)
    frame["timeframe"] = pd.Categorical(frame["timeframe"], TIMEFRAMES, ordered=True)
    frame["symbol"] = pd.Categorical(frame["symbol"], SYMBOLS, ordered=True)
    order = {key: i for i, key in enumerate(CONFIG_LABEL)}
    frame["_order"] = frame["config_key"].map(order).fillna(len(order))
    frame = frame.sort_values(["symbol", "timeframe", "_order", "configuration"]).drop(columns="_order").reset_index(drop=True)
    return frame


def _table(df: pd.DataFrame, formats: dict) -> str:
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "|".join("---" for _ in df.columns) + "|"]
    for _, row in df.iterrows():
        cells = []
        for column in df.columns:
            value = row[column]
            if value is None or (isinstance(value, (float, np.floating)) and not np.isfinite(value)):
                cells.append("-")
            else:
                cells.append(formats[column].format(value) if column in formats else str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def notes(frame: pd.DataFrame) -> str:
    account = (TV_DIR / "account_limits.md").read_text().strip() if (TV_DIR / "account_limits.md").exists() else ""
    out = [account, ""] if account else []
    w = out.append
    w(f"### Strategy Tester results ({len(frame)} runs)\n")
    w("Figures exactly as shown by TradingView's Strategy Tester (OANDA feed, chart time zone UTC, 100,000 USD account, 1,000 USD risked per trade so 1 R = 1,000 USD, commission as listed, no slippage). Every row has a screenshot in `results/tradingview/screenshots/` and its list of trades in `results/tradingview/trades/`. The tested span is whatever history the account's plan loads for that timeframe.\n")
    for symbol in SYMBOLS:
        part = frame[frame["symbol"] == symbol]
        if part.empty:
            continue
        show = part[["timeframe", "configuration", "tested_from", "tested_to", "total_trades", "percent_profitable", "profit_factor", "net_profit_usd", "net_result_r", "max_drawdown_r"]].copy()
        show.columns = ["Timeframe", "Configuration", "From", "To", "Trades", "Win %", "Profit factor", "Net profit (USD)", "Net (R)", "Max DD (R)"]
        w(f"**{symbol}** (commission {part['commission_pct_per_side'].iloc[0]:g} % per side)\n")
        w(_table(show, {"Win %": "{:.1f}", "Profit factor": "{:.2f}", "Net profit (USD)": "{:+,.0f}", "Net (R)": "{:+.1f}", "Max DD (R)": "{:.1f}"}))
        w("")

    w("### What the TradingView runs show\n")
    traded = frame[frame["total_trades"] > 0]
    profitable = traded[traded["net_profit_usd"] > 0]
    w(f"- {len(profitable)} of {len(traded)} runs with at least one trade ended with a profit. The spans are short (the 5m history is under a month, the 1h history under a year) and most runs have few trades, so these figures say little on their own; they are consistent with the long-history research, not a substitute for it.")
    presets = frame[frame["config_key"].str.startswith("preset_")]
    own = presets[presets.apply(lambda r: f" {r['timeframe']} " in r["configuration"], axis=1)]
    if len(own):
        text = "; ".join(f"{r['configuration'].replace('Preset: ', '')}: {int(r['total_trades'])} trades, {r['net_result_r']:+.1f} R" for _, r in own.iterrows())
        w(f"- Presets on the timeframe they were selected for: {text}.")
    best = traded[traded["total_trades"] >= 20].sort_values("profit_factor", ascending=False).head(3)
    if len(best):
        text = "; ".join(f"{r['symbol']} {r['timeframe']} {r['configuration']} (PF {r['profit_factor']:.2f}, {int(r['total_trades'])} trades, {r['net_result_r']:+.1f} R)" for _, r in best.iterrows())
        w(f"- Highest profit factor among runs with at least 20 trades: {text}. With this little data the ranking is not evidence of an edge.")
    w("")

    w("### Cross-check: TradingView against the Python engine\n")
    w("For every run the Python engine was given the same settings and commission and started on the first day of TradingView's span, on the research data (a different price feed). Only the period both sources cover is compared. \"Same bar\" = both have a trade in the same direction entered on the same bar.\n")
    check = frame[frame["check_tv_trades_in_overlap"] > 0]
    tv_n, py_n, matched = int(check["check_tv_trades_in_overlap"].sum()), int(check["check_python_trades_in_overlap"].sum()), int(check["check_matched_same_bar_and_side"].sum())
    same_exit = int(check["check_matched_same_exit_bar"].fillna(0).sum())
    same_outcome = int(check["check_matched_same_outcome"].fillna(0).sum())
    w(f"- TradingView trades in the common period: {tv_n:,}; Python trades: {py_n:,}.")
    w(f"- {matched:,} TradingView trades ({matched / tv_n:.0%}) have a Python trade on the same bar in the same direction; of those, {same_outcome / matched:.0%} have the same win/loss outcome and {same_exit / matched:.0%} exit on the same bar.")
    w(f"- Median entry-price difference between the two feeds on matched trades: {check['check_matched_entry_price_median_diff_bp'].median():.1f} basis points.")
    w(f"- Net result over all compared runs: TradingView {check['check_tv_net_r_in_overlap'].sum():+.1f} R, Python {check['check_python_net_r_in_overlap'].sum():+.1f} R.")
    by_tf = check.groupby("timeframe", observed=True).agg(runs=("symbol", "size"), tv=("check_tv_trades_in_overlap", "sum"), py=("check_python_trades_in_overlap", "sum"), matched=("check_matched_same_bar_and_side", "sum"), tv_r=("check_tv_net_r_in_overlap", "sum"), py_r=("check_python_net_r_in_overlap", "sum")).reset_index()
    by_tf["share"] = by_tf["matched"] / by_tf["tv"]
    by_tf.columns = ["Timeframe", "Runs", "TradingView trades", "Python trades", "Same bar and side", "TradingView net (R)", "Python net (R)", "Matched share"]
    w("")
    w(_table(by_tf, {"TradingView net (R)": "{:+.1f}", "Python net (R)": "{:+.1f}", "Matched share": "{:.0%}"}))
    w("\nTrades that do not match come from the feed difference: a few cents decide whether a level was swept or a zone touched on a given bar, and after one such difference the two runs can hold different positions for a while. The unmatched share is the size of that effect, not an error in either implementation (the same Pine source matches the Python engine exactly when both read the same bars, section 10).")
    return "\n".join(out) + "\n"


def main() -> None:
    frame = collect()
    frame.to_csv(TV_DIR / "strategy_tester.csv", index=False, float_format="%.4f")
    (TV_DIR / "NOTES.md").write_text(notes(frame))
    print(f"{len(frame)} TradingView runs -> {TV_DIR / 'strategy_tester.csv'}")


if __name__ == "__main__":
    main()
