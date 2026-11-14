"""Build the Pine indicator and strategy from the shared template.

    python -m gsms.pine_build

Both scripts come from pine/src/gsms.template.pine, so their signal logic cannot drift apart.
Presets are read from results/final_configs.json (written by the research pipeline) and
compiled into the scripts as constants, so the parameters in TradingView are the tested ones
without any hand transcription.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import PROJECT_ROOT, RESULTS_DIR, TF_MINUTES
from .params import LABELS, Params

TEMPLATE = PROJECT_ROOT / "pine" / "src" / "gsms.template.pine"
OUTPUTS = {"INDICATOR": PROJECT_ROOT / "pine" / "GSMS_Indicator.pine", "STRATEGY": PROJECT_ROOT / "pine" / "GSMS_Strategy.pine"}
FINAL_CONFIGS = RESULTS_DIR / "final_configs.json"
PRESET_CUSTOM = "Custom (inputs below)"

# (Pine variable, Pine type, input it falls back to, value taken from Params)
_FIELDS = [
    ("useEma", "bool", "i_useEma", lambda p: p.use_ema),
    ("useLs", "bool", "i_useLs", lambda p: p.use_ls),
    ("useSd", "bool", "i_useSd", lambda p: p.use_sd),
    ("useCp", "bool", "i_useCp", lambda p: p.use_cp),
    ("confWindow", "int", "i_confWindow", lambda p: p.conf_window),
    ("emaFastLen", "int", "i_emaFast", lambda p: p.ema_fast),
    ("emaSlowLen", "int", "i_emaSlow", lambda p: p.ema_slow),
    ("emaMode", "string", "i_emaMode", lambda p: LABELS["ema_mode"][p.ema_mode]),
    ("lsSwing", "int", "i_lsSwing", lambda p: p.ls_swing),
    ("lsSource", "string", "i_lsSource", lambda p: LABELS["ls_source"][p.ls_source]),
    ("lsMode", "string", "i_lsMode", lambda p: LABELS["ls_mode"][p.ls_mode]),
    ("lsReclaim", "int", "i_lsReclaim", lambda p: p.ls_reclaim),
    ("lsMaxAge", "int", "i_lsMaxAge", lambda p: p.ls_max_age),
    ("sdSwing", "int", "i_sdSwing", lambda p: p.sd_swing),
    ("sdWidth", "float", "i_sdWidth", lambda p: p.sd_width),
    ("sdStrength", "float", "i_sdStrength", lambda p: p.sd_strength),
    ("sdMaxTouch", "int", "i_sdMaxTouch", lambda p: p.sd_max_touch),
    ("sdInval", "string", "i_sdInval", lambda p: LABELS["sd_inval"][p.sd_inval]),
    ("sdConfirm", "string", "i_sdConfirm", lambda p: LABELS["sd_confirm"][p.sd_confirm]),
    ("sdMaxAge", "int", "i_sdMaxAge", lambda p: p.sd_max_age),
    ("cpEngulf", "bool", "i_cpEngulf", lambda p: bool(p.cp_mask & 1)),
    ("cpPin", "bool", "i_cpPin", lambda p: bool(p.cp_mask & 2)),
    ("cpStar", "bool", "i_cpStar", lambda p: bool(p.cp_mask & 4)),
    ("cpPierce", "bool", "i_cpPierce", lambda p: bool(p.cp_mask & 8)),
    ("cpStrict", "bool", "i_cpStrict", lambda p: p.cp_strict),
    ("cpConfirm", "bool", "i_cpConfirm", lambda p: p.cp_confirm),
    ("slMode", "string", "i_slMode", lambda p: LABELS["sl_mode"][p.sl_mode]),
    ("slAtr", "float", "i_slAtr", lambda p: p.sl_atr),
    ("slLookback", "int", "i_slLookback", lambda p: p.sl_lookback),
    ("slBuffer", "float", "i_slBuffer", lambda p: p.sl_buffer),
    ("minStop", "float", "i_minStop", lambda p: p.min_stop),
    ("maxStop", "float", "i_maxStop", lambda p: p.max_stop),
    ("rr", "float", "i_rr", lambda p: p.rr),
    ("direction", "string", "i_direction", lambda p: LABELS["direction"][p.direction]),
    ("trendLen", "int", "i_trendLen", lambda p: p.trend_len),
    ("sessStart", "int", "i_sessStart", lambda p: p.sess_start),
    ("sessEnd", "int", "i_sessEnd", lambda p: p.sess_end),
    ("atrLen", "int", "i_atrLen", lambda p: p.atr_len),
    ("warmup", "int", "i_warmup", lambda p: p.warmup),
]

_DERIVED = """bool emaCross = emaMode == "Cross"
bool lsWick = lsMode == "Wick"
bool lsUseSwings = str.startswith(lsSource, "Swings")
bool lsUsePd = str.contains(lsSource, "PDH")
bool lsUsePw = str.contains(lsSource, "PWH")
bool sdInvalClose = sdInval == "Close beyond"
bool sdTouchOnly = sdConfirm == "Touch\""""


