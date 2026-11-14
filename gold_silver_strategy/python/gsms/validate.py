"""Stage 7: out-of-sample and walk-forward validation of the final configurations.

    python -m gsms.validate

This is the only place where the out-of-sample period (search.OOS) is simulated. It runs
after results/final_configs.json has been written and does not change it.

For every preset:
  final_performance.csv      train / validation / out-of-sample / whole history, at 0x, 0.5x,
                             1x and 2x the modelled trading cost
  final_yearly.csv           year by year
  final_regimes.csv          by trend, volatility, news and reversal sessions
  final_random_baseline.csv  the same exits, direction, filters and trade frequency with random
                             entries: does the entry logic add anything?
  final_bootstrap.csv        bootstrap confidence interval of the expectancy
  final_trades/<preset>.csv  every trade
and per preset dataset:
  walk_forward.csv           re-optimise on 4 years, trade the next year, roll forward
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import RESULTS_DIR, backtest, data, metrics, regimes, search, space
from .params import Params

ALL = (search.TRAIN[0], search.OOS[1])
PERIODS = {"train": search.TRAIN, "validation": search.VALIDATION, "out_of_sample": search.OOS, "all": ALL}
COST_MULTIPLES = (0.0, 0.5, 1.0, 2.0)
RANDOM_RUNS = 300
BOOTSTRAP_RUNS = 10000
WF_TRAIN_YEARS = 4
WF_FIRST_TEST_YEAR = 2014
WF_MIN_TRADES = 60


def load_presets() -> list[dict]:
    return json.loads((RESULTS_DIR / "final_configs.json").read_text())["presets"]


def _bars(symbol: str, timeframe: str, cache: dict) -> backtest.Bars:
    key = (symbol, timeframe)
    if key not in cache:
        cache[key] = backtest.Bars(data.load_bars(symbol, timeframe), symbol, timeframe, cache_limit=4)
    return cache[key]


def _stats(r: np.ndarray) -> dict:
    wins, losses = r[r > 0], r[r <= 0]
    return {
        "trades": len(r), "win_rate": len(wins) / len(r) if len(r) else np.nan, "expectancy_r": r.mean() if len(r) else np.nan,
        "net_r": r.sum(), "profit_factor": wins.sum() / -losses.sum() if len(losses) and losses.sum() < 0 else np.nan,
    }  # fmt: skip


def performance(preset: dict, bars: backtest.Bars) -> tuple[list[dict], backtest.Trades]:
    p = Params.from_dict(preset["params"])
    base = backtest.COST_PCT_PER_SIDE[bars.symbol]
    rows, kept = [], None
    for multiple in COST_MULTIPLES:
        trades = backtest.run(bars, p, cost_pct_per_side=base * multiple, start=ALL[0])
        if multiple == 1.0:
            kept = trades
        for label, (start, end) in PERIODS.items():
            rows.append({"preset": preset["name"], "symbol": bars.symbol, "timeframe": bars.timeframe, "period": label, "from": start, "to": end, "cost_multiple": multiple, **metrics.summarize(trades, start, end)})
    return rows, kept


def yearly(preset: dict, trades: backtest.Trades) -> list[dict]:
    years = trades.entry_time.astype("datetime64[Y]").astype(int) + 1970
    return [{"preset": preset["name"], "year": int(y), **_stats(trades.r[years == y])} for y in np.unique(years)]


def by_regime(preset: dict, trades: backtest.Trades, table: pd.DataFrame) -> list[dict]:
    labels = regimes.label_trades(preset["symbol"], trades.entry_time, table)
    oos = trades.entry_time >= np.datetime64(search.OOS[0])
    rows = []
    for scope, mask in (("all", np.ones(len(trades), bool)), ("out_of_sample", oos)):
        for column in ("trend", "volatility", "nfp", "shock", "reversal"):
            for value in sorted(labels[column].dropna().unique(), key=str):
                pick = mask & (labels[column] == value).to_numpy()
                if pick.sum():
                    rows.append({"preset": preset["name"], "scope": scope, "regime": column, "value": str(value), **_stats(trades.r[pick])})
    return rows


def random_baseline(preset: dict, bars: backtest.Bars, trades: backtest.Trades, seed: int = 5) -> list[dict]:
    """Random entries pushed through the preset's own exits, direction, trend and session filters."""
    p = Params.from_dict(preset["params"])
    q = space.normalize(p.replace(use_ema=False, use_ls=False, use_sd=False, use_cp=True, conf_window=1))
    rng = np.random.default_rng(seed)
    n = len(bars)
    long_share = float((trades.direction > 0).mean()) if len(trades) else 0.5

    def draw(rate: float):
        fire = rng.random(n) < rate
        is_long = rng.random(n) < long_share
        return backtest.run(bars, q, signals=(fire & is_long, fire & ~is_long), start=ALL[0])

    # Match the trade frequency: one position at a time blocks signals, so the rate is tuned.
    rate = max(len(trades), 1) / n
    for _ in range(4):
        got = np.mean([len(draw(rate)) for _ in range(3)])
        rate = min(rate * len(trades) / max(got, 1.0), 1.0)
    sims = [draw(rate) for _ in range(RANDOM_RUNS)]
    rows = []
    for label, (start, end) in PERIODS.items():
        actual = metrics.summarize(trades, start, end)
        expectancy = np.array([metrics.summarize(s, start, end)["expectancy_r"] for s in sims])
        net = np.array([metrics.summarize(s, start, end)["net_r"] for s in sims])
        count = np.array([metrics.summarize(s, start, end)["trades"] for s in sims])
        rows.append({
            "preset": preset["name"], "period": label, "random_runs": RANDOM_RUNS,
            "trades": actual["trades"], "random_trades_mean": count.mean(),
            "expectancy_r": actual["expectancy_r"], "random_expectancy_mean": expectancy.mean(),
            "random_expectancy_p05": np.quantile(expectancy, 0.05), "random_expectancy_p95": np.quantile(expectancy, 0.95),
            "net_r": actual["net_r"], "random_net_r_mean": net.mean(), "random_net_r_p95": np.quantile(net, 0.95),
            "share_of_random_runs_beaten": float((expectancy < actual["expectancy_r"]).mean()),
        })  # fmt: skip
    return rows


