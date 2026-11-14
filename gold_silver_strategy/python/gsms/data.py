"""Historical data: download 1-minute bars from HistData.com and build session-aligned bars.

Source
------
HistData.com free "ASCII / 1-minute bar quotes" archives (bid quotes).

Timestamps. HistData documents its files as "EST without daylight saving". For XAUUSD and
XAGUSD that is not what the files contain: the 08:30 New York data-release spike sits at
08:30 on the file clock in summer and in winter alike, so the clock does observe daylight
saving. Through 2018 it follows the US calendar (file clock = New York local time). From
2019 it follows the European calendar (file clock = Eastern European Time minus 7 hours),
which equals New York time except in the weeks when only one side of the Atlantic has
switched. `file_clock_to_utc` applies that rule; `gsms.data_checks` verifies it against
Dukascopy, whose timestamps are true UTC.

Damaged window. The 2023 archive is missing every other hour on almost every weekday from
mid-February to July 2023. That window (`dukascopy.REPAIR_WINDOW`) is replaced wholesale
with Dukascopy 1-minute bid candles; see `gsms.dukascopy`.

Bar construction
----------------
TradingView builds bars for spot metals on a New York session that opens at 18:00 and closes
at 17:00 (America/New_York, daylight saving aware). Intraday bars of 1h and above are aligned
to the 18:00 session open, the daily bar is one full session, and the weekly bar is the
Monday-Friday group of sessions. `resample` reproduces that so higher-timeframe values
(previous day / previous week high and low) mean the same thing here as in the Pine script.
"""

from __future__ import annotations

import argparse
import io
import re
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import DATA_DIR, SYMBOLS, TF_MINUTES, TIMEFRAMES, dukascopy

HISTDATA_PAGE = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{sym}/{period}"
HISTDATA_GET = "https://www.histdata.com/get.php"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

RAW_DIR = DATA_DIR / "raw" / "histdata"
M1_DIR = DATA_DIR / "m1"
BARS_DIR = DATA_DIR / "bars"

SESSION_TZ = "America/New_York"
SESSION_OPEN_HOUR = 18
# Both clock rules agree between November 2018 and March 2019, so any date in between works.
CLOCK_RULE_SWITCH = pd.Timestamp("2019-01-01")


def _download_archive(session: requests.Session, symbol: str, year: int, month: int | None) -> bytes:
    period = f"{year}" if month is None else f"{year}/{month}"
    page_url = HISTDATA_PAGE.format(sym=symbol.lower(), period=period)
    page = session.get(page_url, timeout=60)
    page.raise_for_status()
    token = re.search(r'id="tk" value="([0-9a-f]+)"', page.text)
    if token is None:
        raise RuntimeError(f"no download token on {page_url}")
    form = {
        "tk": token.group(1),
        "date": str(year),
        "datemonth": f"{year}" if month is None else f"{year}{month:02d}",
        "platform": "ASCII",
        "timeframe": "M1",
        "fxpair": symbol.upper(),
    }
    resp = session.post(HISTDATA_GET, data=form, headers={"Referer": page_url}, timeout=300)
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        raise RuntimeError(f"unexpected response for {symbol} {period} ({len(resp.content)} bytes)")
    return resp.content


