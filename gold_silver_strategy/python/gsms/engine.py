"""Bar-by-bar strategy engine.

Every function here evaluates bar ``t`` using only bars ``<= t`` and is the Python twin of a
block in the Pine scripts (same formulas, same order of operations, same tie-breaks). The
rules are written out in SPEC.md.

Module functions return per-bar signal arrays that depend only on that module's parameters,
so they can be cached and recombined; `simulate` applies the confluence rule, the stop and
target, and a TradingView-style broker emulation.
"""

from __future__ import annotations

import numpy as np
from numba import njit

NEVER = -(10**9)
LS_CAPACITY = 10  # swing levels tracked per side
SD_CAPACITY = 8  # zones tracked per side

# Exit reasons recorded per trade.
EXIT_STOP, EXIT_TARGET, EXIT_GAP_STOP, EXIT_GAP_TARGET = 0, 1, 2, 3

# Pine Script compares floating-point numbers with a tolerance: two values closer than 1e-10
# are equal. Prices sit on a decimal grid, so thresholds such as "upper wick <= 25% of the
# range" are hit exactly quite often, and plain IEEE comparisons would then decide those ties
# by rounding noise. The helpers below reproduce Pine's rule; every float comparison in the
# strategy logic goes through them.
EPS = 1e-10


@njit(inline="always")
def gt(a, b):
    return a - b >= EPS


@njit(inline="always")
def ge(a, b):
    return a - b > -EPS


@njit(inline="always")
def lt(a, b):
    return b - a >= EPS


@njit(inline="always")
def le(a, b):
    return b - a > -EPS


@njit(cache=True)
def ema(src, length):
    """EMA seeded with the first value (the Pine scripts use the same hand-written recursion)."""
    n = len(src)
    out = np.empty(n)
    alpha = 2.0 / (length + 1.0)
    out[0] = src[0]
    for i in range(1, n):
        out[i] = alpha * src[i] + (1.0 - alpha) * out[i - 1]
    return out


@njit(cache=True)
def atr(high, low, close, length):
    """Wilder ATR seeded with the first bar's range."""
    n = len(close)
    out = np.empty(n)
    out[0] = high[0] - low[0]
    for i in range(1, n):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        out[i] = (out[i - 1] * (length - 1) + tr) / length
    return out


@njit(cache=True)
def _is_pivot_low(low, p, side):
    """Bar `p` is strictly lower than the `side` bars on each side of it."""
    v = low[p]
    for i in range(1, side + 1):
        if le(low[p - i], v) or le(low[p + i], v):
            return False
    return True


@njit(cache=True)
def _is_pivot_high(high, p, side):
    v = high[p]
    for i in range(1, side + 1):
        if ge(high[p - i], v) or ge(high[p + i], v):
            return False
    return True


@njit(cache=True)
def _test_low_level(price, pierce, t, o, l, c, mode, reclaim):
    """Sell-side level vs bar t. Returns (bullish sweep event, level still alive, pierce bar)."""
    if mode == 0:  # wick: pierce and close back above on the same bar
        if lt(l[t], price):
            return gt(c[t], price), False, pierce
        return False, True, pierce
    if pierce < 0:  # close-confirm: wait for the pierce, then for a bullish close back above
        if lt(l[t], price):
            return False, True, t
        return False, True, pierce
    if gt(c[t], price) and gt(c[t], o[t]):
        return True, False, pierce
    return False, t - pierce < reclaim, pierce


@njit(cache=True)
def _test_high_level(price, pierce, t, o, h, c, mode, reclaim):
    if mode == 0:
        if gt(h[t], price):
            return lt(c[t], price), False, pierce
        return False, True, pierce
    if pierce < 0:
        if gt(h[t], price):
            return False, True, t
        return False, True, pierce
    if lt(c[t], price) and lt(c[t], o[t]):
        return True, False, pierce
    return False, t - pierce < reclaim, pierce