def bootstrap(preset: dict, trades: backtest.Trades, seed: int = 9) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for label, (start, end) in PERIODS.items():
        r = trades.between(start, end).r
        if len(r) < 5:
            continue
        means = rng.choice(r, size=(BOOTSTRAP_RUNS, len(r)), replace=True).mean(axis=1)
        rows.append({
            "preset": preset["name"], "period": label, "trades": len(r), "expectancy_r": r.mean(),
            "ci95_low": np.quantile(means, 0.025), "ci95_high": np.quantile(means, 0.975), "prob_expectancy_not_positive": float((means <= 0).mean()),
        })  # fmt: skip
    return rows


# ───────────────────────────── walk-forward ─────────────────────────────
def _monthly(symbol: str, timeframe: str, n_random: int, seed: int):
    """Monthly net R and trade counts over the whole history for every supply/demand strategy."""
    bars = backtest.Bars(data.load_bars(symbol, timeframe), symbol, timeframe, cache_limit=3)
    strategies = [(i, p) for i, (_, p) in enumerate(search.strategy_list(n_random, seed)) if p.use_sd]
    start = np.datetime64(ALL[0])
    n_months = int(np.datetime64(ALL[1], "M").astype(int) - np.datetime64(ALL[0], "M").astype(int)) + 1
    net = np.zeros((len(strategies), n_months), np.float32)
    count = np.zeros((len(strategies), n_months), np.int32)
    for row, (_, p) in enumerate(strategies):
        trades = backtest.run(bars, p, start=ALL[0])
        month = metrics.month_index(trades.entry_time, start)
        np.add.at(net[row], month, trades.r)
        np.add.at(count[row], month, 1)
    return symbol, timeframe, np.array([i for i, _ in strategies]), net, count


def _sharpe(monthly: np.ndarray) -> np.ndarray:
    std = monthly.std(axis=1)
    return np.where(std > 0, monthly.mean(axis=1) / np.where(std > 0, std, 1.0) * np.sqrt(12.0), 0.0)


