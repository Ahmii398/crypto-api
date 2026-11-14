"""Run the engine on a bar series: data preparation, per-module caching, trade records."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import engine
from .params import Params

# Trading cost, charged as a percentage of traded value on each fill. This is exactly how the
# Pine strategy charges it (commission_type = percent), so the two can be compared directly.
#
# The price series are BID quotes, so the bid/ask spread has to be paid on top. The model is
# half the quoted spread per fill plus an allowance for commission and slippage on market and
# stop fills. Spreads were measured on Dukascopy BID/ASK 1-minute quotes on sampled days from
# 2010 to 2026 (results/data_quality.md):
#   XAUUSD: median quoted spread 2.1 bp of price (1.4-1.9 bp since 2017, 4.2 bp in 2010)
#           1.05 bp half spread + 0.70 bp commission and slippage = 1.75 bp per fill
#   XAGUSD: median quoted spread 13.6 bp (6-13 bp since 2020, 15-22 bp in 2013-2019)
#           6.80 bp half spread + 1.20 bp commission and slippage = 8.00 bp per fill
# The spread was wider in the early years than this constant for silver; every final result is
# therefore also reported at half and at double these costs.
COST_PCT_PER_SIDE = {"XAUUSD": 0.0175, "XAGUSD": 0.080}


class _Cache(OrderedDict):
    """Small least-recently-used cache; module outputs on 5-minute data are tens of MB each."""

    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    def fetch(self, key, build):
        if key in self:
            self.move_to_end(key)
            return self[key]
        value = build()
        self[key] = value
        if len(self) > self.limit:
            self.popitem(last=False)
        return value


class Bars:
    """OHLC arrays plus the higher-timeframe references the modules need."""

    def __init__(self, frame: pd.DataFrame, symbol: str, timeframe: str, cache_limit: int = 24):
        self.symbol = symbol
        self.timeframe = timeframe
        self.time = frame["time"].to_numpy("datetime64[ns]")
        self.o = np.ascontiguousarray(frame["open"].to_numpy(np.float64))
        self.h = np.ascontiguousarray(frame["high"].to_numpy(np.float64))
        self.l = np.ascontiguousarray(frame["low"].to_numpy(np.float64))
        self.c = np.ascontiguousarray(frame["close"].to_numpy(np.float64))
        self.hour = frame["time"].dt.hour.to_numpy(np.int64)
        self.new_day, self.pdh, self.pdl = self._previous_period(frame["session"].to_numpy())
        self.new_week, self.pwh, self.pwl = self._previous_period(frame["week"].to_numpy())
        self._ema = _Cache(64)
        self._atr = _Cache(8)
        self._ls = _Cache(cache_limit)
        self._sd = _Cache(cache_limit)
        self._cp = _Cache(cache_limit)
        self._none = np.zeros(len(self.c), np.bool_)
        self._nan = np.full(len(self.c), np.nan)

    def __len__(self) -> int:
        return len(self.c)

    def _previous_period(self, period_id: np.ndarray):
        """First-bar flags of each period and the previous period's high/low on every bar."""
        new = np.r_[True, period_id[1:] != period_id[:-1]]
        starts = np.flatnonzero(new)
        index = np.cumsum(new) - 1
        prev_high = np.r_[np.nan, np.maximum.reduceat(self.h, starts)[:-1]][index]
        prev_low = np.r_[np.nan, np.minimum.reduceat(self.l, starts)[:-1]][index]
        return new, prev_high, prev_low

    def ema(self, length: int) -> np.ndarray:
        return self._ema.fetch(length, lambda: engine.ema(self.c, length))

    def atr(self, length: int) -> np.ndarray:
        return self._atr.fetch(length, lambda: engine.atr(self.h, self.l, self.c, length))

    def liquidity(self, p: Params):
        return self._ls.fetch(
            p.ls_key(),
            lambda: engine.liquidity_sweeps(
                self.o, self.h, self.l, self.c, self.new_day, self.new_week,
                self.pdh, self.pdl, self.pwh, self.pwl,
                p.ls_swing, p.ls_source, p.ls_mode, p.ls_reclaim, p.ls_max_age,
            ),
        )  # fmt: skip

    def zones(self, p: Params):
        return self._sd.fetch(
            p.sd_key(),
            lambda: engine.supply_demand(
                self.o, self.h, self.l, self.c, self.atr(p.atr_len),
                p.sd_swing, p.sd_width, p.sd_strength, p.sd_max_touch, p.sd_inval, p.sd_max_age,
            ),
        )  # fmt: skip

    def candles(self, p: Params):
        return self._cp.fetch(
            p.cp_key(),
            lambda: engine.candle_patterns(self.o, self.h, self.l, self.c, self.atr(p.atr_len), p.cp_mask, p.cp_strict, p.cp_confirm),
        )