@njit(cache=True)
def liquidity_sweeps(o, h, l, c, new_day, new_week, pdh, pdl, pwh, pwl, swing, source, mode, reclaim, max_age):
    """Liquidity-sweep events. Returns (bull, bear) boolean arrays.

    Liquidity rests below swing lows / previous-day / previous-week lows (and above the highs).
    A bullish sweep is price taking a low and coming back above it.
    """
    n = len(c)
    bull = np.zeros(n, np.bool_)
    bear = np.zeros(n, np.bool_)
    use_swings = source <= 2
    use_pd = source >= 1
    use_pw = source == 2 or source == 4

    lo_price = np.empty(LS_CAPACITY)
    lo_born = np.empty(LS_CAPACITY, np.int64)
    lo_pierce = np.empty(LS_CAPACITY, np.int64)
    lo_n = 0
    hi_price = np.empty(LS_CAPACITY)
    hi_born = np.empty(LS_CAPACITY, np.int64)
    hi_pierce = np.empty(LS_CAPACITY, np.int64)
    hi_n = 0
    # Higher-timeframe slots: 0 = previous day, 1 = previous week.
    hl_price = np.zeros(2)
    hl_alive = np.zeros(2, np.bool_)
    hl_pierce = np.full(2, -1, np.int64)
    hh_price = np.zeros(2)
    hh_alive = np.zeros(2, np.bool_)
    hh_pierce = np.full(2, -1, np.int64)

    for t in range(n):
        if use_pd and new_day[t] and not np.isnan(pdl[t]):
            hl_price[0] = pdl[t]
            hl_alive[0] = True
            hl_pierce[0] = -1
            hh_price[0] = pdh[t]
            hh_alive[0] = True
            hh_pierce[0] = -1
        if use_pw and new_week[t] and not np.isnan(pwl[t]):
            hl_price[1] = pwl[t]
            hl_alive[1] = True
            hl_pierce[1] = -1
            hh_price[1] = pwh[t]
            hh_alive[1] = True
            hh_pierce[1] = -1

        ev_bull = False
        ev_bear = False
        for k in range(2):
            if hl_alive[k]:
                ev, alive, pierce = _test_low_level(hl_price[k], hl_pierce[k], t, o, l, c, mode, reclaim)
                ev_bull = ev_bull or ev
                hl_alive[k] = alive
                hl_pierce[k] = pierce
            if hh_alive[k]:
                ev, alive, pierce = _test_high_level(hh_price[k], hh_pierce[k], t, o, h, c, mode, reclaim)
                ev_bear = ev_bear or ev
                hh_alive[k] = alive
                hh_pierce[k] = pierce

        if use_swings:
            j = 0
            for k in range(lo_n):
                alive = t - lo_born[k] <= max_age
                pierce = lo_pierce[k]
                if alive:
                    ev, alive, pierce = _test_low_level(lo_price[k], pierce, t, o, l, c, mode, reclaim)
                    ev_bull = ev_bull or ev
                if alive:
                    lo_price[j] = lo_price[k]
                    lo_born[j] = lo_born[k]
                    lo_pierce[j] = pierce
                    j += 1
            lo_n = j
            j = 0
            for k in range(hi_n):
                alive = t - hi_born[k] <= max_age
                pierce = hi_pierce[k]
                if alive:
                    ev, alive, pierce = _test_high_level(hi_price[k], pierce, t, o, h, c, mode, reclaim)
                    ev_bear = ev_bear or ev
                if alive:
                    hi_price[j] = hi_price[k]
                    hi_born[j] = hi_born[k]
                    hi_pierce[j] = pierce
                    j += 1
            hi_n = j

            # A pivot `swing` bars back is confirmed on this bar and becomes a level.
            if t >= 2 * swing:
                p = t - swing
                if _is_pivot_low(l, p, swing):
                    if lo_n == LS_CAPACITY:  # drop the oldest
                        for k in range(1, lo_n):
                            lo_price[k - 1] = lo_price[k]
                            lo_born[k - 1] = lo_born[k]
                            lo_pierce[k - 1] = lo_pierce[k]
                        lo_n -= 1
                    lo_price[lo_n] = l[p]
                    lo_born[lo_n] = t
                    lo_pierce[lo_n] = -1
                    lo_n += 1
                if _is_pivot_high(h, p, swing):
                    if hi_n == LS_CAPACITY:
                        for k in range(1, hi_n):
                            hi_price[k - 1] = hi_price[k]
                            hi_born[k - 1] = hi_born[k]
                            hi_pierce[k - 1] = hi_pierce[k]
                        hi_n -= 1
                    hi_price[hi_n] = h[p]
                    hi_born[hi_n] = t
                    hi_pierce[hi_n] = -1
                    hi_n += 1

        bull[t] = ev_bull
        bear[t] = ev_bear
    return bull, bear


