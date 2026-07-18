"""Built-in test plans for the v1 magnitude + frequency suite.

Two plans, both mapping to IEEE C37.118 steady-state characterisation:

* **amplitude** -- fixed 60 Hz, amplitude stepped across the working range. Used
  to fit the PMU front-end scale (``volts_per_count``) against the DMM and to
  report magnitude error / TVE vs level.
* **frequency** -- fixed amplitude, frequency stepped across the off-nominal
  band (M-class steady-state range). Used to report frequency error and TVE vs
  frequency, cross-checked against the scope.

Amplitudes are in **Vrms at the injection node**. Keep them inside your HP 3325B
output range and your PMU front-end input range -- override on the CLI with
``--vrms`` / ``--freqs`` / ``--vrms-steps`` for your actual bench levels.
"""
from __future__ import annotations

from .sequencer import TestPoint


def amplitude_plan(freq_hz: float = 60.0,
                   levels=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0),
                   settle_s: float = 2.5) -> list[TestPoint]:
    return [
        TestPoint(label=f"A_{v:g}Vrms", freq_hz=freq_hz, vrms=float(v),
                  settle_s=settle_s)
        for v in levels
    ]


def frequency_plan(vrms: float = 5.0,
                   freqs=(57.0, 58.0, 59.0, 59.5, 60.0, 60.5, 61.0, 62.0, 63.0),
                   settle_s: float = 3.0) -> list[TestPoint]:
    return [
        TestPoint(label=f"F_{f:g}Hz", freq_hz=float(f), vrms=vrms,
                  settle_s=settle_s)
        for f in freqs
    ]


PLANS = {
    "amplitude": amplitude_plan,
    "frequency": frequency_plan,
}
