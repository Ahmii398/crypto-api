"""Parity check: the generated Pine strategy, run in PineTS, against the Python engine.

    python -m gsms.parity --jobs 60

PineTS (https://github.com/LuxAlgo/PineTS, AGPL-3.0) is an open-source Pine Script runtime with
its own broker emulator. It is NOT TradingView: agreement here shows that the Pine source and
the Python engine implement the same rules, not that TradingView's Strategy Tester was run.

For each job a random strategy is compiled into the Pine script as its default preset, the
script is executed bar by bar on a slice of history, and every internal series (ATR, EMAs,
previous day/week levels, module signals, entries, stops, targets) and every closed trade is
compared with the Python engine on the same bars.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from . import PROJECT_ROOT, RESULTS_DIR, TF_MINUTES, backtest, data, pine_build, space
from .params import Params

TOOL_DIR = PROJECT_ROOT / "tools" / "pinets"
PINE_TIMEFRAME = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "1D": "D"}
MINTICK = {"XAUUSD": 0.001, "XAGUSD": 0.00001}
RISK_CASH = 1000.0  # i_riskCash default in the Pine script
COMMISSION_PCT = 0.0175  # commission_value in the Pine strategy() declaration

# (symbol, timeframe, first day, last day): about 2,500-6,500 bars each, all seven timeframes.
SLICES = [
    ("XAUUSD", "5m", "2025-03-03", "2025-03-28"),
    ("XAGUSD", "5m", "2016-06-06", "2016-07-01"),
    ("XAUUSD", "15m", "2020-02-17", "2020-04-24"),
    ("XAGUSD", "15m", "2024-09-02", "2024-11-08"),
    ("XAUUSD", "30m", "2013-03-04", "2013-07-26"),
    ("XAGUSD", "30m", "2021-01-04", "2021-05-28"),
    ("XAUUSD", "1h", "2024-01-01", "2024-06-28"),
    ("XAGUSD", "1h", "2011-03-07", "2011-09-30"),
    ("XAUUSD", "2h", "2018-01-01", "2019-03-29"),
    ("XAGUSD", "2h", "2022-06-06", "2023-09-01"),
    ("XAUUSD", "4h", "2015-01-05", "2017-12-29"),
    ("XAGUSD", "4h", "2019-01-07", "2021-12-31"),
    ("XAUUSD", "1D", "2010-01-04", "2026-11-05"),
    ("XAGUSD", "1D", "2010-01-04", "2026-11-05"),
]

FLOAT_SERIES = ("atr", "emaFast", "emaSlow", "pdh", "pdl", "pwh", "pwl")
FLAG_SERIES = ("newDay", "newWeek", "lsBull", "lsBear", "sdTouchBull", "sdRejBull", "sdInvalBull", "sdTouchBear", "sdRejBear", "sdInvalBear", "cpBull", "cpBear")
DEBUG_PLOTS = [f"dbg_{name}" for name in FLOAT_SERIES + FLAG_SERIES + ("goLong", "goShort", "stopPx", "targetPx", "stopDist", "barHour")]
INDICATOR_PLOTS = ["dbg_vTrades", "dbg_vWins", "dbg_vNetR", "dbg_vMaxDdR"]  # the indicator's own virtual broker


def _slice(symbol: str, timeframe: str, first: str, last: str) -> pd.DataFrame:
    bars = data.load_bars(symbol, timeframe)
    keep = (bars["session"] >= pd.Timestamp(first)) & (bars["session"] <= pd.Timestamp(last))
    return bars[keep].reset_index(drop=True)


def _ms(times: pd.Series) -> np.ndarray:
    return times.to_numpy("datetime64[ms]").astype(np.int64)


def _candles(frame: pd.DataFrame, minutes: int) -> list[dict]:
    return [
        {"openTime": int(t), "closeTime": int(t) + minutes * 60_000 - 1, "open": o, "high": h, "low": l, "close": c, "volume": 1000.0}
        for t, o, h, l, c in zip(_ms(frame["time"]), frame["open"], frame["high"], frame["low"], frame["close"])
    ]


def _period_candles(frame: pd.DataFrame, column: str, minutes: int) -> list[dict]:
    """Daily or weekly candles aggregated from the slice itself, exactly as backtest.Bars does."""
    grouped = frame.groupby(column, sort=True).agg(time=("time", "first"), last=("time", "last"), open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    return [
        {"openTime": int(t), "closeTime": int(e) + minutes * 60_000 - 1, "open": o, "high": h, "low": l, "close": c, "volume": 1000.0}
        for t, e, o, h, l, c in zip(_ms(grouped["time"]), _ms(grouped["last"]), grouped["open"], grouped["high"], grouped["low"], grouped["close"])
    ]


def run_pinets(frame: pd.DataFrame, symbol: str, timeframe: str, source: str, workdir: Path, plots: list[str]) -> dict:
    minutes = TF_MINUTES[timeframe]
    files = {"chart": workdir / "chart.json", "D": workdir / "daily.json", "W": workdir / "weekly.json"}
    files["chart"].write_text(json.dumps(_candles(frame, minutes)))
    files["D"].write_text(json.dumps(_period_candles(frame, "session", minutes)))
    files["W"].write_text(json.dumps(_period_candles(frame, "week", minutes)))
    pine_file, out_file, job_file = workdir / "script.pine", workdir / "out.json", workdir / "job.json"
    pine_file.write_text(source)
    job_file.write_text(json.dumps({
        "pine": str(pine_file), "out": str(out_file), "symbol": symbol, "timeframe": PINE_TIMEFRAME[timeframe],
        "mintick": MINTICK[symbol], "bars": {k: str(v) for k, v in files.items()}, "plots": plots,
    }))  # fmt: skip
    done = subprocess.run(["node", "run_pine.mjs", str(job_file)], cwd=TOOL_DIR, capture_output=True, text=True, timeout=1800)
    if done.returncode != 0:
        raise RuntimeError(f"PineTS failed: {done.stderr.strip()[-2000:]}")
    return json.loads(out_file.read_text())


def _series(result: dict, name: str) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in result["plots"][f"dbg_{name}"]], dtype=float)


def compare(frame: pd.DataFrame, symbol: str, timeframe: str, p: Params, result: dict, target: str = "STRATEGY") -> dict:
    """Bar-by-bar and trade-by-trade differences between PineTS and the Python engine."""
    bars = backtest.Bars(frame, symbol, timeframe)
    tick = MINTICK[symbol]
    trades = backtest.run(bars, p, cost_pct_per_side=COMMISSION_PCT, tick=tick)
    n = len(bars)
    ls = bars.liquidity(p)
    sd = bars.zones(p)
    cp = bars.candles(p)
    python = {
        "atr": bars.atr(p.atr_len), "emaFast": bars.ema(p.ema_fast), "emaSlow": bars.ema(p.ema_slow),
        "pdh": bars.pdh, "pdl": bars.pdl, "pwh": bars.pwh, "pwl": bars.pwl,
        "newDay": bars.new_day, "newWeek": bars.new_week, "lsBull": ls[0], "lsBear": ls[1],
        "sdTouchBull": sd[0], "sdRejBull": sd[1], "sdInvalBull": sd[3], "sdTouchBear": sd[4], "sdRejBear": sd[5], "sdInvalBear": sd[7],
        "cpBull": cp[0], "cpBear": cp[1],
    }  # fmt: skip
    # Levels that are never read are not worth failing on: the script only uses the previous
    # day/week values on the first bar of a new day/week.
    report = {"bars": n, "series": {}}
    for name in FLOAT_SERIES:
        theirs, ours = _series(result, name), np.asarray(python[name], dtype=float)
        both = ~np.isnan(theirs) & ~np.isnan(ours)
        report["series"][name] = {
            "max_abs_diff": float(np.max(np.abs(theirs[both] - ours[both]))) if both.any() else 0.0,
            "nan_mismatch": int(np.sum(np.isnan(theirs) != np.isnan(ours))),
        }
    for name in FLAG_SERIES:
        theirs, ours = _series(result, name) > 0.5, np.asarray(python[name], dtype=bool)
        if name in ("newDay", "newWeek"):
            theirs, ours = theirs[1:], ours[1:]  # the very first bar has no previous bar to differ from
        report["series"][name] = {"true_pine": int(theirs.sum()), "true_python": int(ours.sum()), "mismatch": int(np.sum(theirs != ours))}

    hour = _series(result, "barHour")
    report["series"]["barHour"] = {"mismatch": int(np.sum(hour != bars.hour))}

    # Signals: Python records the signal bar of every closed trade. PineTS additionally shows
    # the signal of a trade that is still open, or was never filled, at the end of the data.
    go = np.where(_series(result, "goLong") > 0.5, 1, 0) - np.where(_series(result, "goShort") > 0.5, 1, 0)
    pine_signals = np.flatnonzero(go)
    extra = np.setdiff1d(pine_signals, trades.signal_bar)
    missing = np.setdiff1d(trades.signal_bar, pine_signals)
    last_exit = int(trades.exit_bar[-1]) if len(trades) else -1
    report["signals"] = {
        "pine": int(len(pine_signals)), "python_closed": int(len(trades)),
        "pine_only_before_last_exit": int(np.sum(extra < last_exit)), "pine_only_at_end": int(np.sum(extra >= last_exit)), "python_only": int(len(missing)),
        "direction_mismatch": int(np.sum(go[trades.signal_bar] != trades.direction)) if len(trades) and not len(missing) else None,
    }  # fmt: skip

    if target == "INDICATOR":
        # The indicator keeps its own trade ledger (the dashboard figures); compare its totals.
        r = trades.r
        equity = np.cumsum(r)
        max_dd = float(np.max(np.maximum.accumulate(np.maximum(equity, 0.0)) - equity)) if len(r) else 0.0
        last = {name: _series(result, name)[-1] for name in ("vTrades", "vWins", "vNetR", "vMaxDdR")}
        report["trades"] = {
            "pine": int(last["vTrades"]), "python": int(len(trades)), "open_in_pine": 0,
            "wins_pine": int(last["vWins"]), "wins_python": int(np.sum(r > 0)),
            "net_r_pine": float(last["vNetR"]), "net_r_python": float(r.sum()),
            "max_dd_r_pine": float(last["vMaxDdR"]), "max_dd_r_python": max_dd,
        }  # fmt: skip
        return report

    closed = result["closed"]
    report["trades"] = {"pine": len(closed), "python": int(len(trades)), "open_in_pine": len(result["open"])}
    m = min(len(closed), len(trades))
    if m:
        frame_time = bars.time.astype("datetime64[ms]").astype(np.int64)
        entry_bar = np.array([t["entry_bar"] for t in closed[:m]])
        exit_bar = np.array([t["exit_bar"] for t in closed[:m]])
        entry_px = np.array([t["entry_price"] for t in closed[:m]])
        exit_px = np.array([t["exit_price"] for t in closed[:m]])
        size = np.array([t["size"] for t in closed[:m]])
        profit = np.array([t["profit"] for t in closed[:m]])
        commission = np.array([t["commission"] or 0.0 for t in closed[:m]])
        qty = RISK_CASH / trades.stop_dist[:m]
        gross = trades.direction[:m] * (trades.exit[:m] - trades.entry[:m]) * qty
        cost = COMMISSION_PCT / 100.0 * (trades.entry[:m] + trades.exit[:m]) * qty
        report["trades"].update({
            "compared": m,
            "entry_bar_mismatch": int(np.sum(entry_bar != trades.entry_bar[:m])),
            "exit_bar_mismatch": int(np.sum(exit_bar != trades.exit_bar[:m])),
            "entry_time_mismatch": int(np.sum(np.array([t["entry_time"] for t in closed[:m]]) != frame_time[trades.entry_bar[:m]])),
            "direction_mismatch": int(np.sum(np.sign(size) != trades.direction[:m])),
            "entry_price_max_diff": float(np.max(np.abs(entry_px - trades.entry[:m]))),
            "exit_price_max_diff": float(np.max(np.abs(exit_px - trades.exit[:m]))),
            "size_max_rel_diff": float(np.max(np.abs(np.abs(size) - qty) / qty)),
            # PineTS reports profit either before or after commission depending on the field;
            # both readings are recorded so the report can state which one matched.
            "profit_gross_max_diff": float(np.max(np.abs(profit - gross))),
            "profit_net_max_diff": float(np.max(np.abs(profit - (gross - cost)))),
            "commission_max_diff": float(np.max(np.abs(commission - cost))),
            "python_net_cash": float(np.sum(gross - cost)),
            "pine_netprofit": result.get("netprofit"),
        })  # fmt: skip
    return report


def passed(report: dict, tick: float) -> tuple[bool, list[str]]:
    """Whether a job counts as an exact match, and what differs when it does not."""
    problems = []
    for name, entry in report["series"].items():
        # PineTS reports plot values rounded to 10 decimals, hence the 1e-9 allowance.
        if entry.get("mismatch", 0) or entry.get("nan_mismatch", 0) or entry.get("max_abs_diff", 0.0) > 1e-9:
            problems.append(f"series {name}: {entry}")
    signals = report["signals"]
    if signals["pine_only_before_last_exit"] or signals["python_only"] or signals["direction_mismatch"]:
        problems.append(f"signals: {signals}")
    trades = report["trades"]
    if trades["pine"] != trades["python"]:
        problems.append(f"trade count: pine {trades['pine']} vs python {trades['python']}")
    if "net_r_pine" in trades:
        if trades["wins_pine"] != trades["wins_python"]:
            problems.append(f"indicator wins: {trades['wins_pine']} vs {trades['wins_python']}")
        for key in ("net_r", "max_dd_r"):
            if abs(trades[f"{key}_pine"] - trades[f"{key}_python"]) > 1e-6:
                problems.append(f"indicator {key}: {trades[f'{key}_pine']} vs {trades[f'{key}_python']}")
    if trades.get("compared"):
        for key in ("entry_bar_mismatch", "exit_bar_mismatch", "entry_time_mismatch", "direction_mismatch"):
            if trades[key]:
                problems.append(f"trades {key}: {trades[key]}")
        for key in ("entry_price_max_diff", "exit_price_max_diff"):
            if trades[key] > tick * 0.51:
                problems.append(f"trades {key}: {trades[key]}")
    return not problems, problems


def job_params(rng: np.random.Generator, index: int) -> Params:
    """Random strategies, with the first ones forced through every module combination."""
    subset = space.SUBSETS[index % len(space.SUBSETS)] if index < 2 * len(space.SUBSETS) else None
    return space.sample(rng, subset=subset)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=int, default=56)
    parser.add_argument("--seed", type=int, default=20261114)
    parser.add_argument("--target", choices=["STRATEGY", "INDICATOR"], default="STRATEGY")
    parser.add_argument("--offset", type=int, default=0, help="rotate the slice order, so the same strategies meet other slices")
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    frames = {}
    rows = []
    for index in range(args.jobs):
        symbol, timeframe, first, last = SLICES[(index + args.offset) % len(SLICES)]
        key = (symbol, timeframe)
        if key not in frames:
            frames[key] = _slice(symbol, timeframe, first, last)
        frame = frames[key]
        p = job_params(rng, index)
        preset = {"name": "parity", "symbol": symbol, "timeframe": timeframe, "params": p.to_dict()}
        source = pine_build.render(args.target, [preset], default="parity", debug=True)
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pinets(frame, symbol, timeframe, source, Path(tmp), DEBUG_PLOTS + (INDICATOR_PLOTS if args.target == "INDICATOR" else []))
        report = compare(frame, symbol, timeframe, p, result, args.target)
        ok, problems = passed(report, MINTICK[symbol])
        rows.append({
            "job": index, "symbol": symbol, "timeframe": timeframe, "first": first, "last": last, "modules": p.modules(),
            "exact": ok, "problems": problems, "pinets_version": result["pinets_version"], "pinets_warnings": result["warnings"],
            "params": p.to_dict(), **report,
        })  # fmt: skip
        trades = report["trades"]
        print(f"[{index + 1:3d}/{args.jobs}] {symbol} {timeframe:>3s} {p.modules():12s} bars {report['bars']:5d}  trades pine {trades['pine']:4d} python {trades['python']:4d}  {'EXACT' if ok else 'DIFF: ' + '; '.join(problems)[:300]}", flush=True)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"parity_pinets_{args.target.lower()}.json").write_text(json.dumps(rows, indent=1, default=float))
    exact = sum(r["exact"] for r in rows)
    print(f"\n{exact}/{len(rows)} jobs match exactly; {sum(r['trades']['python'] for r in rows)} trades compared")


if __name__ == "__main__":
    main()