@njit(cache=True)
def supply_demand(o, h, l, c, atr_arr, swing, width, strength, max_touch, inval, max_age):
    """Supply/demand zones and price's interaction with them.

    A demand zone is born when a swing low is confirmed and price has left it with enough
    force: zone = [swing low, swing low + width*ATR], departure >= strength*ATR. Supply mirrors.

    Returns, per bar:
      touch_bull  bar traded inside an eligible demand zone (touch count <= max_touch)
      rej_bull    ...and closed bullish above that zone's top
      bot_bull    lowest bottom among the demand zones touched on the bar (nan if none)
      inval_bull  a demand zone was invalidated on the bar
    and the same four for supply (touch_bear, rej_bear, top_bear, inval_bear).
    """
    n = len(c)
    touch_bull = np.zeros(n, np.bool_)
    rej_bull = np.zeros(n, np.bool_)
    bot_bull = np.full(n, np.nan)
    inval_bull = np.zeros(n, np.bool_)
    touch_bear = np.zeros(n, np.bool_)
    rej_bear = np.zeros(n, np.bool_)
    top_bear = np.full(n, np.nan)
    inval_bear = np.zeros(n, np.bool_)

    d_top = np.empty(SD_CAPACITY)
    d_bot = np.empty(SD_CAPACITY)
    d_born = np.empty(SD_CAPACITY, np.int64)
    d_touch = np.empty(SD_CAPACITY, np.int64)
    d_in = np.empty(SD_CAPACITY, np.bool_)
    d_n = 0
    s_top = np.empty(SD_CAPACITY)
    s_bot = np.empty(SD_CAPACITY)
    s_born = np.empty(SD_CAPACITY, np.int64)
    s_touch = np.empty(SD_CAPACITY, np.int64)
    s_in = np.empty(SD_CAPACITY, np.bool_)
    s_n = 0

    for t in range(n):
        j = 0
        for k in range(d_n):
            keep = t - d_born[k] <= max_age
            if keep:
                broken = lt(c[t], d_bot[k]) if inval == 0 else lt(l[t], d_bot[k])
                if broken:
                    keep = False
                    inval_bull[t] = True
                else:
                    inside = le(l[t], d_top[k])
                    if inside and not d_in[k]:
                        d_touch[k] += 1
                    d_in[k] = inside
                    if inside and d_touch[k] <= max_touch:
                        touch_bull[t] = True
                        if np.isnan(bot_bull[t]) or d_bot[k] < bot_bull[t]:
                            bot_bull[t] = d_bot[k]
                        if gt(c[t], d_top[k]) and gt(c[t], o[t]):
                            rej_bull[t] = True
            if keep:
                d_top[j] = d_top[k]
                d_bot[j] = d_bot[k]
                d_born[j] = d_born[k]
                d_touch[j] = d_touch[k]
                d_in[j] = d_in[k]
                j += 1
        d_n = j

        j = 0
        for k in range(s_n):
            keep = t - s_born[k] <= max_age
            if keep:
                broken = gt(c[t], s_top[k]) if inval == 0 else gt(h[t], s_top[k])
                if broken:
                    keep = False
                    inval_bear[t] = True
                else:
                    inside = ge(h[t], s_bot[k])
                    if inside and not s_in[k]:
                        s_touch[k] += 1
                    s_in[k] = inside
                    if inside and s_touch[k] <= max_touch:
                        touch_bear[t] = True
                        if np.isnan(top_bear[t]) or s_top[k] > top_bear[t]:
                            top_bear[t] = s_top[k]
                        if lt(c[t], s_bot[k]) and lt(c[t], o[t]):
                            rej_bear[t] = True
            if keep:
                s_top[j] = s_top[k]
                s_bot[j] = s_bot[k]
                s_born[j] = s_born[k]
                s_touch[j] = s_touch[k]
                s_in[j] = s_in[k]
                j += 1
        s_n = j

        if t >= 2 * swing:
            p = t - swing
            a = atr_arr[t]
            if _is_pivot_low(l, p, swing):
                bottom = l[p]
                top = bottom + width * a
                departure_high = h[p + 1]
                for i in range(p + 2, t + 1):
                    if h[i] > departure_high:
                        departure_high = h[i]
                if ge(departure_high - top, strength * a) and gt(c[t], top):
                    overlaps = False
                    for k in range(d_n):
                        if le(bottom, d_top[k]) and ge(top, d_bot[k]):
                            overlaps = True
                    if not overlaps:
                        if d_n == SD_CAPACITY:
                            for k in range(1, d_n):
                                d_top[k - 1] = d_top[k]
                                d_bot[k - 1] = d_bot[k]
                                d_born[k - 1] = d_born[k]
                                d_touch[k - 1] = d_touch[k]
                                d_in[k - 1] = d_in[k]
                            d_n -= 1
                        d_top[d_n] = top
                        d_bot[d_n] = bottom
                        d_born[d_n] = t
                        d_touch[d_n] = 0
                        d_in[d_n] = False
                        d_n += 1
            if _is_pivot_high(h, p, swing):
                top = h[p]
                bottom = top - width * a
                departure_low = l[p + 1]
                for i in range(p + 2, t + 1):
                    if l[i] < departure_low:
                        departure_low = l[i]
                if ge(bottom - departure_low, strength * a) and lt(c[t], bottom):
                    overlaps = False
                    for k in range(s_n):
                        if le(bottom, s_top[k]) and ge(top, s_bot[k]):
                            overlaps = True
                    if not overlaps:
                        if s_n == SD_CAPACITY:
                            for k in range(1, s_n):
                                s_top[k - 1] = s_top[k]
                                s_bot[k - 1] = s_bot[k]
                                s_born[k - 1] = s_born[k]
                                s_touch[k - 1] = s_touch[k]
                                s_in[k - 1] = s_in[k]
                            s_n -= 1
                        s_top[s_n] = top
                        s_bot[s_n] = bottom
                        s_born[s_n] = t
                        s_touch[s_n] = 0
                        s_in[s_n] = False
                        s_n += 1
    return touch_bull, rej_bull, bot_bull, inval_bull, touch_bear, rej_bear, top_bear, inval_bear


