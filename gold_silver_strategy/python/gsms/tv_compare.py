"""Compare a TradingView Strategy Tester trade list with the Python engine.

TradingView ran the strategy on its own OANDA bars; the Python engine runs the same settings
on the research data (HistData) starting from the same first bar. The two price feeds differ
by small amounts on every bar, so the trade lists are expected to be close, not identical.
A trade is "matched" when both lists have a trade in the same direction entered on the same bar.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest, data
from .params import Params

STRATEGY_COMMISSION_PCT = {"XAUUSD": 0.0175, "XAGUSD": 0.08}  # as set in the TradingView run
MINTICK = {"XAUUSD": 0.001, "XAGUSD": 0.00001}
RISK_CASH = 1000.0


def tv_trades(path: Path) -> pd.DataFrame:
    rows = json.loads(Path(path).read_text())["trades"]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["n", "side", "entry_time", "exit_time", "entry_price", "exit_price", "size", "net_pnl", "direction"])
    for column in ("entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column], format="%b %d, %Y, %H:%M")
    frame["direction"] = np.where(frame["side"] == "Long", 1, -1)
    return frame.sort_values("entry_time").reset_index(drop=True)


def python_trades(symbol: str, timeframe: str, params: dict, first_bar: str, commission_pct: float | None = None) -> tuple[pd.DataFrame, pd.Timestamp, int]:
    bars = data.load_bars(symbol, timeframe)
    bars = bars[bars["time"] >= pd.Timestamp(first_bar)].reset_index(drop=True)
    wrapped = backtest.Bars(bars, symbol, timeframe)
    cost = STRATEGY_COMMISSION_PCT[symbol] if commission_pct is None else commission_pct
    trades = backtest.run(wrapped, Params.from_dict(params), cost_pct_per_side=cost, tick=MINTICK[symbol])
    frame = trades.frame()
    frame["net_pnl"] = frame["r"] * RISK_CASH
    return frame, bars["time"].iloc[-1], len(bars)


def compare(tv: pd.DataFrame, py: pd.DataFrame, data_end: pd.Timestamp) -> dict:
    """Statistics over the period both sources cover (TradingView trades closed by `data_end`)."""
    tv_in = tv[tv["exit_time"] <= data_end]
    py_in = py[py["exit_time"] <= data_end] if len(py) else py
    out = {
        "tv_trades_total": int(len(tv)), "tv_trades_in_overlap": int(len(tv_in)), "python_trades_in_overlap": int(len(py_in)),
        "tv_net_r_in_overlap": float(tv_in["net_pnl"].sum() / RISK_CASH) if len(tv_in) else 0.0,
        "python_net_r_in_overlap": float(py_in["net_pnl"].sum() / RISK_CASH) if len(py_in) else 0.0,
        "tv_win_rate_in_overlap": float((tv_in["net_pnl"] > 0).mean()) if len(tv_in) else None,
        "python_win_rate_in_overlap": float((py_in["net_pnl"] > 0).mean()) if len(py_in) else None,
        "matched_same_bar_and_side": 0,
    }  # fmt: skip
    if len(tv_in) and len(py_in):
        left = tv_in[["entry_time", "direction", "exit_time", "entry_price", "net_pnl"]].rename(columns={"exit_time": "tv_exit_time", "entry_price": "tv_entry", "net_pnl": "tv_pnl"})
        right = py_in[["entry_time", "direction", "exit_time", "entry", "net_pnl"]].rename(columns={"exit_time": "py_exit_time", "entry": "py_entry", "net_pnl": "py_pnl"})
        merged = left.merge(right, on=["entry_time", "direction"], how="inner")
        out["matched_same_bar_and_side"] = int(len(merged))
        if len(merged):
            rel = (merged["tv_entry"] - merged["py_entry"]).abs() / merged["py_entry"]
            out.update({
                "matched_same_exit_bar": int((merged["tv_exit_time"] == merged["py_exit_time"]).sum()),
                "matched_same_outcome": int(((merged["tv_pnl"] > 0) == (merged["py_pnl"] > 0)).sum()),
                "matched_entry_price_median_diff_bp": float(rel.median() * 1e4),
                "matched_entry_price_max_diff_bp": float(rel.max() * 1e4),
            })  # fmt: skip
    return out
