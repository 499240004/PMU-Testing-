"""Persistent host-side calibration store for the micro-PMU.

The board streams raw ADC counts; the host multiplies by ``volts_per_count`` to
recover grid volts. That scale is the one bench-tuned calibration constant (see
:data:`upmu.config.DEFAULT_VOLTS_PER_COUNT`). The firmware has **no command
channel** -- the USB protocol is stream-only -- so a calibration can't be written
into the board. Instead we persist the calibrated value here, and the PMU
adapter loads it on ``open()`` whenever no explicit value is supplied.

One JSON file per checkout (``pmu_validation/calibration.json``); it's specific
to the physical unit on the bench, so it is git-ignored rather than committed.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .paths import FROZEN, data_dir, bundle_dir

# Where the live calibration is read from / written to. In a frozen build the
# package dir is read-only, so persist under the per-user data dir instead.
CAL_PATH = (data_dir() / "calibration.json") if FROZEN \
    else Path(__file__).with_name("calibration.json")

# Read-only calibration shipped inside the bundle, used as a first-run default
# when no user calibration exists yet (frozen builds only).
_SEED_PATH = bundle_dir() / "calibration.json"


def _read_first(*paths: Path):
    for p in paths:
        try:
            return json.loads(p.read_text())
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            continue
    return None


def load_calibration(path: str | Path | None = None) -> float | None:
    """Return the stored ``volts_per_count`` (a positive float), or ``None`` if
    there is no valid calibration file yet.

    With no explicit path, the live file wins; a frozen build falls back to the
    calibration shipped in the bundle so the app is accurate out of the box."""
    if path:
        data = _read_first(Path(path))
    elif FROZEN:
        data = _read_first(CAL_PATH, _SEED_PATH)
    else:
        data = _read_first(CAL_PATH)
    try:
        vpc = float(data["volts_per_count"])
        return vpc if vpc > 0 else None
    except (TypeError, KeyError, ValueError):
        return None


def load_meta(path: str | Path | None = None) -> dict | None:
    """Return the full calibration record (value + provenance), or ``None``."""
    if path:
        return _read_first(Path(path))
    if FROZEN:
        return _read_first(CAL_PATH, _SEED_PATH)
    return _read_first(CAL_PATH)


def save_calibration(volts_per_count: float, *, dmm_ref_vrms: float | None = None,
                     n_points: int | None = None, spread_pct: float | None = None,
                     note: str = "", path: str | Path | None = None) -> Path:
    """Persist a calibrated ``volts_per_count`` with provenance for traceability."""
    p = Path(path) if path else CAL_PATH
    record = {
        "volts_per_count": float(volts_per_count),
        "calibrated_local": datetime.now().isoformat(timespec="seconds"),
        "dmm_ref_vrms": dmm_ref_vrms,
        "n_points": n_points,
        "spread_pct": spread_pct,
        "note": note,
    }
    p.write_text(json.dumps(record, indent=2))
    return p
