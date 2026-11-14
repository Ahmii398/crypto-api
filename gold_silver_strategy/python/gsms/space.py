"""The parameter space that is searched, and helpers to sample and enumerate it.

Every value listed here can be selected in the Pine inputs. The grids follow the brief
(fast EMA 5/7/9/12/15, slow EMA 15/18/20/25/30, reward:risk 1.5/2/3/4, ...) and are kept
coarse on purpose: a fine grid mostly adds ways to fit noise.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np

from . import params as P
from .params import Params

MODULES = ("ema", "ls", "sd", "cp")
MODULE_LABEL = {"ema": "EMA", "ls": "LS", "sd": "SD", "cp": "CP"}

# All 15 non-empty module combinations, single modules first.
SUBSETS = [combo for size in range(1, 5) for combo in combinations(MODULES, size)]

SPACE = {
    "conf_window": [1, 2, 3, 5, 8, 12, 20],
    # EMA
    "ema_fast": [5, 7, 9, 12, 15],
    "ema_slow": [15, 18, 20, 25, 30],
    "ema_mode": [P.EMA_CROSS, P.EMA_TREND],
    # Liquidity sweep
    "ls_swing": [3, 5, 8, 13, 21],
    "ls_source": [P.LS_SRC_SWINGS, P.LS_SRC_SWINGS_PD, P.LS_SRC_SWINGS_PD_PW, P.LS_SRC_PD, P.LS_SRC_PD_PW],
    "ls_mode": [P.LS_WICK, P.LS_CLOSE_CONFIRM],
    "ls_reclaim": [2, 3, 5],
    "ls_max_age": [100, 300, 1000],
    # Supply / demand
    "sd_swing": [3, 5, 8, 13, 21],
    "sd_width": [0.5, 1.0, 1.5, 2.0],
    "sd_strength": [0.0, 0.5, 1.0, 2.0, 3.0],
    "sd_max_touch": [1, 2, 3, 99],
    "sd_inval": [P.SD_INVAL_CLOSE, P.SD_INVAL_WICK],
    "sd_confirm": [P.SD_TOUCH, P.SD_REJECTION],
    "sd_max_age": [200, 500, 2000],
    # Candlestick patterns: every non-empty combination of the four pattern families
    "cp_mask": list(range(1, 16)),
    "cp_strict": [False, True],
    "cp_confirm": [False, True],
    # Trade management
    "sl_mode": [P.SL_ATR, P.SL_STRUCTURE, P.SL_ZONE],
    "sl_atr": [1.0, 1.5, 2.0, 3.0],
    "sl_lookback": [5, 10, 20],
    "sl_buffer": [0.1, 0.25, 0.5],
    "rr": [1.5, 2.0, 3.0, 4.0],
    # Filters
    "direction": [P.DIR_BOTH, P.DIR_LONG, P.DIR_SHORT],
    "trend_len": [0, 100, 200],
    "session": [(-1, -1), (7, 20), (12, 20), (0, 7)],  # off, London+New York, New York, Asia (UTC hours)
}

_DEFAULT = Params()
_OWNED = {
    "ema": ("ema_fast", "ema_slow", "ema_mode"),
    "ls": ("ls_swing", "ls_source", "ls_mode", "ls_reclaim", "ls_max_age"),
    "sd": ("sd_swing", "sd_width", "sd_strength", "sd_max_touch", "sd_inval", "sd_confirm", "sd_max_age"),
    "cp": ("cp_mask", "cp_strict", "cp_confirm"),
}


def subset_label(subset) -> str:
    return "+".join(MODULE_LABEL[m] for m in MODULES if m in subset)


def with_modules(p: Params, subset) -> Params:
    return p.replace(**{f"use_{m}": m in subset for m in MODULES})


def normalize(p: Params) -> Params:
    """Reset every setting that cannot influence the result, so equal strategies compare equal."""
    changes = {}
    for module, owned in _OWNED.items():
        if not getattr(p, f"use_{module}"):
            changes.update({name: getattr(_DEFAULT, name) for name in owned})
    # The window only matters when two or more modules have to line up in time. A module that
    # triggers alone is in context on its own signal bar, and an EMA in Trend mode is a state.
    timed = [m for m in MODULES if getattr(p, f"use_{m}") and not (m == "ema" and p.ema_mode == P.EMA_TREND)]
    if len(timed) <= 1:
        changes["conf_window"] = 1
    if p.use_ls and p.ls_mode == P.LS_WICK:
        changes["ls_reclaim"] = _DEFAULT.ls_reclaim
    if p.use_ls and p.ls_source in (P.LS_SRC_PD, P.LS_SRC_PD_PW):
        changes["ls_swing"], changes["ls_max_age"] = _DEFAULT.ls_swing, _DEFAULT.ls_max_age
    sl_mode = p.sl_mode
    if sl_mode == P.SL_ZONE and not p.use_sd:
        sl_mode = changes["sl_mode"] = P.SL_STRUCTURE
    if sl_mode == P.SL_ATR:
        changes["sl_lookback"], changes["sl_buffer"] = _DEFAULT.sl_lookback, _DEFAULT.sl_buffer
    else:
        changes["sl_atr"] = _DEFAULT.sl_atr
    return p.replace(**changes) if changes else p


def is_valid(p: Params) -> bool:
    if not (p.use_ema or p.use_ls or p.use_sd or p.use_cp):
        return False
    if p.use_ema and p.ema_fast >= p.ema_slow:
        return False
    # In Trend mode the EMA is only a filter; another module has to provide the trigger.
    if p.use_ema and p.ema_mode == P.EMA_TREND and not (p.use_ls or p.use_sd or p.use_cp):
        return False
    return True


def sample(rng: np.random.Generator, subset=None, require=()) -> Params:
    """One random strategy. `subset` fixes the modules; `require` forces modules on."""
    while True:
        if subset is None:
            chosen = set(SUBSETS[rng.integers(len(SUBSETS))]) | set(require)
        else:
            chosen = set(subset)
        values = {name: options[rng.integers(len(options))] for name, options in SPACE.items()}
        values["sess_start"], values["sess_end"] = values.pop("session")
        p = normalize(with_modules(Params(**values), chosen))
        if is_valid(p):
            return p


def neighbours(p: Params) -> list[Params]:
    """Every strategy one grid step away from `p` in a single numeric setting."""
    out = []
    for name, options in SPACE.items():
        if name in ("session", "cp_mask") or isinstance(options[0], bool):
            continue
        if name in ("ema_mode", "ls_source", "ls_mode", "sd_inval", "sd_confirm", "sl_mode", "direction"):
            continue
        value = getattr(p, name)
        if value not in options:
            continue
        index = options.index(value)
        for step in (-1, 1):
            if 0 <= index + step < len(options):
                q = normalize(p.replace(**{name: options[index + step]}))
                if q != p and is_valid(q):
                    out.append(q)
    return list(dict.fromkeys(out))
