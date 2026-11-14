"""Pick one XAUUSD configuration per timeframe, using only data before 2022.

    python -m gsms.gold_select

The rules are the ones already used for the presets (see gsms.select): supply/demand on,
both directions, at least 80 trades in 2010-2017 and 40 in 2018-2021, profitable after
costs in both periods, and at least 70% of one-step neighbours profitable before 2022.
Ranking is the worse of the train and validation monthly Sharpe ratios, never win rate.

On 1h, 2h and 4h the original default settings are kept unless a candidate passes every
rule above AND beats those defaults on that same score. A high in-sample win rate with a
handful of trades does not keep the defaults in place once they fail the rules.

Nothing in this module reads trades from 2022 onwards. Out-of-sample results are computed
later by gsms.gold_validate, which is not allowed to change the choices written here.
"""

from __future__ import annotations

import json

import pandas as pd

from . import RESULTS_DIR, search
from .params import Params
from .select import MIN_NEIGHBOUR_SHARE, MIN_TRAIN_TRADES, MIN_VAL_TRADES, _enough, _score, neighbourhood

GOLD_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1D")
# The chart timeframes the user asked to leave unchanged unless a candidate is more robust.
KEEP_ORIGINAL = ("1h", "2h", "4h")
PLATEAU_CHECKS = 8
CONFIG_PATH = RESULTS_DIR / "gold" / "timeframe_configs.json"

STAT_KEYS = (
    "train_trades", "train_wins", "train_losses", "train_win_rate", "train_profit_factor", "train_net_r",
    "train_expectancy_r", "train_max_dd_r", "train_sharpe_monthly", "train_long_trades", "train_long_net_r",
    "train_short_trades", "train_short_net_r",
    "val_trades", "val_wins", "val_losses", "val_win_rate", "val_profit_factor", "val_net_r",
    "val_expectancy_r", "val_max_dd_r", "val_sharpe_monthly", "val_long_trades", "val_long_net_r",
    "val_short_trades", "val_short_net_r",
)


def _jsonable(value):
    if hasattr(value, "item"):
        return value.item()
    return value


def _stats(row) -> dict:
    return {key: _jsonable(row[key]) for key in STAT_KEYS if key in row.index}


def _original_row(part: pd.DataFrame) -> pd.Series:
    original = part[(part["group"] == "baseline") & (part["modules"] == Params().modules())]
    if len(original) != 1:
        raise RuntimeError(f"expected one original baseline, found {len(original)}")
    return original.iloc[0]


def _plateau(symbol: str, row, known: pd.DataFrame, bars_cache: dict, budget: list[int]) -> dict:
    """Neighbourhood result from the existing short-list, or a fresh check within the budget."""
    if len(known):
        hit = known[(known["timeframe"] == row["timeframe"]) & (known["strategy_id"] == row["strategy_id"])]
        if len(hit):
            found = hit.iloc[0]
            return {
                "neighbours": int(found["neighbours"]),
                "neighbours_profitable_pre_oos": float(found["neighbours_profitable_pre_oos"]),
                "neighbours_profitable_both": float(found["neighbours_profitable_both"]),
                "plateau": bool(found["plateau"]),
                "plateau_source": "results/candidates.csv",
            }
    if budget[0] <= 0:
        return {"neighbours": 0, "neighbours_profitable_pre_oos": None, "neighbours_profitable_both": None, "plateau": False, "plateau_source": "not checked (budget spent on higher-ranked settings)"}
    budget[0] -= 1
    extra = neighbourhood(symbol, row["timeframe"], search.params_of(row), bars_cache)
    extra["plateau"] = bool(extra["neighbours_profitable_pre_oos"] >= MIN_NEIGHBOUR_SHARE)
    extra["plateau_source"] = "computed"
    print(f"  {row['timeframe']} id {int(row['strategy_id'])}: neighbours profitable {extra['neighbours_profitable_pre_oos']:.0%}", flush=True)
    return extra


