"""Stage 5: module study and parameter search on the TRAIN and VALIDATION periods.

    python -m gsms.search --random 3000 --workers 4

The same list of strategies is run on every symbol and timeframe, so timeframes and assets
can be compared like for like:
  * baseline   - the 15 module combinations at default settings
  * ema_grid   - fast 5/7/9/12/15 x slow 15/18/20/25/30, EMA alone and EMA + supply/demand
  * random     - random draws from the whole parameter space (gsms.space); 60% of them are
                 forced to include the supply/demand module

Nothing after OOS_START is ever simulated here: the engine stops taking signals at that date,
so the out-of-sample period cannot influence which settings are chosen.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import fields

import numpy as np
import pandas as pd

from . import RESULTS_DIR, SYMBOLS, TIMEFRAMES, backtest, data, metrics, space
from . import params as P
from .params import Params

TRAIN = ("2010-01-01", "2018-01-01")
VALIDATION = ("2018-01-01", "2022-01-01")
OOS = ("2022-01-01", "2026-11-07")
PRE_OOS = (TRAIN[0], VALIDATION[1])
OOS_START = OOS[0]

SEARCH_DIR = RESULTS_DIR / "search"
PARAM_FIELDS = [f.name for f in fields(Params)]
KEEP = (
    "trades", "win_rate", "profit_factor", "net_r", "expectancy_r", "max_dd_r", "payoff", "avg_win_r", "avg_loss_r",
    "max_consec_losses", "long_trades", "long_net_r", "short_trades", "short_net_r",
    "t_stat", "sharpe_monthly", "pos_months", "trades_per_year", "net_r_per_year", "avg_cost_r", "gross_expectancy_r",
)  # fmt: skip


def strategy_list(n_random: int, seed: int = 11) -> list[tuple[str, Params]]:
    """(group, strategy) pairs, de-duplicated; identical for every symbol and timeframe."""
    out: dict[Params, str] = {}
    base = Params()
    for subset in space.SUBSETS:
        out.setdefault(space.normalize(space.with_modules(base, subset)), "baseline")
    for subset in (("ema",), ("ema", "sd")):
        for fast in space.SPACE["ema_fast"]:
            for slow in space.SPACE["ema_slow"]:
                p = space.normalize(space.with_modules(base.replace(ema_fast=fast, ema_slow=slow), subset))
                if space.is_valid(p):
                    out.setdefault(p, "ema_grid")
    rng = np.random.default_rng(seed)
    target = len(out) + n_random
    while len(out) < target:
        require = ("sd",) if rng.random() < 0.6 else ()
        out.setdefault(space.sample(rng, require=require), "random")
    return [(group, p) for p, group in out.items()]


def evaluate(bars: backtest.Bars, p: Params, end: str = OOS_START) -> dict:
    """Train and validation statistics of one strategy; signals stop at `end`."""
    trades = backtest.run(bars, p, start=TRAIN[0], end=end)
    row = {}
    for label, (start, stop) in (("train", TRAIN), ("val", VALIDATION), ("pre", PRE_OOS)):
        stats = metrics.summarize(trades, start, stop)
        for name in KEEP:
            row[f"{label}_{name}"] = stats[name]
    return row


def run_dataset(symbol: str, timeframe: str, n_random: int, seed: int) -> str:
    path = SEARCH_DIR / f"{symbol}_{timeframe}.parquet"
    strategies = strategy_list(n_random, seed)
    bars = backtest.Bars(data.load_bars(symbol, timeframe), symbol, timeframe, cache_limit=3)
    rows = []
    started = time.time()
    for index, (group, p) in enumerate(strategies):
        row = {"symbol": symbol, "timeframe": timeframe, "strategy_id": index, "group": group, "modules": p.modules(), **p.to_dict()}
        row.update(evaluate(bars, p))
        rows.append(row)
        if (index + 1) % 500 == 0:
            print(f"  {symbol} {timeframe}: {index + 1}/{len(strategies)} in {time.time() - started:.0f}s", flush=True)
    frame = pd.DataFrame(rows)
    frame.to_parquet(path, index=False)
    return f"{symbol} {timeframe}: {len(frame)} strategies in {time.time() - started:.0f}s -> {path.name}"


def load(symbols=SYMBOLS, timeframes=TIMEFRAMES) -> pd.DataFrame:
    frames = [pd.read_parquet(SEARCH_DIR / f"{s}_{tf}.parquet") for s in symbols for tf in timeframes if (SEARCH_DIR / f"{s}_{tf}.parquet").exists()]
    return pd.concat(frames, ignore_index=True)


def params_of(row) -> Params:
    return Params.from_dict({name: row[name] for name in PARAM_FIELDS})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--random", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--symbols", nargs="*", default=list(SYMBOLS))
    parser.add_argument("--timeframes", nargs="*", default=list(TIMEFRAMES))
    args = parser.parse_args()

    SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    # Largest datasets first so the long jobs are not left for the end.
    jobs = [(s, tf) for tf in args.timeframes for s in args.symbols]
    print(f"{len(strategy_list(args.random, args.seed))} strategies x {len(jobs)} datasets", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_dataset, s, tf, args.random, args.seed) for s, tf in jobs]
        for future in as_completed(futures):
            print(future.result(), flush=True)


if __name__ == "__main__":
    main()