@njit(cache=True)
def candle_patterns(o, h, l, c, atr_arr, mask, strict, confirm):
    """Candlestick reversal patterns. Returns (bull, bear) boolean arrays.

    mask bits: 1 engulfing, 2 pin bar (hammer / shooting star), 4 morning / evening star,
    8 piercing line / dark cloud cover. `strict` raises every size threshold. With `confirm`
    the signal is delayed one bar and needs that bar to close beyond the pattern bar's extreme.
    """
    n = len(c)
    pat_bull = np.zeros(n, np.bool_)
    pat_bear = np.zeros(n, np.bool_)
    if strict:
        eng_body, pin_wick, pin_opp, pin_rng = 0.6, 0.66, 0.15, 1.0
        star_body, star_mid, star_rec, prc_body, prc_rec = 0.8, 0.3, 0.66, 0.8, 0.66
    else:
        eng_body, pin_wick, pin_opp, pin_rng = 0.3, 0.55, 0.25, 0.6
        star_body, star_mid, star_rec, prc_body, prc_rec = 0.5, 0.5, 0.5, 0.5, 0.5

    for t in range(2, n):
        a = atr_arr[t]
        body = abs(c[t] - o[t])
        rng = h[t] - l[t]
        up_wick = h[t] - max(o[t], c[t])
        lo_wick = min(o[t], c[t]) - l[t]
        body1 = abs(c[t - 1] - o[t - 1])
        body2 = abs(c[t - 2] - o[t - 2])
        bull0 = gt(c[t], o[t])
        bear0 = lt(c[t], o[t])
        bull1 = gt(c[t - 1], o[t - 1])
        bear1 = lt(c[t - 1], o[t - 1])
        b = False
        s = False
        if mask & 1:
            if bear1 and bull0 and gt(c[t], o[t - 1]) and ge(body, eng_body * a):
                if (not strict) or (gt(c[t], h[t - 1]) and ge(body, body1)):
                    b = True
            if bull1 and bear0 and lt(c[t], o[t - 1]) and ge(body, eng_body * a):
                if (not strict) or (lt(c[t], l[t - 1]) and ge(body, body1)):
                    s = True
        if mask & 2:
            if ge(rng, pin_rng * a) and ge(lo_wick, pin_wick * rng) and le(up_wick, pin_opp * rng) and lt(l[t], l[t - 1]):
                b = True
            if ge(rng, pin_rng * a) and ge(up_wick, pin_wick * rng) and le(lo_wick, pin_opp * rng) and gt(h[t], h[t - 1]):
                s = True
        if mask & 4:
            if lt(c[t - 2], o[t - 2]) and ge(body2, star_body * a) and le(body1, star_mid * body2):
                if bull0 and ge(c[t], c[t - 2] + star_rec * body2):
                    b = True
            if gt(c[t - 2], o[t - 2]) and ge(body2, star_body * a) and le(body1, star_mid * body2):
                if bear0 and le(c[t], c[t - 2] - star_rec * body2):
                    s = True
        if mask & 8:
            if bear1 and ge(body1, prc_body * a) and bull0 and ge(c[t], c[t - 1] + prc_rec * body1) and lt(c[t], o[t - 1]):
                b = True
            if bull1 and ge(body1, prc_body * a) and bear0 and le(c[t], c[t - 1] - prc_rec * body1) and gt(c[t], o[t - 1]):
                s = True
        pat_bull[t] = b
        pat_bear[t] = s

    if not confirm:
        return pat_bull, pat_bear
    bull = np.zeros(n, np.bool_)
    bear = np.zeros(n, np.bool_)
    for t in range(1, n):
        bull[t] = pat_bull[t - 1] and gt(c[t], h[t - 1])
        bear[t] = pat_bear[t - 1] and lt(c[t], l[t - 1])
    return bull, bear


