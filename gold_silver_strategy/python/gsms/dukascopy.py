"""Dukascopy 1-minute candles: repair source for a damaged HistData window, and spread samples.

The Dukascopy public datafeed (true UTC timestamps, separate BID and ASK candles) answers
slowly, 10-40 s per daily file, and sometimes refuses connections, so it is not used for the
bulk history. It is used for two bounded jobs:

``repair``  BID candles for every UTC day of ``REPAIR_WINDOW``. HistData.com's 2023 archive is
            missing every other hour on almost every weekday between mid-February and July
            2023; `gsms.data.build_m1` swaps that window for the Dukascopy series.
``sample``  BID and ASK candles for a few mid-week days per year, used to measure the real
            quoted spread (ASK close - BID close) for the cost model.
``clock``   BID candles for four diagnostic days per year, used by `gsms.data_checks` to
            verify the HistData timestamp conversion in every year.

HistData's XAUUSD/XAGUSD prices turn out to be identical to Dukascopy's to the last decimal,
so the two are the same feed: the repair leaves no seam, and Dukascopy validates HistData's
timestamps but is not an independent check on price levels.

Days that cannot be fetched are reported and left out. Nothing is interpolated or invented.
"""

from __future__ import annotations

import argparse
import lzma
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import DATA_DIR, SYMBOLS

URL = "https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m:02d}/{d:02d}/{side}_candles_min_1.bi5"
RAW_DIR = DATA_DIR / "raw" / "dukascopy"
REPAIR_WINDOW = (date(2023, 2, 1), date(2023, 7, 31))
POINT = {"XAUUSD": 1e-3, "XAGUSD": 1e-3}
RECORD = struct.Struct(">5if")  # seconds from midnight UTC, open, close, low, high, volume
WORKERS = 6

_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers["User-Agent"] = "Mozilla/5.0"
    return _local.session


def day_path(symbol: str, day: date, side: str) -> Path:
    return RAW_DIR / symbol / f"{day.isoformat()}_{side}.parquet"


def fetch_day(symbol: str, day: date, side: str, tries: int = 6) -> str:
    """Download one daily file to parquet. Returns 'cached', 'ok', 'empty' or 'failed'."""
    out = day_path(symbol, day, side)
    if out.exists():
        return "cached"
    url = URL.format(sym=symbol, y=day.year, m=day.month - 1, d=day.day, side=side)  # month is zero-based
    for attempt in range(tries):
        try:
            resp = _session().get(url, timeout=(8, 120))
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
            continue
        if resp.status_code == 404 or (resp.status_code == 200 and not resp.content):
            frame = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
            out.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(out, index=False)
            return "empty"
        if resp.status_code == 200:
            try:
                raw = lzma.decompress(resp.content)
            except lzma.LZMAError:
                time.sleep(3 * (attempt + 1))
                continue
            arr = np.array([RECORD.unpack_from(raw, i) for i in range(0, len(raw), RECORD.size)], dtype=np.float64)
            base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
            scale = POINT[symbol]
            frame = pd.DataFrame(
                {
                    "time": pd.to_datetime(base + arr[:, 0], unit="s"),
                    "open": arr[:, 1] * scale,
                    "high": arr[:, 4] * scale,
                    "low": arr[:, 3] * scale,
                    "close": arr[:, 2] * scale,
                    "volume": arr[:, 5],
                }
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(out, index=False)
            return "ok"
        time.sleep(5 * (attempt + 1))  # 503 and friends: back off
    return "failed"


def load_day(symbol: str, day: date, side: str) -> pd.DataFrame | None:
    """Traded minutes of one day (flat zero-volume filler candles removed), or None if absent."""
    path = day_path(symbol, day, side)
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    return frame[frame["volume"] > 0].reset_index(drop=True)


def repair_days() -> list[date]:
    start, end = REPAIR_WINDOW
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [d for d in days if d.weekday() != 5]  # no Saturday trading


def sample_days(start_year: int, end: date, per_year: int) -> list[date]:
    """Evenly spaced mid-week days (Tue-Thu) so every sample is a full trading session."""
    days, step, current = [], 365 // per_year, date(start_year, 1, 15)
    while current < end:
        probe = current
        while probe.weekday() not in (1, 2, 3):
            probe += timedelta(days=1)
        if probe < end:
            days.append(probe)
        current += timedelta(days=step)
    return days


def _sunday(year: int, month: int, which: int) -> date:
    """`which`-th Sunday of the month, or the last one when `which` is -1."""
    if which > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (which - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def clock_check_days(start_year: int, end: date) -> dict[date, str]:
    """Four Wednesdays per year that tell the candidate clock rules apart.

    Two sit in the weeks where the US has changed its clocks and Europe has not (after the
    second Sunday of March, after the last Sunday of October); the other two are mid-winter
    and mid-summer.
    """
    days: dict[date, str] = {}
    for year in range(start_year, end.year + 1):
        candidates = {
            _sunday(year, 1, 2) + timedelta(days=3): "winter",
            _sunday(year, 3, 2) + timedelta(days=3): "March, US summer time only",
            _sunday(year, 7, 2) + timedelta(days=3): "summer",
            _sunday(year, 10, -1) + timedelta(days=3): "Oct/Nov, US summer time only",
        }
        days.update({d: label for d, label in candidates.items() if d <= end})
    return days


def run(jobs: list[tuple[str, date, str]], label: str) -> dict[str, int]:
    counts = {"cached": 0, "ok": 0, "empty": 0, "failed": 0}
    done = 0
    lock = threading.Lock()

    def work(job: tuple[str, date, str]) -> None:
        nonlocal done
        status = fetch_day(*job)
        with lock:
            counts[status] += 1
            done += 1
            if status == "failed":
                print(f"  FAILED {job[0]} {job[1]} {job[2]}", flush=True)
            if done % 20 == 0 or done == len(jobs):
                print(f"  {label}: {done}/{len(jobs)} {counts}", flush=True)

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(work, jobs))
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("job", choices=["repair", "sample", "clock"])
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="last day (sample and clock jobs)")
    parser.add_argument("--per-year", type=int, default=3)
    args = parser.parse_args()
    if args.job != "repair" and args.end is None:
        parser.error("--end is required for the sample and clock jobs")
    if args.job == "repair":
        jobs = [(s, d, "BID") for d in repair_days() for s in args.symbols]
    elif args.job == "clock":
        jobs = [(s, d, "BID") for d in clock_check_days(args.start_year, args.end) for s in args.symbols]
    else:
        days = sample_days(args.start_year, args.end, args.per_year)
        jobs = [(s, d, side) for d in reversed(days) for s in args.symbols for side in ("BID", "ASK")]
    print(f"{args.job}: {len(jobs)} daily files", flush=True)
    counts = run(jobs, args.job)
    print(f"{args.job} finished: {counts}", flush=True)


if __name__ == "__main__":
    main()
