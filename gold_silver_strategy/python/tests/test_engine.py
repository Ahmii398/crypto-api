"""Engine tests: fill rules on hand-made bars, and a no-look-ahead (prefix) test on real data.

    cd python && python -m pytest -q
"""

import numpy as np
import pandas as pd
import pytest

from gsms import backtest, data, engine, space
from gsms.params import Params


def make_bars(rows, symbol="XAUUSD"):
    """rows: (open, high, low, close); hourly bars inside one session."""
    time = pd.date_range("2024-01-02 00:00", periods=len(rows), freq="h")
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"]).assign(time=time)
    frame["session"] = pd.Timestamp("2024-01-02")
    frame["week"] = pd.Timestamp("2024-01-01")
    return backtest.Bars(frame, symbol, "1h")


def run_signals(rows, signal_bar, direction, **overrides):
    """Push one hand-placed signal through the trade management (ATR stop of 1 ATR, RR 2)."""
    bars = make_bars(rows)
    n = len(bars)
    bull, bear = np.zeros(n, bool), np.zeros(n, bool)
    (bull if direction > 0 else bear)[signal_bar] = True
    settings = dict(use_ema=False, use_ls=False, use_sd=False, use_cp=True, conf_window=1, sl_atr=1.0, rr=2.0, min_stop=0.01, max_stop=100.0, warmup=0)
    settings.update(overrides)
    return bars, backtest.run(bars, Params(**settings), cost_pct_per_side=0.0, signals=(bull, bear))


FLAT = (100.0, 101.0, 99.0, 100.0)  # every bar has a range of 2, so ATR = 2 throughout


def test_ema_and_atr_are_seeded_with_the_first_bar():
    close = np.array([10.0, 12.0, 11.0])
    out = engine.ema(close, 3)
    assert out[0] == 10.0 and out[1] == pytest.approx(11.0) and out[2] == pytest.approx(11.0)
    high, low = np.array([11.0, 13.0]), np.array([9.0, 11.5])
    atr = engine.atr(high, low, np.array([10.0, 12.0]), 2)
    assert atr[0] == 2.0 and atr[1] == pytest.approx((2.0 + 3.0) / 2)


def test_entry_is_the_next_open_and_target_is_hit():
    rows = [FLAT, FLAT, (100.5, 101.0, 100.0, 100.8), (100.8, 104.5, 100.6, 104.0), FLAT]
    _, trades = run_signals(rows, 1, +1)
    assert len(trades) == 1
    assert trades.signal_bar[0] == 1 and trades.entry_bar[0] == 2 and trades.entry[0] == 100.5
    # stop = 100 - 2, target = 100 + 4, both fixed from the signal bar's close
    assert trades.exit_bar[0] == 3 and trades.exit[0] == pytest.approx(104.0) and trades.reason[0] == engine.EXIT_TARGET
    assert trades.r_gross[0] == pytest.approx((104.0 - 100.5) / 2.0)


def test_stop_is_hit_for_a_short():
    rows = [FLAT, FLAT, (99.5, 100.0, 99.0, 99.8), (99.8, 102.5, 99.5, 102.0), FLAT]
    _, trades = run_signals(rows, 1, -1)
    assert trades.exit[0] == pytest.approx(102.0) and trades.reason[0] == engine.EXIT_STOP
    assert trades.r_gross[0] == pytest.approx(-(102.0 - 99.5) / 2.0)


def test_gap_through_the_stop_fills_at_the_open():
    rows = [FLAT, FLAT, (100.0, 100.5, 99.5, 100.0), (97.0, 97.5, 96.0, 96.5), FLAT]
    _, trades = run_signals(rows, 1, +1)
    assert trades.exit[0] == 97.0 and trades.reason[0] == engine.EXIT_GAP_STOP


def test_bar_touching_stop_and_target_uses_the_extreme_nearer_the_open():
    # open 100: high 104.5 is 4.5 away, low 97.5 is 2.5 away -> low first -> stop
    rows = [FLAT, FLAT, (100.0, 100.5, 99.5, 100.0), (100.0, 104.5, 97.5, 100.0), FLAT]
    _, trades = run_signals(rows, 1, +1)
    assert trades.reason[0] == engine.EXIT_STOP and trades.exit[0] == pytest.approx(98.0)
    # open 103: high 104.5 is 1.5 away, low 97.5 is 5.5 away -> high first -> target
    rows[3] = (103.0, 104.5, 97.5, 100.0)
    _, trades = run_signals(rows, 1, +1)
    assert trades.reason[0] == engine.EXIT_TARGET and trades.exit[0] == pytest.approx(104.0)


def test_exit_can_happen_on_the_entry_bar_and_only_one_position_is_held():
    rows = [FLAT, FLAT, (100.0, 104.5, 99.5, 104.0), FLAT, FLAT]
    bars = make_bars(rows)
    bull = np.array([False, True, True, False, False])
    p = Params(use_ema=False, use_ls=False, use_sd=False, use_cp=True, conf_window=1, sl_atr=1.0, rr=2.0, min_stop=0.01, max_stop=100.0, warmup=0)
    trades = backtest.run(bars, p, cost_pct_per_side=0.0, signals=(bull, np.zeros(5, bool)))
    # first trade enters and exits on bar 2; the signal on bar 2 is then free to open the next one
    assert list(trades.entry_bar) == [2, 3] and trades.exit_bar[0] == 2


def test_costs_are_charged_on_both_fills_in_r():
    rows = [FLAT, FLAT, (100.5, 101.0, 100.0, 100.8), (100.8, 104.5, 100.6, 104.0), FLAT]
    bars = make_bars(rows)
    bull = np.array([False, True, False, False, False])
    p = Params(use_ema=False, use_ls=False, use_sd=False, use_cp=True, conf_window=1, sl_atr=1.0, rr=2.0, min_stop=0.01, max_stop=100.0, warmup=0)
    trades = backtest.run(bars, p, cost_pct_per_side=0.01, signals=(bull, np.zeros(5, bool)))
    assert trades.cost_r[0] == pytest.approx(0.0001 * (100.5 + 104.0) / 2.0)


@pytest.fixture(scope="module")
def real_frame():
    try:
        frame = data.load_bars("XAUUSD", "1h")
    except FileNotFoundError:
        pytest.skip("market data not downloaded (python -m gsms.data)")
    return frame[(frame["time"] >= "2019-01-01") & (frame["time"] < "2020-07-01")].reset_index(drop=True)


def test_no_look_ahead_signals_and_trades_do_not_change_when_later_bars_arrive(real_frame):
    """Run on the first part of the data and on all of it: everything already decided must match."""
    cut = len(real_frame) * 2 // 3
    whole = backtest.Bars(real_frame, "XAUUSD", "1h")
    part = backtest.Bars(real_frame.iloc[:cut].reset_index(drop=True), "XAUUSD", "1h")
    rng = np.random.default_rng(3)
    for index in range(30):
        p = space.sample(rng, subset=space.SUBSETS[index % len(space.SUBSETS)])
        for name in ("liquidity", "zones", "candles"):
            for a, b in zip(getattr(whole, name)(p), getattr(part, name)(p)):
                assert np.array_equal(a[:cut], b, equal_nan=True), (name, p)
        full, short = backtest.run(whole, p), backtest.run(part, p)
        # Trades the full run closed before the cut must be the first trades of the short run.
        done = int((full.exit_bar < cut - 1).sum())
        assert len(short) >= done
        for field in ("signal_bar", "entry_bar", "exit_bar", "direction", "entry", "exit", "stop_dist"):
            assert np.array_equal(getattr(full, field)[:done], getattr(short, field)[:done]), (field, p)