@njit(cache=True)
def simulate(
    o, h, l, c, atr_arr, hour, ema_fast, ema_slow, ema_trend,
    ls_bull, ls_bear,
    sd_touch_bull, sd_rej_bull, sd_bot_bull, sd_inval_bull,
    sd_touch_bear, sd_rej_bear, sd_top_bear, sd_inval_bear,
    cp_bull, cp_bear,
    use_ema, use_ls, use_sd, use_cp, window, ema_mode, sd_confirm,
    sl_mode, sl_atr, sl_lookback, sl_buffer, min_stop, max_stop, rr,
    direction, use_trend, sess_start, sess_end, first_bar, last_bar, tick,
):  # fmt: skip
    """Confluence, order placement and broker emulation.

    Signal on the close of bar t -> market entry at the open of bar t+1, with a stop and a
    target fixed from bar t's close. One position at a time. Fills follow TradingView's
    broker emulator: a gap through an order fills at the open; when one bar touches both
    stop and target, the extreme nearer the open is assumed to be reached first.

    Signals are taken on bars first_bar <= t < last_bar. With tick > 0 the stop and target
    are rounded to the price tick, as TradingView does with order prices.

    Returns arrays (signal bar, entry bar, exit bar, direction, entry price, exit price,
    stop distance, exit reason), one element per closed trade.
    """
    n = len(c)
    cap = n // 2 + 2
    t_sig = np.empty(cap, np.int64)
    t_in = np.empty(cap, np.int64)
    t_out = np.empty(cap, np.int64)
    t_dir = np.empty(cap, np.int8)
    p_in = np.empty(cap)
    p_out = np.empty(cap)
    p_dist = np.empty(cap)
    reason = np.empty(cap, np.int8)
    k = 0

    last_xup = NEVER
    last_xdn = NEVER
    last_ls_bull = NEVER
    last_ls_bear = NEVER
    last_sd_bull = NEVER
    last_sd_bear = NEVER
    last_cp_bull = NEVER
    last_cp_bear = NEVER
    zone_bot = np.nan
    zone_top = np.nan

    pos = 0
    pending = 0
    stop = 0.0
    target = 0.0
    dist = 0.0
    entry = 0.0
    entry_bar = 0
    signal_bar = 0
    w = window - 1

    for t in range(n):
        # ---- broker: fill the pending entry at the open, then test stop/target on this bar
        if pending != 0:
            pos = pending
            pending = 0
            entry = o[t]
            entry_bar = t
        if pos != 0:
            out = np.nan
            why = -1
            if pos == 1:
                if le(o[t], stop):
                    out, why = o[t], EXIT_GAP_STOP
                elif ge(o[t], target):
                    out, why = o[t], EXIT_GAP_TARGET
                else:
                    hit_stop = le(l[t], stop)
                    hit_target = ge(h[t], target)
                    if hit_stop and hit_target:
                        if lt(h[t] - o[t], o[t] - l[t]):  # high reached first
                            out, why = target, EXIT_TARGET
                        else:
                            out, why = stop, EXIT_STOP
                    elif hit_stop:
                        out, why = stop, EXIT_STOP
                    elif hit_target:
                        out, why = target, EXIT_TARGET
            else:
                if ge(o[t], stop):
                    out, why = o[t], EXIT_GAP_STOP
                elif le(o[t], target):
                    out, why = o[t], EXIT_GAP_TARGET
                else:
                    hit_stop = ge(h[t], stop)
                    hit_target = le(l[t], target)
                    if hit_stop and hit_target:
                        if lt(h[t] - o[t], o[t] - l[t]):  # high reached first
                            out, why = stop, EXIT_STOP
                        else:
                            out, why = target, EXIT_TARGET
                    elif hit_stop:
                        out, why = stop, EXIT_STOP
                    elif hit_target:
                        out, why = target, EXIT_TARGET
            if why >= 0:
                t_sig[k] = signal_bar
                t_in[k] = entry_bar
                t_out[k] = t
                t_dir[k] = pos
                p_in[k] = entry
                p_out[k] = out
                p_dist[k] = dist
                reason[k] = why
                k += 1
                pos = 0

        # ---- strategy: update module state on the close of bar t
        ema_up = gt(ema_fast[t], ema_slow[t])
        ema_dn = lt(ema_fast[t], ema_slow[t])
        x_up = t > 0 and ema_up and le(ema_fast[t - 1], ema_slow[t - 1])
        x_dn = t > 0 and ema_dn and ge(ema_fast[t - 1], ema_slow[t - 1])
        if x_up:
            last_xup = t
        if x_dn:
            last_xdn = t
        if ls_bull[t]:
            last_ls_bull = t
        if ls_bear[t]:
            last_ls_bear = t
        if sd_inval_bull[t]:
            last_sd_bull = NEVER
        if sd_touch_bull[t]:
            last_sd_bull = t
            zone_bot = sd_bot_bull[t]
        if sd_inval_bear[t]:
            last_sd_bear = NEVER
        if sd_touch_bear[t]:
            last_sd_bear = t
            zone_top = sd_top_bear[t]
        if cp_bull[t]:
            last_cp_bull = t
        if cp_bear[t]:
            last_cp_bear = t

        if pos != 0 or pending != 0 or t < first_bar or t >= last_bar or t >= n - 1:
            continue

        # ---- confluence: every enabled module in context, at least one firing on this bar
        ctx_long = True
        ctx_short = True
        evt_long = False
        evt_short = False
        if use_ema:
            if ema_mode == 0:
                ctx_long = ctx_long and ema_up and t - last_xup <= w
                ctx_short = ctx_short and ema_dn and t - last_xdn <= w
                evt_long = evt_long or x_up
                evt_short = evt_short or x_dn
            else:
                ctx_long = ctx_long and ema_up
                ctx_short = ctx_short and ema_dn
        if use_ls:
            ctx_long = ctx_long and t - last_ls_bull <= w
            ctx_short = ctx_short and t - last_ls_bear <= w
            evt_long = evt_long or ls_bull[t]
            evt_short = evt_short or ls_bear[t]
        if use_sd:
            ctx_long = ctx_long and t - last_sd_bull <= w
            ctx_short = ctx_short and t - last_sd_bear <= w
            if sd_confirm == 0:
                evt_long = evt_long or sd_touch_bull[t]
                evt_short = evt_short or sd_touch_bear[t]
            else:
                evt_long = evt_long or sd_rej_bull[t]
                evt_short = evt_short or sd_rej_bear[t]
        if use_cp:
            ctx_long = ctx_long and t - last_cp_bull <= w
            ctx_short = ctx_short and t - last_cp_bear <= w
            evt_long = evt_long or cp_bull[t]
            evt_short = evt_short or cp_bear[t]

        go_long = ctx_long and evt_long and direction != 2
        go_short = ctx_short and evt_short and direction != 1
        if use_trend:
            go_long = go_long and gt(c[t], ema_trend[t])
            go_short = go_short and lt(c[t], ema_trend[t])
        if sess_start >= 0:
            if sess_start <= sess_end:
                in_session = sess_start <= hour[t] < sess_end
            else:
                in_session = hour[t] >= sess_start or hour[t] < sess_end
            go_long = go_long and in_session
            go_short = go_short and in_session
        if go_long == go_short:  # no signal, or conflicting signals
            continue

        # ---- stop and target from this bar's close
        a = atr_arr[t]
        if go_long:
            if sl_mode == 0:
                level = c[t] - sl_atr * a
            elif sl_mode == 2 and use_sd and t - last_sd_bull <= w:
                level = zone_bot - sl_buffer * a
            else:
                lowest = l[t]
                for i in range(1, sl_lookback):
                    if t - i >= 0 and l[t - i] < lowest:
                        lowest = l[t - i]
                level = lowest - sl_buffer * a
            d = c[t] - level
        else:
            if sl_mode == 0:
                level = c[t] + sl_atr * a
            elif sl_mode == 2 and use_sd and t - last_sd_bear <= w:
                level = zone_top + sl_buffer * a
            else:
                highest = h[t]
                for i in range(1, sl_lookback):
                    if t - i >= 0 and h[t - i] > highest:
                        highest = h[t - i]
                level = highest + sl_buffer * a
            d = level - c[t]
        d = max(d, min_stop * a)
        d = min(d, max_stop * a)
        dist = d
        signal_bar = t
        if go_long:
            pending = 1
            stop = c[t] - d
            target = c[t] + rr * d
        else:
            pending = -1
            stop = c[t] + d
            target = c[t] - rr * d
        if tick > 0.0:
            stop = np.floor(stop / tick + 0.5) * tick
            target = np.floor(target / tick + 0.5) * tick

    return t_sig[:k], t_in[:k], t_out[:k], t_dir[:k], p_in[:k], p_out[:k], p_dist[:k], reason[:k]
