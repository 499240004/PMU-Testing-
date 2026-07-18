"""DMM adapter: the HP 34401A bench meter -- the accurate amplitude reference.

The DMM is the slow, precise anchor for RMS voltage (it calibrates the PMU's
``volts_per_count``). We configure it for AC volts and average N triggered
readings per test point.

Interface used by the sequencer:

    open() / close()
    identify() -> str
    read_vrms(navg=1) -> float
"""
from __future__ import annotations

from .._vendor import import_hp34401, import_hp34401_sim
from ..virtualbench import DEFAULT_BENCH, VirtualBench


class Hp34401Dmm:
    """Real HP 34401A over RS-232 configured for AC volts."""

    def __init__(self, port: str, baud: int = 9600, parity: str = "N",
                 nplc: float = 10.0, meas_range="AUTO"):
        self.port = port
        self.baud = baud
        self.parity = parity
        self.nplc = nplc
        self.meas_range = meas_range
        self._dev = None

    def open(self) -> "Hp34401Dmm":
        HP34401A = import_hp34401()
        self._dev = HP34401A(self.port, baud=self.baud, parity=self.parity).open()
        # AC volts. NPLC only bites on DC integrating functions, but pass it so
        # a DC cross-check reuse of this class behaves; autozero left default.
        self._dev.configure("VAC", meas_range=self.meas_range)
        return self

    def close(self) -> None:
        if self._dev is not None:
            self._dev.close()
            self._dev = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def identify(self) -> str:
        return self._dev.idn()

    def read_vrms(self, navg: int = 1) -> float:
        vals = [self._dev.read_value() for _ in range(max(1, navg))]
        return sum(vals) / len(vals)


class SimDmm:
    """Simulated DMM: reads AC Vrms from the shared bench."""

    def __init__(self, bench: VirtualBench | None = None):
        self.bench = bench or DEFAULT_BENCH
        self._dev = None

    def open(self) -> "SimDmm":
        # Instantiate the app's own simulator too, so the code path that would
        # exercise it stays covered even though we read from the bench.
        SimulatedHP34401A = import_hp34401_sim()
        self._dev = SimulatedHP34401A().open()
        self._dev.configure("VAC")
        return self

    def close(self) -> None:
        if self._dev is not None:
            self._dev.close()
            self._dev = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def identify(self) -> str:
        return "HEWLETT-PACKARD,34401A,SIMULATED,0"

    def read_vrms(self, navg: int = 1) -> float:
        vals = [self.bench.dmm_vrms() for _ in range(max(1, navg))]
        return sum(vals) / len(vals)


def make_dmm(simulate: bool, *, port: str | None = None, baud: int = 9600,
             parity: str = "N", nplc: float = 10.0,
             bench: VirtualBench | None = None):
    if simulate:
        return SimDmm(bench)
    if not port:
        raise ValueError("a real HP 34401A needs --dmm-port COMx")
    return Hp34401Dmm(port, baud=baud, parity=parity, nplc=nplc)
