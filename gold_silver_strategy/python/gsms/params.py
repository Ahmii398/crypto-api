"""Strategy parameters. Field names and meanings match the Pine inputs one-to-one (see SPEC.md)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

# Enumerations shared with the Pine scripts (same order as the input dropdowns).
EMA_CROSS, EMA_TREND = 0, 1
LS_SRC_SWINGS, LS_SRC_SWINGS_PD, LS_SRC_SWINGS_PD_PW, LS_SRC_PD, LS_SRC_PD_PW = 0, 1, 2, 3, 4
LS_WICK, LS_CLOSE_CONFIRM = 0, 1
SD_INVAL_CLOSE, SD_INVAL_WICK = 0, 1
SD_TOUCH, SD_REJECTION = 0, 1
SL_ATR, SL_STRUCTURE, SL_ZONE = 0, 1, 2
DIR_BOTH, DIR_LONG, DIR_SHORT = 0, 1, 2
CP_ENGULFING, CP_PIN, CP_STAR, CP_PIERCING = 1, 2, 4, 8

LABELS = {
    "ema_mode": {EMA_CROSS: "Cross", EMA_TREND: "Trend"},
    "ls_source": {
        LS_SRC_SWINGS: "Swings",
        LS_SRC_SWINGS_PD: "Swings + PDH/PDL",
        LS_SRC_SWINGS_PD_PW: "Swings + PDH/PDL + PWH/PWL",
        LS_SRC_PD: "PDH/PDL",
        LS_SRC_PD_PW: "PDH/PDL + PWH/PWL",
    },
    "ls_mode": {LS_WICK: "Wick", LS_CLOSE_CONFIRM: "Close confirm"},
    "sd_inval": {SD_INVAL_CLOSE: "Close beyond", SD_INVAL_WICK: "Wick beyond"},
    "sd_confirm": {SD_TOUCH: "Touch", SD_REJECTION: "Rejection close"},
    "sl_mode": {SL_ATR: "ATR", SL_STRUCTURE: "Structure", SL_ZONE: "Zone"},
    "direction": {DIR_BOTH: "Both", DIR_LONG: "Long only", DIR_SHORT: "Short only"},
}


@dataclass(frozen=True)
class Params:
    # Which modules take part in the confluence, and the confluence window in bars.
    use_ema: bool = True
    use_ls: bool = True
    use_sd: bool = True
    use_cp: bool = True
    conf_window: int = 5

    # EMA module.
    ema_fast: int = 9
    ema_slow: int = 20
    ema_mode: int = EMA_CROSS

    # Liquidity-sweep module.
    ls_swing: int = 5
    ls_source: int = LS_SRC_SWINGS
    ls_mode: int = LS_WICK
    ls_reclaim: int = 3
    ls_max_age: int = 300

    # Supply/demand module.
    sd_swing: int = 10
    sd_width: float = 1.0
    sd_strength: float = 1.0
    sd_max_touch: int = 2
    sd_inval: int = SD_INVAL_CLOSE
    sd_confirm: int = SD_REJECTION
    sd_max_age: int = 500

    # Candlestick module.
    cp_mask: int = CP_ENGULFING | CP_PIN | CP_STAR | CP_PIERCING
    cp_strict: bool = False
    cp_confirm: bool = False

    # Trade management.
    sl_mode: int = SL_ATR
    sl_atr: float = 1.5
    sl_lookback: int = 10
    sl_buffer: float = 0.25
    min_stop: float = 0.5
    max_stop: float = 5.0
    rr: float = 2.0

    # Filters.
    direction: int = DIR_BOTH
    trend_len: int = 0  # 0 = off, otherwise EMA length: longs above it, shorts below it
    sess_start: int = -1  # -1 = off, otherwise signals only on bars opening in [start, end) UTC hours
    sess_end: int = -1

    atr_len: int = 14
    warmup: int = 300

    def modules(self) -> str:
        parts = [name for name, on in (("EMA", self.use_ema), ("LS", self.use_ls), ("SD", self.use_sd), ("CP", self.use_cp)) if on]
        return "+".join(parts) if parts else "none"

    def to_dict(self) -> dict:
        return asdict(self)

    def replace(self, **changes) -> "Params":
        return replace(self, **changes)

    @classmethod
    def from_dict(cls, values: dict) -> "Params":
        known = {f.name: f.type for f in fields(cls)}
        casts = {"bool": bool, "int": int, "float": float}
        return cls(**{k: casts[known[k]](v) for k, v in values.items() if k in known})

    # Cache keys: each module's output depends only on its own parameters.
    def ls_key(self) -> tuple:
        return (self.ls_swing, self.ls_source, self.ls_mode, self.ls_reclaim, self.ls_max_age)

    def sd_key(self) -> tuple:
        return (self.sd_swing, self.sd_width, self.sd_strength, self.sd_max_touch, self.sd_inval, self.sd_max_age, self.atr_len)

    def cp_key(self) -> tuple:
        return (self.cp_mask, self.cp_strict, self.cp_confirm, self.atr_len)
