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

import time

from .._vendor import import_scope
from ..virtualbench import DEFAULT_BENCH, VirtualBench
from ..harmonics import analyze as analyze_harmonics

# The Infiniium returns this sentinel when a measurement has no valid result
# yet (e.g. immediately after :RUN, before an acquisition has completed).
_SCOPE_INVALID = 9.9e37


class Mso8104Scope:
    """Real MSO8104A over Ethernet/VISA."""

    def __init__(self, ip: str, channel: int = 1, timeout_ms: int = 10000,
                 probe_atten: float = 1.0, coupling: str = "DC"):
        self.ip = ip
        self.channel = channel
        self.timeout_ms = timeout_ms
        # A high-voltage differential probe (e.g. 200:1 on L-N) needs its ratio
        # entered here AND a high-Z (1 MOhm, "DC") input -- a 50-ohm ("DC50")
        # input loads the probe and halves it. With the ratio unset the scope
        # reports the raw BNC volts (~400x low). We apply both on open() so the
        # measurement can't silently regress on a power-cycle / Default Setup.
        self.probe_atten = probe_atten
        self.coupling = coupling
        self._scope = None
        self._mso = None

    def open(self) -> "Mso8104Scope":
        mso8104a, _measurements = import_scope()
        self._mso = mso8104a
        self._scope = mso8104a.MSO8104A(self.ip, timeout_ms=self.timeout_ms)
        self._scope.__enter__()
        self._scope.set_channel(self.channel, display=True, coupling=self.coupling)
        if self.probe_atten and self.probe_atten != 1.0:
            self._scope.write(f":CHANnel{self.channel}:PROBe {self.probe_atten:g}")
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

    def _poll(self, cmd: str, deadline_s: float = 3.0) -> float:
        """Query a measurement, retrying past the 'not available yet' sentinel."""
        t0 = time.monotonic()
        while True:
            try:
                v = self._scope.query_float(cmd)
                if v < _SCOPE_INVALID:
                    return v
            except Exception:                       # noqa: BLE001 (TMO on empty)
                pass
            if time.monotonic() - t0 > deadline_s:
                return float("nan")
            time.sleep(0.15)

    def read(self, navg: int = 1) -> dict:
        src = f"CHANnel{self.channel}"
        # Steady-state line signal -> measure while free-running, not SINGle
        # (SINGle blocks the query until a trigger event). AUTO sweep keeps
        # acquisitions flowing even without a clean edge. VRMS on the 8000-
        # series Infiniium requires an interval/type qualifier; the bare
        # ":MEASure:VRMS? CHANnel1" form times out (verified on MY45002120).
        self._scope.run()
        self._scope.write(":TRIGger:SWEep AUTO")
        self._scope.set_trigger_edge(src, level=0.0, slope="POSitive")
        time.sleep(0.3)
        vr, fr, vp = [], [], []
        for _ in range(max(1, navg)):
            vr.append(self._poll(f":MEASure:VRMS? DISPlay,AC,{src}"))
            fr.append(self._poll(f":MEASure:FREQuency? {src}"))
            vp.append(self._poll(f":MEASure:VPP? {src}"))
        avg = lambda xs: sum(xs) / len(xs)
        return {"vrms": avg(vr), "freq": avg(fr), "vpp": avg(vp)}

    def read_phase(self, zc_channel: int, ref_channel: int, *,
                   f0: float = 60.0) -> float:
        """Phase (deg) of ``zc_channel`` relative to ``ref_channel``.

        On this bench CH3 is the PMU's zero-cross output and CH1 is the line
        source (same node the DMM sees), so this returns the PMU's timing
        offset from the line. The absolute value is a fixed hardware delay --
        its *constancy* over a run is the PMU's phase stability. Triggers on
        the reference channel and measures over ~3 cycles. Returns NaN if the
        scope can't resolve an edge on both channels."""
        zc = f"CHANnel{zc_channel}"
        ref = f"CHANnel{ref_channel}"
        self._scope.set_channel(zc_channel, display=True)
        self._scope.set_channel(ref_channel, display=True)
        self._scope.set_timebase(scale=(3.0 / f0) / 10.0)
        self._scope.run()
        self._scope.write(":TRIGger:SWEep AUTO")
        self._scope.set_trigger_edge(ref, level=0.0, slope="POSitive")
        time.sleep(0.3)
        # Infiniium :MEASure:PHASe? <src1>,<src2> -> phase of src1 wrt src2.
        return self._poll(f":MEASure:PHASe? {zc},{ref}")

    def capture_waveform(self, channel: int | None = None, *, cycles: int = 6,
                         f0: float = 60.0, max_points: int = 20000):
        """Return ``(t_seconds, volts)`` for one acquisition spanning ~``cycles``
        of ``f0`` on ``channel`` (defaults to this scope's channel), already
        scaled by the probe ratio. Point count is capped so a full-memory
        Infiniium record doesn't stall the transfer."""
        ch = channel or self.channel
        self._scope.set_channel(ch, display=True)
        self._scope.set_timebase(scale=(cycles / f0) / 10.0)
        self._scope.set_trigger_edge(f"CHANnel{ch}", level=0.0, slope="POSitive")
        t, v = self._scope.capture_waveform(channel=ch, points=max_points)
        return t, v

    def read_spectrum(self, f0: float, n_harmonics: int = 13, cycles: int = 8):
        """Capture a waveform spanning ~``cycles`` of ``f0`` and return a
        :class:`~pmu_validation.harmonics.HarmonicResult`."""
        t, v = self.capture_waveform(cycles=cycles, f0=f0)
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

    def read_phase(self, zc_channel: int, ref_channel: int, *,
                   f0: float = 60.0) -> float:
        """Simulated fixed ZC-vs-source offset with a little phase noise so the
        stability display has something to show."""
        return (self.bench.scope_phase_deg()
                if hasattr(self.bench, "scope_phase_deg") else 2.0)

    def capture_waveform(self, channel: int | None = None, *, cycles: int = 6,
                         f0: float = 60.0, max_points: int = 20000):
        """Synthesize a bench waveform spanning ~``cycles`` of ``f0``."""
        fs = 20000.0
        seconds = max(cycles / f0, 0.05)
        return self.bench.waveform(fs, seconds)

    def read_spectrum(self, f0: float, n_harmonics: int = 13, cycles: int = 8):
        """Synthesize the bench waveform (fundamental + harmonics + scope gain
        error/noise) and analyze it exactly like the real scope path."""
        fs = 20000.0
        seconds = max(cycles / f0, 0.4)
        t, v = self.bench.waveform(fs, seconds)
        return analyze_harmonics(v, fs, f0, n_harmonics)


def make_scope(simulate: bool, *, ip: str | None = None, channel: int = 1,
               probe_atten: float = 1.0, coupling: str = "DC",
               bench: VirtualBench | None = None):
    if simulate:
        return SimScope(bench)
    if not ip:
        raise ValueError("a real MSO8104A needs --scope-ip <addr>")
    return Mso8104Scope(ip, channel=channel, probe_atten=probe_atten,
                        coupling=coupling)
