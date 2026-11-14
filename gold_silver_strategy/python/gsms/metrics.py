"""Performance statistics from a list of trade results.

Everything is measured in R, the amount risked on a trade (entry-to-stop distance times
position size). The Pine strategy sizes each position to risk a fixed cash amount, so
cash profit = R x that amount; with 1% of starting equity at risk, 1R = 1% (not compounded).
"""

from __future__ import annotations

import numpy as np
from numba import njit

from .backtest import Trades

RISK_CASH = 100.0  # reporting convention: $10,000 account risking $100 (1%) per trade

CORE_FIELDS = (
    "trades", "wins", "losses", "win_rate", "profit_factor", "net_r", "max_dd_r",
    "avg_win_r", "avg_loss_r", "payoff", "expectancy_r", "max_consec_wins", "max_consec_losses",
    "long_trades", "long_win_rate", "long_net_r", "long_pf",
    "short_trades", "short_win_rate", "short_net_r", "short_pf",
    "t_stat", "sharpe_monthly", "pos_months",
)  # fmt: skip


@njit(cache=True)
def _core(r, direction, month, n_months):
    out = np.zeros(24)
    n = len(r)
    if n == 0:
        return out
    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    run_w = 0
    run_l = 0
    best_w = 0
    best_l = 0
    side_n = np.zeros(2)
    side_w = np.zeros(2)
    side_gw = np.zeros(2)
    side_gl = np.zeros(2)
    monthly = np.zeros(max(n_months, 1))
    sum_sq = 0.0
    for i in range(n):
        x = r[i]
        s = 0 if direction[i] > 0 else 1
        side_n[s] += 1
        if x > 0:
            wins += 1
            gross_win += x
            side_w[s] += 1
            side_gw[s] += x
            run_w += 1
            run_l = 0
            if run_w > best_w:
                best_w = run_w
        else:
            gross_loss -= x
            side_gl[s] -= x
            run_l += 1
            run_w = 0
            if run_l > best_l:
                best_l = run_l
        equity += x
        sum_sq += x * x
        if equity > peak:
            peak = equity
        if peak - equity > max_dd:
            max_dd = peak - equity
        if 0 <= month[i] < n_months:
            monthly[month[i]] += x
    losses = n - wins
    mean = equity / n
    out[0] = n
    out[1] = wins
    out[2] = losses
    out[3] = wins / n
    out[4] = gross_win / gross_loss if gross_loss > 0 else np.inf
    out[5] = equity
    out[6] = max_dd
    out[7] = gross_win / wins if wins > 0 else 0.0
    out[8] = -gross_loss / losses if losses > 0 else 0.0
    out[9] = out[7] / -out[8] if out[8] < 0 else np.inf
    out[10] = mean
    out[11] = best_w
    out[12] = best_l
    for s in range(2):
        base = 13 + 4 * s
        out[base] = side_n[s]
        out[base + 1] = side_w[s] / side_n[s] if side_n[s] > 0 else 0.0
        out[base + 2] = side_gw[s] - side_gl[s]
        out[base + 3] = side_gw[s] / side_gl[s] if side_gl[s] > 0 else np.inf
    if n > 1:
        var = (sum_sq - n * mean * mean) / (n - 1)
        if var > 0:
            out[21] = mean / np.sqrt(var / n)
    if n_months > 1:
        m_mean = monthly.mean()
        m_std = monthly.std()
        if m_std > 0:
            out[22] = m_mean / m_std * np.sqrt(12.0)
        out[23] = (monthly > 0).sum() / n_months
    return out


def month_index(times: np.ndarray, start) -> np.ndarray:
    """Whole months elapsed between `start` and each timestamp."""
    months = times.astype("datetime64[M]").astype(np.int64)
    return months - np.datetime64(start, "M").astype(np.int64)


def summarize(trades: Trades, start, end) -> dict:
    """Statistics for trades entered in [start, end).

    sharpe_monthly is the annualised Sharpe ratio of monthly R totals (months without trades
    count as zero), so it rewards frequency and consistency rather than a few large winners.
    """
    start64, end64 = np.datetime64(start), np.datetime64(end)
    keep = (trades.entry_time >= start64) & (trades.entry_time < end64)
    r = trades.r[keep]
    n_months = int(np.datetime64(end64, "M").astype(np.int64) - np.datetime64(start64, "M").astype(np.int64))
    core = _core(r, trades.direction[keep], month_index(trades.entry_time[keep], start64), n_months)
    stats = dict(zip(CORE_FIELDS, core.tolist()))
    for name in ("trades", "wins", "losses", "max_consec_wins", "max_consec_losses", "long_trades", "short_trades"):
        stats[name] = int(stats[name])
    years = (end64 - start64) / np.timedelta64(1, "D") / 365.25
    stats["years"] = float(years)
    stats["trades_per_year"] = stats["trades"] / years if years > 0 else 0.0
    stats["net_r_per_year"] = stats["net_r"] / years if years > 0 else 0.0
    stats["recovery"] = stats["net_r"] / stats["max_dd_r"] if stats["max_dd_r"] > 0 else (np.inf if stats["net_r"] > 0 else 0.0)
    stats["net_profit_cash"] = stats["net_r"] * RISK_CASH
    stats["max_dd_cash"] = stats["max_dd_r"] * RISK_CASH
    stats["avg_cost_r"] = float(trades.cost_r[keep].mean()) if stats["trades"] else 0.0
    stats["gross_expectancy_r"] = float(trades.r_gross[keep].mean()) if stats["trades"] else 0.0
    return stats
