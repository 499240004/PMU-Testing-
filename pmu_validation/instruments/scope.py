"""Scope adapter: the Agilent/Keysight MSO8104A -- the waveform reference.

The scope is the fast, high-fidelity anchor for frequency (and, later, phase and
THD). For the v1 magnitude+frequency suite we pull Vrms, frequency and Vpp per
test point.

Interface used by the sequencer:

    open() / close()
    identify() -> str
    read(navg=1) -> {"vrms": float, "freq": float, "vpp": float}
"""
from __future__ import annotations

from .._vendor import import_scope
from ..virtualbench import DEFAULT_BENCH, VirtualBench
from ..harmonics import analyze as analyze_harmonics


class Mso8104Scope:
    """Real MSO8104A over Ethernet/VISA."""

    def __init__(self, ip: str, channel: int = 1, timeout_ms: int = 10000):
        self.ip = ip
        self.channel = channel
        self.timeout_ms = timeout_ms
        self._scope = None
        self._mso = None

    def open(self) -> "Mso8104Scope":
        mso8104a, _measurements = import_scope()
        self._mso = mso8104a
        self._scope = mso8104a.MSO8104A(self.ip, timeout_ms=self.timeout_ms)
        self._scope.__enter__()
        self._scope.set_channel(self.channel, display=True)
        return self

    def close(self) -> None:
        if self._scope is not None:
            self._scope.__exit__(None, None, None)
            self._scope = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def identify(self) -> str:
        return self._scope.idn()

    def read(self, navg: int = 1) -> dict:
        src = f"CHANnel{self.channel}"
        vr, fr, vp = [], [], []
        for _ in range(max(1, navg)):
            self._scope.single()
            vr.append(self._scope.measure("VRMS", src))
            fr.append(self._scope.measure("FREQuency", src))
            vp.append(self._scope.measure("VPP", src))
        avg = lambda xs: sum(xs) / len(xs)
        return {"vrms": avg(vr), "freq": avg(fr), "vpp": avg(vp)}

    def read_spectrum(self, f0: float, n_harmonics: int = 13, cycles: int = 8):
        """Capture a waveform spanning ~``cycles`` of ``f0`` and return a
        :class:`~pmu_validation.harmonics.HarmonicResult`."""
        # Slow the timebase so the record holds enough cycles for resolution.
        self._scope.set_timebase(scale=(cycles / f0) / 10.0)
        self._scope.single()
        t, v = self._scope.capture_waveform(channel=self.channel)
        if t.size < 2:
            return None
        fs = 1.0 / float(t[1] - t[0])
        return analyze_harmonics(v, fs, f0, n_harmonics)


class SimScope:
    """Simulated scope: reads the shared bench."""

    def __init__(self, bench: VirtualBench | None = None):
        self.bench = bench or DEFAULT_BENCH

    def open(self) -> "SimScope":
        return self

    def close(self) -> None:
        pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def identify(self) -> str:
        return "AGILENT TECHNOLOGIES,MSO8104A,SIMULATED,0"

    def read(self, navg: int = 1) -> dict:
        import math
        vr, fr = [], []
        for _ in range(max(1, navg)):
            vr.append(self.bench.scope_vrms())
            fr.append(self.bench.scope_freq())
        avg = lambda xs: sum(xs) / len(xs)
        vrms = avg(vr)
        return {"vrms": vrms, "freq": avg(fr), "vpp": vrms * 2.0 * math.sqrt(2.0)}

    def read_spectrum(self, f0: float, n_harmonics: int = 13, cycles: int = 8):
        """Synthesize the bench waveform (fundamental + harmonics + scope gain
        error/noise) and analyze it exactly like the real scope path."""
        fs = 20000.0
        seconds = max(cycles / f0, 0.4)
        t, v = self.bench.waveform(fs, seconds)
        return analyze_harmonics(v, fs, f0, n_harmonics)


def make_scope(simulate: bool, *, ip: str | None = None, channel: int = 1,
               bench: VirtualBench | None = None):
    if simulate:
        return SimScope(bench)
    if not ip:
        raise ValueError("a real MSO8104A needs --scope-ip <addr>")
    return Mso8104Scope(ip, channel=channel)