def walk_forward(datasets: list[tuple[str, str]], n_random: int, seed: int, workers: int = 4) -> pd.DataFrame:
    first_month = np.datetime64(ALL[0], "M").astype(int)
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for symbol, timeframe, ids, net, count in pool.map(_monthly, *zip(*[(s, tf, n_random, seed) for s, tf in datasets])):
            last_year = int(str(np.datetime64(ALL[1], "Y")))
            for year in range(WF_FIRST_TEST_YEAR, last_year + 1):
                a = np.datetime64(f"{year - WF_TRAIN_YEARS}-01", "M").astype(int) - first_month
                b = np.datetime64(f"{year}-01", "M").astype(int) - first_month
                c = min(np.datetime64(f"{year + 1}-01", "M").astype(int) - first_month, net.shape[1])
                fit_net, fit_count = net[:, a:b], count[:, a:b].sum(axis=1)
                score = _sharpe(fit_net)
                score[(fit_count < WF_MIN_TRADES) | (fit_net.sum(axis=1) <= 0)] = -np.inf
                order = np.argsort(-score)
                test = net[:, b:c].sum(axis=1)
                eligible = int(np.isfinite(score).sum())
                best, top10 = order[0], order[: min(10, max(eligible, 1))]
                rows.append({
                    "symbol": symbol, "timeframe": timeframe, "test_year": year, "fit_from": year - WF_TRAIN_YEARS, "fit_to": year - 1,
                    "pool": len(ids), "eligible_in_fit": eligible,
                    "chosen_strategy_id": int(ids[best]) if eligible else -1, "chosen_fit_sharpe": float(score[best]) if eligible else np.nan,
                    "chosen_test_trades": int(count[best, b:c].sum()) if eligible else 0, "chosen_test_net_r": float(test[best]) if eligible else 0.0,
                    "top10_test_net_r_mean": float(test[top10].mean()) if eligible else 0.0,
                    "pool_test_net_r_median": float(np.median(test)), "pool_share_profitable_in_test": float((test > 0).mean()),
                })  # fmt: skip
    return pd.DataFrame(rows)


def main() -> None:
    presets = load_presets()
    cache, tables = {}, {}
    perf, years, regime_rows, random_rows, boot = [], [], [], [], []
    (RESULTS_DIR / "final_trades").mkdir(parents=True, exist_ok=True)
    for preset in presets:
        bars = _bars(preset["symbol"], preset["timeframe"], cache)
        if preset["symbol"] not in tables:
            tables[preset["symbol"]] = regimes.daily_regimes(preset["symbol"])
        rows, trades = performance(preset, bars)
        perf += rows
        years += yearly(preset, trades)
        regime_rows += by_regime(preset, trades, tables[preset["symbol"]])
        random_rows += random_baseline(preset, bars, trades)
        boot += bootstrap(preset, trades)
        frame = trades.frame()
        frame = pd.concat([frame, regimes.label_trades(preset["symbol"], trades.entry_time, tables[preset["symbol"]])], axis=1)
        frame.to_csv(RESULTS_DIR / "final_trades" / f"{preset['name'].replace(' ', '_')}.csv", index=False, float_format="%.6f")
        oos = next(r for r in rows if r["period"] == "out_of_sample" and r["cost_multiple"] == 1.0)
        print(f"{preset['name']:28s} OOS: {oos['trades']} trades, win {oos['win_rate']:.1%}, PF {oos['profit_factor']:.2f}, net {oos['net_r']:+.1f} R, max DD {oos['max_dd_r']:.1f} R, Sharpe {oos['sharpe_monthly']:.2f}", flush=True)

    for name, rows in (("final_performance", perf), ("final_yearly", years), ("final_regimes", regime_rows), ("final_random_baseline", random_rows), ("final_bootstrap", boot)):
        pd.DataFrame(rows).to_csv(RESULTS_DIR / f"{name}.csv", index=False, float_format="%.5f")

    datasets = sorted({(preset["symbol"], preset["timeframe"]) for preset in presets})
    n_random = int(search.load([datasets[0][0]], [datasets[0][1]])["group"].eq("random").sum())
    wf = walk_forward(datasets, n_random, 11)
    wf.to_csv(RESULTS_DIR / "walk_forward.csv", index=False, float_format="%.5f")
    for (symbol, timeframe), part in wf.groupby(["symbol", "timeframe"]):
        print(f"walk-forward {symbol} {timeframe}: chosen {part['chosen_test_net_r'].sum():+.1f} R over {len(part)} test years ({(part['chosen_test_net_r'] > 0).sum()} profitable), top-10 mean {part['top10_test_net_r_mean'].sum():+.1f} R, pool median {part['pool_test_net_r_median'].sum():+.1f} R")


if __name__ == "__main__":
    main()
