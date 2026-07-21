"""Writable- and bundle-location helpers that work both in-tree and frozen.

When run from source, everything lives under the repo root (unchanged
behaviour). When run as a PyInstaller ``.exe`` the package files sit inside a
read-only bundle, so anything we *write* (calibration, result CSVs) has to go to
a real per-user directory instead. These helpers centralise that decision so the
rest of the code never has to test ``sys.frozen`` itself.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# True when running inside a PyInstaller (or similar) frozen build.
FROZEN = bool(getattr(sys, "frozen", False))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """A guaranteed-writable directory for this app's persistent data.

    Frozen: ``%LOCALAPPDATA%\\PMU-Validation`` (created on demand).
    Source: the repo root, so a dev checkout keeps writing in place.
    """
    if FROZEN:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PMU-Validation"
    else:
        base = _repo_root()
    base.mkdir(parents=True, exist_ok=True)
    return base


def results_dir() -> Path:
    """Where run/monitor CSVs and plots are written (``<data_dir>/results``)."""
    d = data_dir() / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundle_dir() -> Path:
    """Read-only root of files shipped with the app.

    Frozen: PyInstaller's ``sys._MEIPASS`` unpack dir. Source: the repo root.
    """
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", _repo_root()))
    return _repo_root()