def download(symbol: str, start_year: int, end_year: int, end_month: int, force_recent: bool = False) -> list[Path]:
    """Fetch yearly archives for completed years and monthly archives for `end_year`."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    jobs: list[tuple[int, int | None]] = [(y, None) for y in range(start_year, end_year)]
    jobs += [(end_year, m) for m in range(1, end_month + 1)]
    paths = []
    for year, month in jobs:
        name = f"{symbol}_{year}.zip" if month is None else f"{symbol}_{year}_{month:02d}.zip"
        path = RAW_DIR / name
        is_latest = month == end_month and year == end_year
        if path.exists() and not (force_recent and is_latest):
            paths.append(path)
            continue
        for attempt in range(4):
            try:
                content = _download_archive(session, symbol, year, month)
                path.write_bytes(content)
                print(f"  {name}: {len(content) / 1e6:.1f} MB", flush=True)
                break
            except Exception as exc:  # network hiccups: retry with backoff
                wait = 4 * 2**attempt
                print(f"  {name}: {exc!r}; retry in {wait}s", flush=True)
                time.sleep(wait)
        else:
            raise RuntimeError(f"failed to download {name}")
        paths.append(path)
        time.sleep(1.0)
    return paths


def _read_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        raw = zf.read(csv_name)
    df = pd.read_csv(
        io.BytesIO(raw),
        sep=";",
        header=None,
        names=["ts", "open", "high", "low", "close", "volume"],
        dtype={"ts": str, "open": np.float64, "high": np.float64, "low": np.float64, "close": np.float64},
    )
    df["file_time"] = pd.to_datetime(df["ts"], format="%Y%m%d %H%M%S")
    return df[["file_time", "open", "high", "low", "close"]]


def file_clock_to_utc(file_time: pd.Series) -> pd.Series:
    """Convert the HistData file clock to UTC (see the module docstring for the evidence)."""

    def convert(ts: pd.Series, shift_hours: int, zone: str) -> pd.Series:
        local = (ts + pd.Timedelta(hours=shift_hours)).dt.tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
        return local.dt.tz_convert("UTC").dt.tz_localize(None)

    us_rule = file_time < CLOCK_RULE_SWITCH
    utc = pd.concat(
        [
            convert(file_time[us_rule], 0, "America/New_York"),
            convert(file_time[~us_rule], 7, "Europe/Athens"),
        ]
    )
    return utc.reindex(file_time.index)


def read_histdata(symbol: str) -> pd.DataFrame:
    """All downloaded HistData archives of `symbol` as one UTC 1-minute frame (unrepaired)."""
    files = sorted(RAW_DIR.glob(f"{symbol}_*.zip"))
    if not files:
        raise FileNotFoundError(f"no archives for {symbol} in {RAW_DIR}")
    df = pd.concat([_read_archive(p) for p in files], ignore_index=True)
    df["time"] = file_clock_to_utc(df["file_time"])
    unresolved = int(df["time"].isna().sum())
    if unresolved:
        print(f"  {symbol}: dropping {unresolved} rows whose clock time does not exist or is ambiguous")
    df = df.dropna(subset=["time"])
    return df[["time", "open", "high", "low", "close"]]


def read_repair(symbol: str) -> tuple[pd.DataFrame, list]:
    """Dukascopy bid candles for the repair window, plus the days that are not on disk."""
    frames, missing = [], []
    for day in dukascopy.repair_days():
        frame = dukascopy.load_day(symbol, day, "BID")
        if frame is None:
            missing.append(day)
        elif len(frame):
            frames.append(frame[["time", "open", "high", "low", "close"]])
    repaired = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["time", "open", "high", "low", "close"])
    return repaired, missing


def build_m1(symbol: str) -> pd.DataFrame:
    """Merge HistData archives and the Dukascopy repair window into one cleaned UTC 1-minute frame."""
    df = read_histdata(symbol)
    repaired, missing = read_repair(symbol)
    if missing:
        # A day that could not be fetched keeps its (holed) HistData rows rather than being dropped.
        print(f"  {symbol}: {len(missing)} repair-window days not downloaded; HistData kept for those days")
    if len(repaired):
        replaced_days = repaired["time"].dt.normalize().unique()
        stale = df["time"].dt.normalize().isin(replaced_days)
        print(f"  {symbol}: repair window: {int(stale.sum()):,} HistData rows replaced by {len(repaired):,} Dukascopy rows")
        df = pd.concat([df[~stale], repaired], ignore_index=True)
    df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    ohlc = df[["open", "high", "low", "close"]]
    bad = (
        (ohlc <= 0).any(axis=1)
        | (df["high"] < ohlc[["open", "close"]].max(axis=1))
        | (df["low"] > ohlc[["open", "close"]].min(axis=1))
    )
    if bad.any():
        print(f"  {symbol}: dropping {int(bad.sum())} malformed 1-minute rows")
        df = df[~bad].reset_index(drop=True)
    M1_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(M1_DIR / f"{symbol}.parquet", index=False)
    return df


def load_m1(symbol: str) -> pd.DataFrame:
    return pd.read_parquet(M1_DIR / f"{symbol}.parquet")


def session_fields(time_utc: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (session_date, minutes_since_session_open) for UTC timestamps.

    A session runs 18:00 -> 17:00 New York time and is dated by the day it closes on,
    so the Sunday-evening open belongs to Monday's session.
    """
    local = time_utc.dt.tz_localize("UTC").dt.tz_convert(SESSION_TZ)
    shifted = local + pd.Timedelta(hours=24 - SESSION_OPEN_HOUR)
    session_date = shifted.dt.tz_localize(None).dt.normalize()
    minutes = shifted.dt.hour * 60 + shifted.dt.minute
    return session_date, minutes