@dataclass
class Trades:
    signal_bar: np.ndarray
    entry_bar: np.ndarray
    exit_bar: np.ndarray
    direction: np.ndarray
    entry: np.ndarray
    exit: np.ndarray
    stop_dist: np.ndarray
    reason: np.ndarray
    entry_time: np.ndarray
    exit_time: np.ndarray
    r_gross: np.ndarray
    cost_r: np.ndarray

    @property
    def r(self) -> np.ndarray:
        """Net result of each trade in units of the risk taken (R-multiples)."""
        return self.r_gross - self.cost_r

    def __len__(self) -> int:
        return len(self.entry)

    def between(self, start, end) -> "Trades":
        """Trades entered in [start, end)."""
        keep = (self.entry_time >= np.datetime64(start)) & (self.entry_time < np.datetime64(end))
        return self.select(keep)

    def select(self, keep: np.ndarray) -> "Trades":
        return Trades(**{name: getattr(self, name)[keep] for name in self.__dataclass_fields__})

    def frame(self) -> pd.DataFrame:
        data = {name: getattr(self, name) for name in self.__dataclass_fields__}
        data["r"] = self.r
        return pd.DataFrame(data)


def run(
    bars: Bars,
    p: Params,
    cost_pct_per_side: float | None = None,
    signals: tuple | None = None,
    start=None,
    end=None,
    tick: float = 0.0,
) -> Trades:
    """Backtest `p` on `bars`.

    `signals`, when given, replaces the candlestick module's (bull, bear) arrays; the
    random-entry baseline uses it to push arbitrary entries through the same trade management.
    `start` / `end` restrict signals to bars opening in [start, end), like the Pine strategy's
    date window. `tick` rounds order prices to the symbol's price tick (TradingView parity).
    """
    if cost_pct_per_side is None:
        cost_pct_per_side = COST_PCT_PER_SIDE[bars.symbol]
    none, nan = bars._none, bars._nan
    ls = bars.liquidity(p) if p.use_ls else (none, none)
    sd = bars.zones(p) if p.use_sd else (none, none, nan, none, none, none, nan, none)
    cp = signals if signals is not None else (bars.candles(p) if p.use_cp else (none, none))
    trend = bars.ema(p.trend_len) if p.trend_len > 0 else nan
    first_bar = p.warmup if start is None else max(p.warmup, int(np.searchsorted(bars.time, np.datetime64(start))))
    last_bar = len(bars) if end is None else int(np.searchsorted(bars.time, np.datetime64(end)))
    out = engine.simulate(
        bars.o, bars.h, bars.l, bars.c, bars.atr(p.atr_len), bars.hour,
        bars.ema(p.ema_fast), bars.ema(p.ema_slow), trend,
        ls[0], ls[1], *sd, cp[0], cp[1],
        p.use_ema, p.use_ls, p.use_sd, p.use_cp, p.conf_window, p.ema_mode, p.sd_confirm,
        p.sl_mode, p.sl_atr, p.sl_lookback, p.sl_buffer, p.min_stop, p.max_stop, p.rr,
        p.direction, p.trend_len > 0, p.sess_start, p.sess_end, first_bar, last_bar, tick,
    )  # fmt: skip
    signal_bar, entry_bar, exit_bar, direction, entry, exit_, dist, reason = out
    r_gross = direction * (exit_ - entry) / dist
    cost_r = cost_pct_per_side / 100.0 * (entry + exit_) / dist
    return Trades(
        signal_bar, entry_bar, exit_bar, direction, entry, exit_, dist, reason,
        bars.time[entry_bar], bars.time[exit_bar], r_gross, cost_r,
    )  # fmt: skip
