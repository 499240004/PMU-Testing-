"""Signal source adapter: the HP 3325B function generator (stimulus).

The source *commands* the bench signal (frequency, amplitude, phase). Both the
real and simulated variants expose the same tiny interface used by the
sequencer:

    open() / close()               -- lifecycle (also a context manager)
    identify() -> str
    set_signal(freq_hz, vrms, phase_deg=0.0, function="Sine")

Amplitude is commanded in **Vrms** throughout the validation stack (the 3325B
supports a Vrms amplitude unit directly), so every instrument in the run speaks
the same units and the PMU's RMS magnitude compares apples-to-apples.
"""
from __future__ import annotations

from .._vendor import import_hp3325
from ..virtualbench import DEFAULT_BENCH, VirtualBench


class Hp3325Source:
    """Real HP 3325B over RS-232 (see the HP-3325 app README for wiring)."""

    def __init__(self, port: str, baud: int = 4800, bytesize: int = 7,
                 parity: str = "E", settle_s: float = 0.3):
        self.port = port
        self.baud = baud
        self.bytesize = bytesize
        self.parity = parity
        self.settle_s = settle_s
        self._dev = None

    def open(self) -> "Hp3325Source":
        hp3325b_driver = import_hp3325()
        self._dev = hp3325b_driver.HP3325B()
        self._dev.connect(self.port, baudrate=self.baud,
                          bytesize=self.bytesize, parity=self.parity)
        self._dev.init_session()          # echo off, numeric responses
        self._dev.remote_lockout()
        return self

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.local()
            finally:
                self._dev.disconnect()
            self._dev = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def identify(self) -> str:
        return self._dev.identify()

    def set_signal(self, freq_hz: float, vrms: float, phase_deg: float = 0.0,
                   function: str = "Sine") -> None:
        self._dev.set_function(function)
        self._dev.set_frequency(freq_hz, "Hz")
        self._dev.set_amplitude(vrms, "Vrms")
        self._dev.set_phase(phase_deg)


class SimSource:
    """Simulated source: writes the commanded signal into the shared bench."""

    def __init__(self, bench: VirtualBench | None = None):
        self.bench = bench or DEFAULT_BENCH

    def open(self) -> "SimSource":
        return self

    def close(self) -> None:
        pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def identify(self) -> str:
        return "HEWLETT-PACKARD,3325B,SIMULATED,0"

    def set_signal(self, freq_hz: float, vrms: float, phase_deg: float = 0.0,
                   function: str = "Sine") -> None:
        self.bench.set_signal(freq_hz, vrms, phase_deg)


def make_source(simulate: bool, *, port: str | None = None, baud: int = 4800,
                bench: VirtualBench | None = None):
    """Factory: a :class:`SimSource` or a real :class:`Hp3325Source`."""
    if simulate:
        return SimSource(bench)
    if not port:
        raise ValueError("a real HP 3325B needs --source-port COMx")
    return Hp3325Source(port, baud=baud)