def decide(timeframe: str, original_score: float, candidate: dict | None) -> dict:
    """Choose the active settings. `candidate` is None when nobody passed the rules."""
    if candidate is None:
        return {
            "status": "unoptimized",
            "active": "original",
            "chart_label": f"{timeframe} unoptimized",
            "chart_color": "color.new(color.red, 20)",
            "reason": f"{timeframe} unoptimized: no settings passed validation. Original defaults are used.",
        }
    if timeframe in KEEP_ORIGINAL and not (candidate["score"] > original_score):
        return {
            "status": "original_retained",
            "active": "original",
            "chart_label": f"{timeframe} original",
            "chart_color": "color.new(color.gray, 30)",
            "reason": f"{timeframe} kept the original settings: no candidate was more robust on 2010-2021 data.",
        }
    reason = f"{timeframe} selected on 2010-2021 data. Read GOLD_REPORT.md before trading it."
    if timeframe in KEEP_ORIGINAL:
        reason = f"{timeframe} selected: it beat the original settings on 2010-2021 robustness. Read GOLD_REPORT.md."
    return {
        "status": "passed_pre_oos_checks",
        "active": "candidate",
        "chart_label": f"{timeframe} selected",
        "chart_color": "color.new(color.gray, 30)",
        "reason": reason,
    }


def select_timeframe(part: pd.DataFrame, known: pd.DataFrame, bars_cache: dict) -> dict:
    timeframe = str(part["timeframe"].iloc[0])
    original = _original_row(part)
    original_score = float(min(original["train_sharpe_monthly"], original["val_sharpe_monthly"]))
    eligible = part[part["use_sd"] & (part["direction"] == 0) & _enough(part) & (part["train_net_r"] > 0) & (part["val_net_r"] > 0)].copy()
    eligible["score"] = _score(eligible)
    eligible = eligible.sort_values(["score", "strategy_id"], ascending=[False, True])
    budget = [PLATEAU_CHECKS]
    candidate = None
    for _, row in eligible.iterrows():
        plateau = _plateau("XAUUSD", row, known, bars_cache, budget)
        if plateau["plateau"]:
            candidate = {"strategy_id": int(row["strategy_id"]), "modules": row["modules"], "score": float(row["score"]), "params": search.params_of(row).to_dict(), "stats": _stats(row), **plateau}
            break
        if budget[0] <= 0 and plateau["plateau_source"].startswith("not checked"):
            break
    choice = decide(timeframe, original_score, candidate)
    active_params = candidate["params"] if choice["active"] == "candidate" else Params().to_dict()
    print(f"{timeframe}: eligible {len(eligible)}, status {choice['status']}", flush=True)
    return {
        "timeframe": timeframe,
        "minutes": {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1D": 1440}[timeframe],
        "tested": int(len(part)),
        "eligible": int(len(eligible)),
        "original_score": original_score,
        "original_stats": _stats(original),
        "candidate": candidate,
        "active_params": active_params,
        **choice,
    }


def main() -> None:
    frames = []
    missing = []
    for timeframe in GOLD_TIMEFRAMES:
        path = search.SEARCH_DIR / f"XAUUSD_{timeframe}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
        else:
            missing.append(timeframe)
    if not frames:
        raise SystemExit("no gold search files found")
    df = pd.concat(frames, ignore_index=True)
    known_path = RESULTS_DIR / "candidates.csv"
    known = pd.read_csv(known_path) if known_path.exists() else pd.DataFrame()
    known = known[known["symbol"] == "XAUUSD"] if len(known) else known
    bars_cache = {}
    chosen = [select_timeframe(part, known, bars_cache) for _, part in df.groupby("timeframe", sort=False)]
    order = {tf: i for i, tf in enumerate(GOLD_TIMEFRAMES)}
    chosen.sort(key=lambda row: order[row["timeframe"]])
    payload = {
        "symbol": "XAUUSD",
        "data": "Dukascopy BID quotes, repaired with Dukascopy where HistData was stale. See results/data_quality.md.",
        "selection_data": {"train": list(search.TRAIN), "validation": list(search.VALIDATION), "not_used": list(search.OOS)},
        "rules": {
            "supply_demand_required": True,
            "direction": "both",
            "min_train_trades": MIN_TRAIN_TRADES,
            "min_validation_trades": MIN_VAL_TRADES,
            "min_neighbour_share": MIN_NEIGHBOUR_SHARE,
            "rank": "min(train monthly Sharpe, validation monthly Sharpe)",
            "keep_original_unless_beaten": list(KEEP_ORIGINAL),
            "plateau_checks_per_timeframe": PLATEAU_CHECKS,
            "optuna": "Not used. The search is a fixed-seed grid plus 3,000 random draws (gsms.search), scored on train and validation separately. A sequential sampler such as Optuna would spend extra trials on in-sample winners, and those winners did not transfer between 2010-2017 and 2018-2021.",
        },
        "original_params": Params().to_dict(),
        "missing_timeframes": missing,
        "timeframes": chosen,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(payload, indent=1, default=_jsonable))
    print(f"wrote {CONFIG_PATH}")
    if missing:
        print("not in the search yet:", ", ".join(missing))


if __name__ == "__main__":
    main()
