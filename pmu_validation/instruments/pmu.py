"""PMU adapter: the device under test (Elastic Energy micro-PMU).

Wraps the ``upmu`` host pipeline -- ``Simulator``/serial source ->
``SerialReceiver`` -> ``PmuEngine`` -- and exposes averaged reports to the
sequencer:

    open() / close()
    arm(freq_hz, vrms, phase_deg)   -- (re)point the DUT at the current signal
    read(navg, timeout_s) -> {"freq","vmag","phase","rocof","tve","synced","n"}
    volts_per_count                 -- the front-end scale currently in use

``arm()`` is where real and simulated diverge:

* **Real hardware** -- the physical 3325B already drives the board, so ``arm()``
  just notes the reference phasor; the running receiver keeps producing reports.
* **Simulate** -- there is no wire, so ``arm()`` tears down any previous
  in-process ``Simulator`` pipeline and starts a fresh one generating the signal
  the bench was just commanded to, including a deliberate front-end scale error
  (see :class:`~pmu_validation.virtualbench.VirtualBench`) so the amplitude
  calibration test recovers a real correction factor.
"""
from __future__ import annotations

import time

from .._vendor import import_upmu
from ..virtualbench import DEFAULT_BENCH, VirtualBench


class _PmuBase:
    def __init__(self, volts_per_count: float | None = None,
                 report_rate: float = 10.0):
        u = import_upmu()
        self._u = u
        cfg = u["HostConfig"]()
        if volts_per_count is not None:
            cfg.volts_per_count = volts_per_count
        cfg.report_rate = report_rate
        self.cfg = cfg
        self.engine = None
        self.receiver = None
        self._source = None

    @property
    def volts_per_count(self) -> float:
        return self.cfg.volts_per_count

    def identify(self) -> str:
        st = getattr(self.engine, "status", None) if self.engine else None
        if st is not None:
            return (f"Elastic Energy micro-PMU fw {st.fw_version_major}."
                    f"{st.fw_version_minor} (adc_id=0x{st.adc_id:02X})")
        return (f"Elastic Energy micro-PMU (upmu host, "
                f"volts_per_count={self.cfg.volts_per_count:.6g})")

    def _start_pipeline(self, source) -> None:
        self._teardown_pipeline()
        # Reference phasor for TVE is the commanded signal (set in arm()).
        self.engine = self._u["PmuEngine"](self.cfg)
        self.receiver = self._u["SerialReceiver"](source, self.engine.on_message)
        self._source = source
        self.receiver.start()

    def _teardown_pipeline(self) -> None:
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None
        if self._source is not None:
            try:
                self._source.close()
            except Exception:
                pass
            self._source = None
        self.engine = None

    def _set_reference(self, freq_hz: float, vrms: float, phase_deg: float) -> None:
        self.cfg.reference.magnitude = vrms
        self.cfg.reference.frequency = freq_hz
        self.cfg.reference.phase_deg = phase_deg

    def close(self) -> None:
        self._teardown_pipeline()

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def read(self, navg: int = 5, timeout_s: float = 8.0,
             require_sync: bool = True) -> dict:
        """Poll the engine for ``navg`` valid reports and average them.

        Returns partial results (with the count actually obtained) rather than
        raising, so a point that never locks still records *why*.
        """
        got = []
        deadline = time.monotonic() + timeout_s
        period = 1.0 / max(1.0, self.cfg.report_rate)
        while len(got) < navg and time.monotonic() < deadline:
            time.sleep(period)
            r = self.engine.report() if self.engine else None
            if r is None:
                continue
            if require_sync and not r.synced:
                continue
            got.append(r)
        if not got:
            return {"freq": None, "vmag": None, "phase": None, "rocof": None,
                    "tve": None, "synced": False, "n": 0}
        n = len(got)
        mean = lambda f: sum(f(r) for r in got) / n
        return {
            "freq": mean(lambda r: r.freq),
            "vmag": mean(lambda r: r.vmag_phasor),
            "phase": mean(lambda r: r.phase_deg),
            "rocof": mean(lambda r: r.rocof),
            "tve": mean(lambda r: r.tve_percent),
            "synced": all(r.synced for r in got),
            "n": n,
        }


class SerialPmu(_PmuBase):
    """Real micro-PMU on a USB-CDC serial port (or auto-detected by VID:PID)."""

    def __init__(self, port: str | None = None, baud: int = 115200,
                 volts_per_count: float | None = None, report_rate: float = 10.0):
        super().__init__(volts_per_count, report_rate)
        self.port = port
        self.baud = baud

    def open(self) -> "SerialPmu":
        sources = self._u["sources"]
        port = self.port
        if port in (None, "auto"):
            port = sources.find_upmu_port(timeout=15.0)
            if not port:
                raise RuntimeError(
                    "no micro-PMU found (USB 0483:5740); is CN13 connected "
                    "and the board flashed?")
        source = sources.open_serial(port, self.baud)
        self._start_pipeline(source)
        return self

    def arm(self, freq_hz: float, vrms: float, phase_deg: float = 0.0) -> None:
        # The physical signal is already applied by the 3325B; just update the
        # TVE reference so mag/phase errors are measured against this setpoint.
        self._set_reference(freq_hz, vrms, phase_deg)


class SimPmu(_PmuBase):
    """In-process ``upmu`` Simulator matched to the shared virtual bench."""

    def __init__(self, bench: VirtualBench | None = None,
                 volts_per_count: float | None = None, report_rate: float = 10.0):
        super().__init__(volts_per_count, report_rate)
        self.bench = bench or DEFAULT_BENCH
        # The board's TRUE front-end scale is a fixed physical constant, anchored
        # to the uncalibrated baseline (NOT the engine's current estimate, or the
        # error would chase the calibration and never close). Calibration moves
        # the engine's volts_per_count toward this value.
        baseline_vpc = self._u["config"].DEFAULT_VOLTS_PER_COUNT
        self._true_vpc = baseline_vpc * self.bench.pmu_front_end_error

    def open(self) -> "SimPmu":
        return self          # pipeline is (re)built per point in arm()

    def arm(self, freq_hz: float, vrms: float, phase_deg: float = 0.0) -> None:
        self._set_reference(freq_hz, vrms, phase_deg)
        # Generate the stream at the fixed true front-end scale; the engine
        # reconstructs magnitude with its own (possibly miscalibrated) vpc, so
        # PMU vmag = commanded * engine_vpc / true_vpc until calibration matches.
        sim_vpc = self._true_vpc
        source = self._u["Simulator"](
            freq=freq_hz, vrms=vrms, phase_deg=phase_deg,
            volts_per_count=sim_vpc, realtime=True,
        )
        self._start_pipeline(source)


def make_pmu(simulate: bool, *, port: str | None = None, baud: int = 115200,
             volts_per_count: float | None = None, report_rate: float = 10.0,
             bench: VirtualBench | None = None):
    if simulate:
        return SimPmu(bench, volts_per_count=volts_per_count, report_rate=report_rate)
    return SerialPmu(port, baud=baud, volts_per_count=volts_per_count,
                     report_rate=report_rate)
