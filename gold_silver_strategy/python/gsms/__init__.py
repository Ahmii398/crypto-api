"""Gold/Silver multi-strategy research framework (data, engine, optimisation, reporting)."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = PROJECT_ROOT / "results"

SYMBOLS = ("XAUUSD", "XAGUSD")
TIMEFRAMES = ("5m", "15m", "30m", "1h", "2h", "4h", "1D")
TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1D": 1440}
