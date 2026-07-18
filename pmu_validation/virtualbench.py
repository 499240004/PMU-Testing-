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

import random
from dataclasses import dataclass, field


@dataclass
class VirtualBench:
    # Current commanded signal at the injection node (set by the source adapter).
    freq_hz: float = 60.0
    vrms: float = 120.0
    phase_deg: float = 0.0

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

    # -- reads for the reference instruments --------------------------------- #
    def dmm_vrms(self) -> float:
        v = self.vrms * (1.0 + self.dmm_gain_err)
        return v * (1.0 + self._rng.gauss(0.0, self.dmm_noise_frac))

    def scope_vrms(self) -> float:
        v = self.vrms * (1.0 + self.scope_gain_err)
        return v * (1.0 + self._rng.gauss(0.0, self.scope_noise_frac))

    def scope_freq(self) -> float:
        return self.freq_hz + self._rng.gauss(0.0, self.scope_freq_noise_hz)

    def scope_phase_deg(self) -> float:
        return self.phase_deg + self._rng.gauss(0.0, 0.05)


# Process-wide default bench so independently-constructed adapters share it in
# simulate mode. Callers may also pass their own instance explicitly.
DEFAULT_BENCH = VirtualBench()
