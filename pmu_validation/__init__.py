"""pmu-validation: an automated validation bench for the Elastic Energy micro-PMU.

Drives an HP 3325B function generator as the stimulus and cross-checks the PMU's
reported magnitude / frequency / TVE against an HP 34401A DMM (accurate amplitude
reference) and an MSO8104A oscilloscope (waveform / frequency reference).

The four instrument apps are consumed unmodified as git submodules under
``apps/`` (see :mod:`pmu_validation._vendor`); each remains fully usable on its
own. Everything here also runs with **no hardware** via a coordinated
:class:`~pmu_validation.virtualbench.VirtualBench` (``--simulate``).
"""
from __future__ import annotations

__version__ = "0.1.0"

from .virtualbench import VirtualBench
from .sequencer import TestPoint, PointResult, run_sweep

__all__ = ["VirtualBench", "TestPoint", "PointResult", "run_sweep", "__version__"]
