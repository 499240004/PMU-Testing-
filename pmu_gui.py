"""PyInstaller entry point for the PMU Validation Bench desktop app.

This is a thin launcher so PyInstaller has a single top-level script to freeze.
It just defers to :func:`pmu_validation.gui.main`. Build it with ``build_exe.ps1``.
"""
from __future__ import annotations

import multiprocessing
import sys

from pmu_validation.gui import main

if __name__ == "__main__":
    # Safe no-op when unfrozen; required so a frozen child process (some
    # backends spawn one) doesn't re-launch the whole GUI.
    multiprocessing.freeze_support()
    sys.exit(main())
