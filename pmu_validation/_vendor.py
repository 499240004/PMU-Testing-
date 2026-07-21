"""Import bridge to the four instrument apps carried as git submodules.

The apps under ``apps/`` are the *unmodified* upstream repositories (each still
fully usable on its own). Rather than fork or repackage them, we add their
source roots to ``sys.path`` on demand and import their driver layers directly:

    apps/hp3325/hp3325b_driver.py      -> HP3325B
    apps/hp34401/hp34401/  (package)   -> HP34401A, SimulatedHP34401A
    apps/scope/mso8104a.py             -> MSO8104A  (+ measurements.py)
    apps/power-brick/host/upmu/ (pkg)  -> PmuEngine, Simulator, ...

Every importer here is lazy: importing :mod:`pmu_validation` pulls in nothing
that would require pyserial / pyvisa until you actually open that instrument.
If a submodule is missing (someone cloned without ``--recurse-submodules``) we
raise a clear, actionable error instead of a bare ``ModuleNotFoundError``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repo root = two levels up from this file (pmu_validation/_vendor.py).
ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"

# Source root that must be on sys.path for each app's import to resolve.
_SRC_3325 = APPS / "hp3325"
_SRC_34401 = APPS / "hp34401"
_SRC_SCOPE = APPS / "scope"
_SRC_UPMU = APPS / "power-brick" / "host"


def _ensure(path: Path, marker: str, app: str) -> None:
    """Put *path* on sys.path, or explain how to obtain the missing submodule."""
    # In a frozen build the app modules are collected straight into the bundle
    # and are importable by name -- there is no ``apps/`` tree to add.
    if getattr(sys, "frozen", False):
        return
    if not (path / marker).exists():
        raise ModuleNotFoundError(
            f"Cannot find '{marker}' under {path}. The '{app}' submodule looks "
            f"uninitialised. From the repo root run:\n"
            f"    git submodule update --init --recursive"
        )
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)


# --- HP 3325B function generator ------------------------------------------- #
def import_hp3325():
    _ensure(_SRC_3325, "hp3325b_driver.py", "HP-3325-Function-Generator-App")
    import hp3325b_driver  # noqa: E402
    return hp3325b_driver


# --- HP 34401A bench DMM ---------------------------------------------------- #
def import_hp34401():
    _ensure(_SRC_34401, "hp34401", "HP-34401-Stream")
    from hp34401 import HP34401A  # noqa: E402
    return HP34401A


def import_hp34401_sim():
    _ensure(_SRC_34401, "hp34401", "HP-34401-Stream")
    from hp34401.simulator import SimulatedHP34401A  # noqa: E402
    return SimulatedHP34401A


# --- MSO8104A oscilloscope -------------------------------------------------- #
def import_scope():
    _ensure(_SRC_SCOPE, "mso8104a.py", "VISA-SCOPE-communication")
    import mso8104a  # noqa: E402
    import measurements  # noqa: E402  (host-side waveform math)
    return mso8104a, measurements


# --- micro-PMU host (upmu package) ------------------------------------------ #
def import_upmu():
    _ensure(_SRC_UPMU, "upmu", "Power-Brick-Testing")
    from upmu.config import HostConfig, ReferencePhasor  # noqa: E402
    from upmu.engine import PmuEngine  # noqa: E402
    from upmu.receiver import SerialReceiver  # noqa: E402
    from upmu.simulator import Simulator  # noqa: E402
    from upmu import sources, config as upmu_config, burstfft  # noqa: E402
    return {
        "HostConfig": HostConfig,
        "ReferencePhasor": ReferencePhasor,
        "PmuEngine": PmuEngine,
        "SerialReceiver": SerialReceiver,
        "Simulator": Simulator,
        "sources": sources,
        "config": upmu_config,
        "burstfft": burstfft,
    }
