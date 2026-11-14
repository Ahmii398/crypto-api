"""The gold timeframe choice is a pure function of pre-2022 scores."""

from gsms.gold_select import decide
from gsms.pine_build import AUTO_NAME, render


def test_unoptimized_falls_back_to_the_original_settings():
    choice = decide("5m", original_score=-1.0, candidate=None)
    assert choice["status"] == "unoptimized"
    assert choice["active"] == "original"
    assert "unoptimized" in choice["chart_label"]


def test_higher_timeframes_keep_the_original_when_it_scores_higher():
    candidate = {"score": 0.2}
    choice = decide("4h", original_score=0.4, candidate=candidate)
    assert choice["status"] == "original_retained"
    assert choice["active"] == "original"


def test_higher_timeframes_switch_when_the_candidate_is_more_robust():
    candidate = {"score": 0.4}
    choice = decide("1h", original_score=-0.6, candidate=candidate)
    assert choice["status"] == "passed_pre_oos_checks"
    assert choice["active"] == "candidate"


def test_other_timeframes_take_the_candidate_that_passed():
    choice = decide("15m", original_score=-0.2, candidate={"score": 0.1})
    assert choice["active"] == "candidate"


def test_auto_preset_is_compiled_into_the_pine_source():
    gold = [{
        "timeframe": "15m", "minutes": 15, "status": "passed_pre_oos_checks",
        "chart_label": "15m selected", "chart_note": "note", "chart_color": "color.new(color.gray, 30)",
        "active_params": {"use_ema": True, "use_ls": False, "use_sd": True, "use_cp": False, "conf_window": 3, "ema_fast": 9, "ema_slow": 20, "ema_mode": 0, "ls_swing": 5, "ls_source": 0, "ls_mode": 0, "ls_reclaim": 3, "ls_max_age": 300, "sd_swing": 5, "sd_width": 1.0, "sd_strength": 1.0, "sd_max_touch": 2, "sd_inval": 0, "sd_confirm": 0, "sd_max_age": 500, "cp_mask": 15, "cp_strict": False, "cp_confirm": False, "sl_mode": 0, "sl_atr": 1.5, "sl_lookback": 10, "sl_buffer": 0.25, "min_stop": 0.5, "max_stop": 5.0, "rr": 2.0, "direction": 0, "trend_len": 0, "sess_start": -1, "sess_end": -1, "atr_len": 14, "warmup": 300},
    }]
    source = render("INDICATOR", [], default=AUTO_NAME, gold=gold)
    assert AUTO_NAME in source
    assert "autoSlot == 1" in source
    assert "wantAuto" in source
    assert "timeframe.in_seconds()" in source
