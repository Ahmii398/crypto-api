"""Data-quality report: timestamps, coverage, measured spreads, independent daily cross-check.

Writes results/data_quality.md and results/data_quality.json. Every number in the report is
computed here from files on disk; a check whose input is missing says so instead of guessing.

Inputs
  data/raw/histdata/           HistData archives (primary 1-minute history)
  data/raw/dukascopy/          Dukascopy daily files fetched by `gsms.dukascopy`
  data/reference/              optional daily reference series from other vendors:
                                 twelvedata_XAUUSD_1day.json            (Twelve Data time_series)
                                 alphavantage_GOLD_SILVER_HISTORY_XAG.json (Alpha Vantage)
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from . import DATA_DIR, RESULTS_DIR, SYMBOLS, data, dukascopy

REFERENCE_DIR = DATA_DIR / "reference"
CLOCK_LABELS = ["winter", "March, US summer time only", "summer", "Oct/Nov, US summer time only"]


def _raw_histdata(symbol: str) -> pd.DataFrame:
    frames = [data._read_archive(p) for p in sorted(data.RAW_DIR.glob(f"{symbol}_*.zip"))]
    return pd.concat(frames, ignore_index=True).drop_duplicates("file_time")


def clock_check(symbol: str, end: date) -> pd.DataFrame:
    """Compare HistData with Dukascopy (true UTC) on four diagnostic days per year.

    For each day: the whole-hour offset k that minimises the mean |close difference| when
    UTC = file clock + k hours, the residual at that offset, the residual at the next best
    offset, and the offset `data.file_clock_to_utc` applies.
    """
    raw = _raw_histdata(symbol)
    rows = []
    for day, label in sorted(dukascopy.clock_check_days(2009, end).items()):
        duka = dukascopy.load_day(symbol, day, "BID")
        if duka is None or not len(duka):
            rows.append({"year": day.year, "day": day.isoformat(), "window": label, "status": "Dukascopy day not available"})
            continue
        duka = duka.set_index("time")["close"]
        lo, hi = pd.Timestamp(day) - pd.Timedelta(days=1), pd.Timestamp(day) + pd.Timedelta(days=2)
        sub = raw[(raw["file_time"] >= lo) & (raw["file_time"] < hi)]
        residual = {}
        for k in range(3, 8):
            shifted = pd.Series(sub["close"].to_numpy(), index=sub["file_time"] + pd.Timedelta(hours=k))
            joined = pd.concat([shifted.rename("hd"), duka.rename("dk")], axis=1, join="inner").dropna()
            if len(joined) > 300:
                residual[k] = float(((joined["hd"] - joined["dk"]).abs() / joined["dk"]).mean() * 1e4)
        if len(residual) < 2:
            rows.append({"year": day.year, "day": day.isoformat(), "window": label, "status": "no HistData overlap"})
            continue
        ranked = sorted(residual, key=residual.get)
        applied_utc = data.file_clock_to_utc(pd.Series([pd.Timestamp(day) + pd.Timedelta(hours=12)])).iloc[0]
        applied = int((applied_utc - (pd.Timestamp(day) + pd.Timedelta(hours=12))) / pd.Timedelta(hours=1))
        rows.append(
            {
                "year": day.year,
                "day": day.isoformat(),
                "window": label,
                "status": "ok",
                "best_offset_h": ranked[0],
                "residual_bp": residual[ranked[0]],
                "next_best_bp": residual[ranked[1]],
                "applied_offset_h": applied,
                "applied_is_best": applied == ranked[0],
            }
        )
    return pd.DataFrame(rows)


def coverage(symbol: str) -> pd.DataFrame:
    """Per year: 1-minute rows, sessions, and weekdays with holes inside the trading day."""
    m1 = data.load_m1(symbol)
    session, minute = data.session_fields(m1["time"])
    frame = pd.DataFrame({"session": session.values, "hour": (minute // 60).values})
    frame = frame[frame["session"].dt.day_of_week < 5]
    hours = frame.groupby(["session", "hour"]).size().unstack(fill_value=0)
    present = (hours.reindex(columns=range(24), fill_value=0) >= 10).to_numpy()
    # Hours 0-22 of the session clock are 18:00-17:00 New York; hour 23 is the daily break.
    holes = []
    for row in present[:, :23]:
        have = np.flatnonzero(row)
        holes.append(0 if not len(have) else int(((~row) & (np.arange(23) > have.min()) & (np.arange(23) < have.max())).sum()))
    per_session = pd.DataFrame({"year": hours.index.year, "holes": holes})
    rows_per_year = m1["time"].dt.year.value_counts().sort_index()
    out = per_session.groupby("year").agg(sessions=("holes", "size"), sessions_with_2plus_missing_hours=("holes", lambda s: int((s >= 2).sum())))
    out.insert(0, "one_minute_rows", rows_per_year.reindex(out.index).astype(int))
    return out.reset_index()


def spreads(symbol: str, end: date, per_year: int = 3) -> tuple[pd.DataFrame, list[str]]:
    """Quoted spread (ASK close - BID close) on the sampled days.

    Returns the per-day table and the days left out because the ASK file carries no spread
    (ask equal to bid all day, an artefact of some early Dukascopy files).
    """
    rows, unusable = [], []
    for day in dukascopy.sample_days(2010, end, per_year):
        bid = dukascopy.load_day(symbol, day, "BID")
        ask = dukascopy.load_day(symbol, day, "ASK")
        if bid is None or ask is None or not len(bid) or not len(ask):
            continue
        both = bid.merge(ask[["time", "close"]].rename(columns={"close": "ask"}), on="time")
        spread = both["ask"] - both["close"]
        if spread.median() <= 0:
            unusable.append(day.isoformat())
            continue
        hour = both["time"].dt.hour
        active = spread[(hour >= 7) & (hour < 20)]  # London and New York hours
        rows.append(
            {
                "day": day.isoformat(),
                "year": day.year,
                "price": float(both["close"].median()),
                "spread_median": float(spread.median()),
                "spread_mean": float(spread.mean()),
                "spread_p90": float(spread.quantile(0.9)),
                "spread_active_median": float(active.median()) if len(active) else np.nan,
            }
        )
    days = pd.DataFrame(rows)
    if days.empty:
        return days, unusable
    for col in ("spread_median", "spread_mean", "spread_p90", "spread_active_median"):
        days[col.replace("spread_", "bp_")] = days[col] / days["price"] * 1e4
    return days, unusable


def daily_reference(symbol: str) -> dict | None:
    """Compare our daily session bars with an independent vendor's daily series."""
    if symbol == "XAUUSD":
        path = REFERENCE_DIR / "twelvedata_XAUUSD_1day.json"
        if not path.exists():
            return None
        values = json.loads(path.read_text()).get("values") or []
        ref = pd.DataFrame(values)
        if ref.empty:
            return None
        ref["date"] = pd.to_datetime(ref["datetime"])
        for col in ("open", "high", "low", "close"):
            ref[col] = ref[col].astype(float)
        vendor, has_range = "Twelve Data XAU/USD 1day", True
    else:
        path = REFERENCE_DIR / "alphavantage_GOLD_SILVER_HISTORY_XAG.json"
        if not path.exists():
            return None
        ref = pd.DataFrame(json.loads(path.read_text()).get("data") or [])
        if ref.empty:
            return None
        ref["date"] = pd.to_datetime(ref["date"])
        ref["close"] = pd.to_numeric(ref["price"], errors="coerce")
        vendor, has_range = "Alpha Vantage GOLD_SILVER_HISTORY (one price per day)", False
    ours = data.load_bars(symbol, "1D")
    merged = ours.merge(ref, left_on="session", right_on="date", suffixes=("", "_ref")).dropna(subset=["close_ref"])
    merged = merged[merged["session"] >= "2010-01-01"]
    if merged.empty:
        return None
    # The vendors cut the day at a different hour than the 17:00 New York close, so closes are
    # not expected to be equal. Two robust tests: is the vendor's price inside our day's range,
    # and do daily returns move together.
    inside = ((merged["close_ref"] >= merged["low"] * 0.999) & (merged["close_ref"] <= merged["high"] * 1.001)).mean()
    close_gap_bp = ((merged["close_ref"] - merged["close"]).abs() / merged["close"] * 1e4)
    ret_corr = merged["close"].pct_change().corr(merged["close_ref"].pct_change())
    out = {
        "vendor": vendor,
        "days_compared": int(len(merged)),
        "first_day": str(merged["session"].iloc[0].date()),
        "last_day": str(merged["session"].iloc[-1].date()),
        "vendor_price_inside_our_daily_range": float(inside),
        "median_abs_close_gap_bp": float(close_gap_bp.median()),
        "p95_abs_close_gap_bp": float(close_gap_bp.quantile(0.95)),
        "daily_return_correlation": float(ret_corr),
    }
    by_year = merged.assign(year=merged["session"].dt.year, gap=close_gap_bp).groupby("year")["gap"].median()
    out["median_abs_close_gap_bp_by_year"] = {int(k): float(v) for k, v in by_year.items()}
    if has_range:
        out["median_abs_high_gap_bp"] = float(((merged["high_ref"] - merged["high"]).abs() / merged["high"] * 1e4).median())
        out["median_abs_low_gap_bp"] = float(((merged["low_ref"] - merged["low"]).abs() / merged["low"] * 1e4).median())
    return out


