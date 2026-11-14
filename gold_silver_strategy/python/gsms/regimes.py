"""Market-regime labels per trading session, used to break results down by market condition.

Labels a trader could know before the session starts (computed from earlier sessions only):
  trend       bull / bear / sideways   close vs 100-day SMA, 20-day vs 100-day SMA, ADX(14) >= 20
  volatility  high / normal / low      ATR(14) % of price, ranked within the previous 252 sessions
Labels that describe the session itself (after the fact, for analysis only):
  nfp         first Friday of the month (US employment report; the calendar rule is approximate)
  shock       a scheduled-release shock: the five minutes after 08:30 or 14:00 New York time
              ranged at least 3x their median over the previous 60 sessions (data-defined; it
              stands in for a full economic calendar, which was not available offline)
  reversal    the session made a new 10-day extreme and closed back through the previous close
              with a range of at least 1.2x ATR
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import data


def _wilder(values: pd.Series, length: int) -> pd.Series:
    return values.ewm(alpha=1.0 / length, adjust=False).mean()


def _adx(d: pd.DataFrame, length: int = 14) -> pd.Series:
    up, down = d["high"].diff(), -d["low"].diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=d.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=d.index)
    prev = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1).max(axis=1)
    atr = _wilder(tr, length)
    plus_di, minus_di = 100 * _wilder(plus, length) / atr, 100 * _wilder(minus, length) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder(dx.fillna(0.0), length)


def _release_shock(symbol: str) -> pd.Series:
    m1 = data.load_m1(symbol)
    local = m1["time"].dt.tz_localize("UTC").dt.tz_convert(data.SESSION_TZ)
    minute = local.dt.hour * 60 + local.dt.minute
    session, _ = data.session_fields(m1["time"])
    flags = []
    for start in (8 * 60 + 30, 14 * 60):
        window = (minute >= start) & (minute < start + 5)
        grouped = m1[window].groupby(session[window])
        rng = grouped["high"].max() - grouped["low"].min()
        usual = rng.rolling(60, min_periods=20).median().shift(1)
        flags.append(rng >= 3.0 * usual)
    return pd.concat(flags, axis=1).fillna(False).any(axis=1)


def daily_regimes(symbol: str) -> pd.DataFrame:
    """One row per session date with columns trend, volatility, nfp, shock, reversal."""
    d = data.load_bars(symbol, "1D").set_index("session")
    close = d["close"]
    sma20, sma100, adx = close.rolling(20).mean(), close.rolling(100).mean(), _adx(d)
    trend = pd.Series("sideways", index=d.index)
    trend[(close > sma100) & (sma20 > sma100) & (adx >= 20)] = "bull"
    trend[(close < sma100) & (sma20 < sma100) & (adx >= 20)] = "bear"
    trend[sma100.isna()] = "unknown"

    prev = close.shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1).max(axis=1)
    atr = _wilder(tr, 14)
    rank = (atr / close).rolling(252, min_periods=120).rank(pct=True)
    vol = pd.Series("normal", index=d.index)
    vol[rank >= 0.7] = "high"
    vol[rank <= 0.3] = "low"
    vol[rank.isna()] = "unknown"

    new_high = d["high"] >= d["high"].shift(1).rolling(10).max()
    new_low = d["low"] <= d["low"].shift(1).rolling(10).min()
    wide = (d["high"] - d["low"]) >= 1.2 * atr.shift(1)
    reversal = wide & ((new_high & (close < prev)) | (new_low & (close > prev)))

    dates = d.index
    nfp = pd.Series((dates.day_of_week == 4) & (dates.day <= 7), index=dates)
    out = pd.DataFrame({
        "trend": trend.shift(1).fillna("unknown"),
        "volatility": vol.shift(1).fillna("unknown"),
        "nfp": nfp,
        "shock": _release_shock(symbol).reindex(dates).fillna(False).astype(bool),
        "reversal": reversal.fillna(False),
    })  # fmt: skip
    return out


def label_trades(symbol: str, entry_time: np.ndarray, regimes: pd.DataFrame | None = None) -> pd.DataFrame:
    """Regime labels of the session each trade was entered in."""
    regimes = daily_regimes(symbol) if regimes is None else regimes
    session, _ = data.session_fields(pd.Series(pd.to_datetime(entry_time)))
    return regimes.reindex(session.to_numpy()).reset_index(drop=True)