def resample(m1: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate 1-minute bars into `timeframe` bars aligned to the New York session open.

    Output columns: time (UTC bar open), open, high, low, close, session (session date),
    week (Monday of the session's week).
    """
    minutes_per_bar = TF_MINUTES[timeframe]
    session_date, minutes = session_fields(m1["time"])
    bucket = (minutes // minutes_per_bar).astype(np.int64)
    work = m1.assign(session=session_date.values, bucket=bucket.values)
    # A few 2009-2010 weeks start with a handful of quotes before the Sunday 18:00 open. They
    # would form a bogus one-hour "Sunday session" (and a bogus daily bar), so they are dropped.
    work = work[work["session"].dt.day_of_week < 5]
    bars = (
        work.groupby(["session", "bucket"], sort=True)
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        .reset_index()
    )
    # Label every bar with its nominal open: session open (18:00 New York on the previous
    # calendar day) plus the bucket offset, expressed in UTC.
    open_local = (
        bars["session"]
        - pd.Timedelta(hours=24 - SESSION_OPEN_HOUR)
        + pd.to_timedelta(bars["bucket"] * minutes_per_bar, unit="m")
    )
    bars["time"] = (
        open_local.dt.tz_localize(SESSION_TZ, ambiguous="NaT", nonexistent="shift_forward")
        .dt.tz_convert("UTC")
        .dt.tz_localize(None)
    )
    bars["week"] = bars["session"] - pd.to_timedelta(bars["session"].dt.day_of_week, unit="D")
    bars = bars.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    return bars[["time", "open", "high", "low", "close", "session", "week"]]


def build_bars(symbol: str) -> None:
    m1 = load_m1(symbol)
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    for tf in TIMEFRAMES:
        bars = resample(m1, tf)
        bars.to_parquet(BARS_DIR / f"{symbol}_{tf}.parquet", index=False)
        print(f"  {symbol} {tf}: {len(bars):,} bars  {bars['time'].iloc[0]} -> {bars['time'].iloc[-1]}")


def load_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    return pd.read_parquet(BARS_DIR / f"{symbol}_{timeframe}.parquet")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--start-year", type=int, default=2009)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--end-month", type=int, required=True, help="last month to fetch in --end-year")
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()
    for symbol in args.symbols:
        if not args.skip_download:
            print(f"downloading {symbol}")
            download(symbol, args.start_year, args.end_year, args.end_month)
        print(f"building 1-minute series for {symbol}")
        m1 = build_m1(symbol)
        print(f"  {len(m1):,} rows  {m1['time'].iloc[0]} -> {m1['time'].iloc[-1]}")
        build_bars(symbol)


if __name__ == "__main__":
    main()