def _table(frame: pd.DataFrame, formats: dict | None = None) -> str:
    formats = formats or {}
    cols = list(frame.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in frame.iterrows():
        cells = []
        for col in cols:
            value = row[col]
            if col in formats and not (isinstance(value, float) and np.isnan(value)):
                cells.append(formats[col].format(value))
            else:
                cells.append("" if (isinstance(value, float) and np.isnan(value)) else str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    md = ["# Data quality report", "", "Generated by `python -m gsms.data_checks`. All figures are computed from the files on disk.", ""]
    for symbol in SYMBOLS:
        m1 = data.load_m1(symbol)
        end = m1["time"].iloc[-1].date()
        md += [f"## {symbol}", "", f"1-minute bid bars: {len(m1):,} rows, {m1['time'].iloc[0]} to {m1['time'].iloc[-1]} UTC.", ""]

        clock = clock_check(symbol, end)
        ok = clock[clock["status"] == "ok"]
        report[symbol] = {"clock": clock.to_dict("records")}
        md += [
            "### Timestamp verification against Dukascopy (true UTC)",
            "",
            "Four diagnostic days per year. `best` is the whole-hour offset (UTC = file clock + k h) that minimises "
            "the mean absolute close difference; `residual` is that difference in basis points of price; `next` is the "
            "residual at the second-best offset. `applied` is the offset used when building the dataset.",
            "",
            f"Days checked: {len(ok)}. Days where the applied offset is the best-fitting one: {int(ok['applied_is_best'].sum())}.",
            "",
        ]
        if len(ok):
            wide = ok.assign(cell=ok.apply(lambda r: f"k={r['best_offset_h']:.0f} ({r['residual_bp']:.2f} bp, next {r['next_best_bp']:.0f})" + ("" if r["applied_is_best"] else f" **applied {r['applied_offset_h']:.0f}**"), axis=1))
            wide = wide.pivot(index="year", columns="window", values="cell").reindex(columns=CLOCK_LABELS).fillna("no data").reset_index()
            md += [_table(wide), ""]
        skipped = clock[clock["status"] != "ok"]
        if len(skipped):
            md += ["Not checked: " + "; ".join(f"{r['day']} ({r['status']})" for _, r in skipped.iterrows()) + ".", ""]

        cov = coverage(symbol)
        report[symbol]["coverage"] = cov.to_dict("records")
        md += [
            "### Coverage after the 2023 repair",
            "",
            "`sessions_with_2plus_missing_hours` counts Monday-Friday sessions with two or more empty hours between the "
            "first and last quoted hour (holiday halts produce a handful per year).",
            "",
            _table(cov, {"one_minute_rows": "{:,}"}),
            "",
        ]

        spread, unusable = spreads(symbol, end)
        if spread.empty:
            md += ["### Quoted spread", "", "No bid/ask sample days on disk; spread not measured.", ""]
            report[symbol]["spread"] = None
        else:
            by_year = spread.groupby("year").agg(
                days=("day", "size"), price=("price", "median"),
                spread_median=("spread_median", "median"), spread_active_median=("spread_active_median", "median"),
                spread_p90=("spread_p90", "median"),
                bp_median=("bp_median", "median"), bp_active_median=("bp_active_median", "median"), bp_p90=("bp_p90", "median"),
            ).reset_index()  # fmt: skip
            by_year[["year", "days"]] = by_year[["year", "days"]].astype(int)
            overall = {
                "sample_days": int(len(spread)),
                "bp_median_all_hours": float(spread["bp_median"].median()),
                "bp_median_active_hours": float(spread["bp_active_median"].median()),
                "bp_mean_all_hours": float(spread["bp_mean"].median()),
                "bp_p90": float(spread["bp_p90"].median()),
                "bp_median_since_2022": float(spread.loc[spread["year"] >= 2022, "bp_median"].median()),
                "bp_max_yearly_median": float(by_year["bp_median"].max()),
                "days_without_usable_ask": unusable,
            }
            report[symbol]["spread"] = {"overall": overall, "by_year": by_year.to_dict("records")}
            md += [
                "### Quoted spread (Dukascopy ASK close - BID close, 1-minute, sampled mid-week days)",
                "",
                f"{overall['sample_days']} sample days. Median over days of the daily median spread: "
                f"**{overall['bp_median_all_hours']:.2f} bp** of price (all hours), {overall['bp_median_active_hours']:.2f} bp in "
                f"07:00-20:00 UTC, 90th percentile within a day {overall['bp_p90']:.2f} bp. Since 2022: {overall['bp_median_since_2022']:.2f} bp.",
                "",
                _table(by_year, {"year": "{:.0f}", "days": "{:.0f}", "price": "{:.2f}", "spread_median": "{:.4f}", "spread_active_median": "{:.4f}", "spread_p90": "{:.4f}", "bp_median": "{:.2f}", "bp_active_median": "{:.2f}", "bp_p90": "{:.2f}"}),
                "",
            ]
            if unusable:
                md += ["Left out (ASK file has no spread, ask = bid all day): " + ", ".join(unusable) + ".", ""]

        ref = daily_reference(symbol)
        report[symbol]["daily_reference"] = ref
        md += ["### Independent daily cross-check", ""]
        if ref is None:
            md += ["No reference series on disk; not checked.", ""]
        else:
            md += [
                f"Reference: {ref['vendor']}; {ref['days_compared']:,} days from {ref['first_day']} to {ref['last_day']}.",
                "",
                f"- Vendor price inside our daily low-high range (0.1% tolerance): {ref['vendor_price_inside_our_daily_range']:.1%} of days",
                f"- Daily return correlation: {ref['daily_return_correlation']:.4f}",
                f"- Absolute close difference: median {ref['median_abs_close_gap_bp']:.1f} bp, 95th percentile {ref['p95_abs_close_gap_bp']:.1f} bp "
                "(the vendors close their day at a different hour, so closes are not expected to be equal)",
            ]
            if "median_abs_high_gap_bp" in ref:
                md += [f"- Absolute difference of daily highs: median {ref['median_abs_high_gap_bp']:.1f} bp; of daily lows: median {ref['median_abs_low_gap_bp']:.1f} bp"]
            md += ["- Median absolute close difference by year (bp): " + ", ".join(f"{y}: {v:.1f}" for y, v in ref["median_abs_close_gap_bp_by_year"].items()), ""]

    (RESULTS_DIR / "data_quality.json").write_text(json.dumps(report, indent=2, default=str))
    (RESULTS_DIR / "data_quality.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
