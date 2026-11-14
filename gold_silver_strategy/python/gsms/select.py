"""Stages 6 and 8: timeframe / module comparison and choice of the final configurations.

    python -m gsms.select

Reads the search results (train and validation only) and writes
  results/timeframe_comparison.csv   one row per symbol and timeframe
  results/module_comparison.csv      one row per symbol, timeframe and module combination
  results/parameter_effects.csv      how each setting value fared, per symbol
  results/candidates.csv             the short-list with the neighbourhood (plateau) check
  results/final_configs.json         Conservative / Balanced / Aggressive per asset

Selection uses data before OOS_START only. A configuration has to
  1. include the supply/demand module,
  2. trade both directions (see below),
  3. trade often enough (MIN_TRAIN_TRADES / MIN_VAL_TRADES),
  4. be profitable after costs in BOTH the train and the validation period,
  5. sit on a plateau: most one-step neighbours in the parameter grid are profitable too.
It is ranked by the worse of its train and validation monthly Sharpe ratios, never by win rate.

Why two-sided only: in the search, long-only settings on gold were profitable in both periods
more than twice as often as two-sided ones, and short-only ones far less often. That is the
metal's upward drift, not a property of the entry rules, and it would turn the presets into a
leveraged bet on the bull market continuing. "Long only" stays available as an input.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import RESULTS_DIR, SYMBOLS, TIMEFRAMES, backtest, data, search, space
from . import params as P

MIN_TRAIN_TRADES = 80
MIN_VAL_TRADES = 40
SHORTLIST = 40  # candidates per symbol that get the neighbourhood check
MIN_NEIGHBOUR_SHARE = 0.70
ASSET_NAME = {"XAUUSD": "Gold", "XAGUSD": "Silver"}


def _enough(df: pd.DataFrame) -> pd.Series:
    return (df["train_trades"] >= MIN_TRAIN_TRADES) & (df["val_trades"] >= MIN_VAL_TRADES)


def _score(df: pd.DataFrame) -> pd.Series:
    return np.minimum(df["train_sharpe_monthly"], df["val_sharpe_monthly"])


def _group_stats(df: pd.DataFrame) -> dict:
    """How a set of strategies did; `df` holds strategies with enough trades only."""
    if df.empty:
        return {"strategies": 0}
    both = (df["train_net_r"] > 0) & (df["val_net_r"] > 0)
    top = df[df["train_sharpe_monthly"] >= df["train_sharpe_monthly"].quantile(0.9)]
    rank = df[["train_sharpe_monthly", "val_sharpe_monthly"]].corr(method="spearman").iloc[0, 1] if len(df) >= 10 else np.nan
    gross_rank = df[["train_gross_expectancy_r", "val_gross_expectancy_r"]].corr(method="spearman").iloc[0, 1] if len(df) >= 10 else np.nan
    return {
        "strategies": len(df),
        "median_trades_per_year": df["pre_trades_per_year"].median(),
        "median_cost_r_per_trade": df["pre_avg_cost_r"].median(),
        "median_gross_expectancy_r": df["pre_gross_expectancy_r"].median(),
        "median_net_expectancy_r": df["pre_expectancy_r"].median(),
        "share_profitable_train": (df["train_net_r"] > 0).mean(),
        "share_profitable_validation": (df["val_net_r"] > 0).mean(),
        "share_profitable_both": both.mean(),
        "median_validation_sharpe": df["val_sharpe_monthly"].median(),
        "train_validation_rank_corr": rank,
        "train_validation_rank_corr_before_costs": gross_rank,
        "top10pct_train_median_validation_sharpe": top["val_sharpe_monthly"].median(),
        "top10pct_train_share_profitable_validation": (top["val_net_r"] > 0).mean(),
        "best_min_sharpe": _score(df).max(),
    }


def timeframe_comparison(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (symbol, timeframe), part in df[df["group"] == "random"].groupby(["symbol", "timeframe"], sort=False):
        rows.append({"symbol": symbol, "timeframe": timeframe, "tested": len(part), **_group_stats(part[_enough(part)])})
    out = pd.DataFrame(rows)
    out["timeframe"] = pd.Categorical(out["timeframe"], TIMEFRAMES, ordered=True)
    return out.sort_values(["symbol", "timeframe"]).reset_index(drop=True)


def module_comparison(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (symbol, timeframe, modules), part in df.groupby(["symbol", "timeframe", "modules"], sort=False):
        base = part[part["group"] == "baseline"]
        row = {"symbol": symbol, "timeframe": timeframe, "modules": modules, "tested": len(part)}
        if len(base):
            b = base.iloc[0]
            row.update({f"default_{k}": b[k] for k in ("train_trades", "train_win_rate", "train_profit_factor", "train_net_r", "train_max_dd_r", "val_trades", "val_win_rate", "val_profit_factor", "val_net_r", "val_max_dd_r")})
        row.update(_group_stats(part[_enough(part) & (part["group"] == "random")]))
        rows.append(row)
    return pd.DataFrame(rows)


def parameter_effects(df: pd.DataFrame) -> pd.DataFrame:
    """Median validation result by value of each setting, over strategies where it matters."""
    owner = {name: module for module, names in space._OWNED.items() for name in names}
    rows = []
    usable = df[(df["group"] == "random") & _enough(df)]
    for symbol, part in usable.groupby("symbol", sort=False):
        for name in [n for n in space.SPACE if n != "session"] + ["sess_start"]:
            subset = part[part[f"use_{owner[name]}"]] if name in owner else part
            if name in ("sl_atr",):
                subset = subset[subset["sl_mode"] == 0]
            if name in ("sl_lookback", "sl_buffer"):
                subset = subset[subset["sl_mode"] != 0]
            for value, group in subset.groupby(name):
                rows.append({
                    "symbol": symbol, "setting": name, "value": value, "strategies": len(group),
                    "share_profitable_both": ((group["train_net_r"] > 0) & (group["val_net_r"] > 0)).mean(),
                    "median_train_expectancy_r": group["train_expectancy_r"].median(),
                    "median_validation_expectancy_r": group["val_expectancy_r"].median(),
                    "median_validation_sharpe": group["val_sharpe_monthly"].median(),
                })  # fmt: skip
    return pd.DataFrame(rows)


def neighbourhood(symbol: str, timeframe: str, p, bars_cache: dict) -> dict:
    key = (symbol, timeframe)
    if key not in bars_cache:
        bars_cache[key] = backtest.Bars(data.load_bars(symbol, timeframe), symbol, timeframe, cache_limit=6)
    results = [search.evaluate(bars_cache[key], q) for q in space.neighbours(p)]
    net = np.array([r["pre_net_r"] for r in results])
    both = np.array([r["train_net_r"] > 0 and r["val_net_r"] > 0 for r in results])
    return {
        "neighbours": len(results),
        "neighbours_profitable_pre_oos": float((net > 0).mean()) if len(net) else np.nan,
        "neighbours_profitable_both": float(both.mean()) if len(both) else np.nan,
        "neighbours_median_sharpe": float(np.median([r["pre_sharpe_monthly"] for r in results])) if results else np.nan,
    }


def shortlist(df: pd.DataFrame) -> pd.DataFrame:
    eligible = df[df["use_sd"] & (df["direction"] == P.DIR_BOTH) & _enough(df) & (df["train_net_r"] > 0) & (df["val_net_r"] > 0)].copy()
    eligible["score"] = _score(eligible)
    out, bars_cache = [], {}
    for symbol in SYMBOLS:
        part = eligible[eligible["symbol"] == symbol].sort_values("score", ascending=False).head(SHORTLIST)
        for _, row in part.iterrows():
            extra = neighbourhood(symbol, row["timeframe"], search.params_of(row), bars_cache)
            out.append({**row.to_dict(), **extra})
        print(f"{symbol}: {int((eligible['symbol'] == symbol).sum())} eligible, {len(part)} short-listed", flush=True)
    result = pd.DataFrame(out)
    if len(result):
        result["plateau"] = result["neighbours_profitable_pre_oos"] >= MIN_NEIGHBOUR_SHARE
    return result


def choose(candidates: pd.DataFrame) -> list[dict]:
    """Balanced = best score; Conservative = best return per unit of drawdown; Aggressive = most R per year."""
    presets = []
    for symbol in SYMBOLS:
        pool = candidates[candidates["symbol"] == symbol] if len(candidates) else candidates
        robust = pool[pool["plateau"]] if len(pool) else pool
        note = "passed all selection rules"
        if len(robust) < 3:
            robust, note = pool, "fewer than three candidates passed the plateau check; chosen from the short-list regardless"
        if robust.empty:
            print(f"{symbol}: no configuration was profitable in both train and validation")
            continue
        robust = robust.assign(recovery=robust["pre_net_r"] / robust["pre_max_dd_r"].clip(lower=1e-9))
        taken = set()
        for label, column in (("Balanced", "score"), ("Conservative", "recovery"), ("Aggressive", "pre_net_r_per_year")):
            ranked = robust[~robust["strategy_id"].astype(str).add(robust["timeframe"].astype(str)).isin(taken)].sort_values(column, ascending=False)
            if ranked.empty:
                continue
            row = ranked.iloc[0]
            taken.add(str(row["strategy_id"]) + str(row["timeframe"]))
            presets.append({
                "name": f"{ASSET_NAME[symbol]} {row['timeframe']} {label}",
                "symbol": symbol, "timeframe": row["timeframe"], "profile": label, "strategy_id": int(row["strategy_id"]),
                "selection_note": note, "selected_by": column,
                "params": search.params_of(row).to_dict(),
                "selection_stats": {k: (None if isinstance(row[k], float) and not np.isfinite(row[k]) else row[k]) for k in row.index if k.startswith(("train_", "val_", "pre_", "neighbours", "score", "plateau"))},
            })  # fmt: skip
    order = {"Conservative": 0, "Balanced": 1, "Aggressive": 2}
    return sorted(presets, key=lambda preset: (SYMBOLS.index(preset["symbol"]), order[preset["profile"]]))


def main() -> None:
    df = search.load()
    print(f"{len(df)} results: {df['strategy_id'].nunique()} strategies on {df.groupby(['symbol', 'timeframe']).ngroups} datasets")
    timeframe_comparison(df).to_csv(RESULTS_DIR / "timeframe_comparison.csv", index=False, float_format="%.4f")
    module_comparison(df).to_csv(RESULTS_DIR / "module_comparison.csv", index=False, float_format="%.4f")
    parameter_effects(df).to_csv(RESULTS_DIR / "parameter_effects.csv", index=False, float_format="%.4f")
    candidates = shortlist(df)
    candidates.to_csv(RESULTS_DIR / "candidates.csv", index=False, float_format="%.5f")
    presets = choose(candidates)
    payload = {
        "selection_data": {"train": search.TRAIN, "validation": search.VALIDATION, "out_of_sample_not_used": search.OOS},
        "rules": {"min_train_trades": MIN_TRAIN_TRADES, "min_validation_trades": MIN_VAL_TRADES, "shortlist_per_symbol": SHORTLIST, "min_neighbour_share": MIN_NEIGHBOUR_SHARE},
        "presets": presets,
    }
    (RESULTS_DIR / "final_configs.json").write_text(json.dumps(payload, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
    for preset in presets:
        s = preset["selection_stats"]
        print(f"{preset['name']:28s} {preset['params']['use_ema'] and 'EMA ' or ''}{preset['params']['use_ls'] and 'LS ' or ''}SD {preset['params']['use_cp'] and 'CP' or ''}  train {s['train_trades']:.0f} tr PF {s['train_profit_factor']:.2f} Sh {s['train_sharpe_monthly']:.2f} | val {s['val_trades']:.0f} tr PF {s['val_profit_factor']:.2f} Sh {s['val_sharpe_monthly']:.2f} | neighbours {s['neighbours_profitable_pre_oos']:.0%}")


if __name__ == "__main__":
    main()