def _literal(kind: str, value) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind == "int":
        return str(int(value))
    if kind == "float":
        text = repr(float(value))
        return text if "." in text or "e" in text else text + ".0"
    return json.dumps(str(value), ensure_ascii=False)


def _conditionals(source: str, active: set[str]) -> str:
    """Resolve //#if NAME ... //#else ... //#endif blocks; a block is kept when NAME is active."""
    out, keep, in_block = [], True, False
    for line in source.splitlines():
        tag = line.strip()
        if tag.startswith("//#if "):
            assert not in_block, "nested //#if is not supported"
            in_block, keep = True, tag.split()[1] in active
        elif tag == "//#else":
            assert in_block
            keep = not keep
        elif tag == "//#endif":
            assert in_block
            in_block, keep = False, True
        elif keep:
            out.append(line)
    assert not in_block, "unterminated //#if"
    return "\n".join(out) + "\n"


def load_presets(path: Path = FINAL_CONFIGS) -> list[dict]:
    """[{name, symbol, timeframe, params}] in file order; empty when the file does not exist."""
    if not path.exists():
        return []
    return json.loads(path.read_text())["presets"]


def render(target: str, presets: list[dict], default: str = PRESET_CUSTOM, debug: bool = False) -> str:
    """Pine source for `target` ("INDICATOR" or "STRATEGY").

    `default` selects the preset the script starts with; `debug` adds plots of the internal
    series. Both exist for the parity check and are not used for the shipped scripts.
    """
    names = [PRESET_CUSTOM] + [preset["name"] for preset in presets]
    assert len(set(names)) == len(names), "preset names must be unique"
    assert default in names, f"unknown default preset {default!r}"
    params = [Params.from_dict(preset["params"]) for preset in presets]

    tooltip = "A preset replaces every setting in groups 1-8. Presets are the settings that tested best on 2010-2021 data for one symbol and timeframe. They are not a performance promise: read REPORT.md for their out-of-sample results before relying on them."
    options = (
        f"string i_preset = input.string({_literal('string', default)}, \"Preset\", options = [{', '.join(_literal('string', n) for n in names)}], tooltip = {_literal('string', tooltip)})"
    )

    lines = []
    chain = "".join(f"i_preset == {_literal('string', preset['name'])} ? {k} : " for k, preset in enumerate(presets, start=1))
    lines.append(f"int presetId = {chain}0")
    for field, kind, fallback, getter in _FIELDS:
        chain = "".join(f"presetId == {k} ? {_literal(kind, getter(p))} : " for k, p in enumerate(params, start=1))
        lines.append(f"{kind} {field} = {chain}{fallback}")
    # What each preset was tested on, for the on-chart mismatch warning.
    tested = [
        ("string", "presetSymbol", lambda preset: preset["symbol"], '""'),
        ("string", "presetMetal", lambda preset: preset["symbol"][:3], '""'),
        ("string", "presetTf", lambda preset: preset["timeframe"], '""'),
        ("int", "presetTfSec", lambda preset: TF_MINUTES[preset["timeframe"]] * 60, "0"),
    ]
    for kind, label, getter, fallback in tested:
        chain = "".join(f"presetId == {k} ? {_literal(kind, getter(preset))} : " for k, preset in enumerate(presets, start=1))
        lines.append(f"{kind} {label} = {chain}{fallback}")
    lines.append(_DERIVED)

    source = _conditionals(TEMPLATE.read_text(), {target, "DEBUG", f"DEBUG_{target}"} if debug else {target})
    for placeholder, text in (("//#presets:options", options), ("//#presets:values", "\n".join(lines))):
        assert source.count(placeholder) == 1, placeholder
        source = source.replace(placeholder, text)
    assert "//#" not in source, "unresolved template directive"
    return source


def build(presets: list[dict] | None = None) -> dict[str, Path]:
    presets = load_presets() if presets is None else presets
    for target, path in OUTPUTS.items():
        path.write_text(render(target, presets))
    return OUTPUTS


def main() -> None:
    presets = load_presets()
    for target, path in build(presets).items():
        print(f"{target.lower():9s} -> {path.relative_to(PROJECT_ROOT)}  ({len(path.read_text().splitlines())} lines, {len(presets)} presets)")


if __name__ == "__main__":
    main()
