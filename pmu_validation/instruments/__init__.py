"""Instrument adapters wrapping the four submodule apps behind one interface.

Each factory returns either a real-hardware adapter or a simulate-mode adapter
that shares a :class:`~pmu_validation.virtualbench.VirtualBench`.
"""
from .source import make_source, Hp3325Source, SimSource
from .dmm import make_dmm, Hp34401Dmm, SimDmm
from .scope import make_scope, Mso8104Scope, SimScope
from .pmu import make_pmu, SerialPmu, SimPmu

__all__ = [
    "make_source", "Hp3325Source", "SimSource",
    "make_dmm", "Hp34401Dmm", "SimDmm",
    "make_scope", "Mso8104Scope", "SimScope",
    "make_pmu", "SerialPmu", "SimPmu",
]
