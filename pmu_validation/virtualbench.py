"""A shared 'virtual signal node' for simulate mode.

On the real bench, the HP 3325B drives one physical node and the DMM, scope and
PMU all measure *that same node*. The value of the cross-check comes from them
observing one signal independently.

Each app ships its own simulator, but those are independent and would never
agree with one another -- so a validation run against them would be meaningless.
Instead, in ``--simulate`` mode all four instrument adapters share this
:class:`VirtualBench`: the source adapter *writes* the commanded signal here and
the DMM / scope / PMU adapters *read* it back, each adding a small, realistic,
instrument-specific error so the cross-check exercises the real comparison math.

The bench also models one deliberate imperfection worth catching: a front-end
scale error on the PMU (``pmu_front_end_error``). The PMU reports magnitude via
its ``volts_per_count``; if the true front-end gain differs, the amplitude
reads high or low until calibrated. Seeding a ~5% error here gives the
amplitude-calibration test something real to recover, so simulate mode actually
verifies the calibration logic rather than trivially returning 1.0.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np


@dataclass
class VirtualBench:
    # Current commanded signal at the injection node (set by the source adapter).
    freq_hz: float = 60.0
    vrms: float = 120.0          # fundamental Vrms
    phase_deg: float = 0.0
    shape: str = "Sine"
    # Harmonic content: order -> amplitude fraction of the fundamental. Set by
    # the source adapter for a harmonic-rich stimulus (shape or custom mix).
    harmonics: dict = field(default_factory=dict)

    # Independent per-instrument systematic errors + noise (fractional / absolute).
    dmm_gain_err: float = 0.0005      # +0.05% reference DMM scale error
    dmm_noise_frac: float = 0.0002    # 0.02% rms reading noise
    scope_gain_err: float = -0.0008   # -0.08% scope vertical error
    scope_noise_frac: float = 0.001   # 0.1% (scope is noisier than the DMM)
    scope_freq_noise_hz: float = 0.002
    # True PMU front-end scale relative to its configured volts_per_count.
    # 1.05 => PMU reads 5% low until calibration corrects volts_per_count up 5%.
    pmu_front_end_error: float = 1.05

    _rng: random.Random = field(default_factory=lambda: random.Random(0xC37118))

    def set_signal(self, freq_hz: float, vrms: float, phase_deg: float = 0.0) -> None:
        self.freq_hz = float(freq_hz)
        self.vrms = float(vrms)
        self.phase_deg = float(phase_deg)

    def _total_rms(self, vrms: float) -> float:
        """Total RMS including harmonic content (fundamental Vrms -> total)."""
        extra = sum(f * f for f in self.harmonics.values())
        return vrms * math.sqrt(1.0 + extra)

    def waveform(self, fs: float, seconds: float, gain_err: float | None = None):
        """Synthesize (t, volts) of the fundamental + harmonics for the scope
        sim. Amplitudes are fractions of the fundamental peak; a small amount of
        noise is added so the spectrum has a realistic floor."""
        g = self.scope_gain_err if gain_err is None else gain_err
        apk = self.vrms * math.sqrt(2.0) * (1.0 + g)
        n = max(16, int(fs * seconds))
        t = np.arange(n) / fs
        w0 = 2.0 * math.pi * self.freq_hz
        v = apk * np.cos(w0 * t + math.radians(self.phase_deg))
        for order, frac in self.harmonics.items():
            v = v + apk * frac * np.cos(order * w0 * t)
        rng = np.random.default_rng(2718)
        v = v + rng.normal(0.0, apk * self.scope_noise_frac * 0.3, size=n)
        return t, v

    # -- reads for the reference instruments --------------------------------- #
    def dmm_vrms(self) -> float:
        v = self._total_rms(self.vrms) * (1.0 + self.dmm_gain_err)
        return v * (1.0 + self._rng.gauss(0.0, self.dmm_noise_frac))

    def scope_vrms(self) -> float:
        v = self._total_rms(self.vrms) * (1.0 + self.scope_gain_err)
        return v * (1.0 + self._rng.gauss(0.0, self.scope_noise_frac))

    def scope_freq(self) -> float:
        return self.freq_hz + self._rng.gauss(0.0, self.scope_freq_noise_hz)

    def dmm_freq(self) -> float:
        # 34401A FREQ reading of the same line (slightly noisier than the scope).
        return self.freq_hz + self._rng.gauss(0.0, 0.003)

    def scope_phase_deg(self) -> float:
        return self.phase_deg + self._rng.gauss(0.0, 0.05)


# Process-wide default bench so independently-constructed adapters share it in
# simulate mode. Callers may also pass their own instance explicitly.
DEFAULT_BENCH = VirtualBench()
