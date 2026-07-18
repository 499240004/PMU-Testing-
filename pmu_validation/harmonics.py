"""Harmonic analysis shared by the scope and PMU paths.

Two pieces:

* :func:`theoretical_fractions` — the *known* harmonic content of the 3325B
  waveform shapes. A single-tone synthesizer can't make an arbitrary harmonic
  mix, but its non-sine shapes are exact Fourier series, so they double as a
  reference:

    - square  → odd harmonics at 1/n   (3rd 33.3%, 5th 20%, …), THD ~48.3%
    - triangle→ odd harmonics at 1/n²  (3rd 11.1%, 5th 4%, …),  THD ~12.2%
    - ramp    → all harmonics at 1/n   (2nd 50%, 3rd 33.3%, …), THD ~80.3%

* :func:`analyze` — pull per-harmonic amplitudes and THD from a captured
  waveform, using the SAME FFT the PMU host uses (``upmu.burstfft.spectrum``) so
  the scope-side and PMU-side numbers are computed identically and compare fair.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ._vendor import import_upmu

# 3325B main-output shapes and whether each has harmonics we can rely on.
SHAPES = ["Sine", "Square", "Triangle", "Ramp"]


def theoretical_fractions(shape: str, n_max: int = 13) -> dict[int, float]:
    """Amplitude ratio (harmonic / fundamental) for an ideal 3325B waveform."""
    shape = shape.capitalize()
    out: dict[int, float] = {}
    if shape == "Square":
        out = {n: 1.0 / n for n in range(3, n_max + 1, 2)}          # odd, 1/n
    elif shape == "Triangle":
        out = {n: 1.0 / (n * n) for n in range(3, n_max + 1, 2)}    # odd, 1/n^2
    elif shape in ("Ramp", "Sawtooth", "Positive ramp", "Negative ramp"):
        out = {n: 1.0 / n for n in range(2, n_max + 1)}             # all, 1/n
    # Sine -> {} (no harmonics)
    return out


def theoretical_thd_pct(fractions: dict[int, float]) -> float:
    """THD (% of fundamental) from a set of harmonic amplitude fractions."""
    if not fractions:
        return 0.0
    return 100.0 * (sum(f * f for f in fractions.values())) ** 0.5


@dataclass
class HarmonicResult:
    f0: float
    fundamental_vrms: float
    orders: list[int] = field(default_factory=list)
    vrms: dict[int, float] = field(default_factory=dict)         # order -> Vrms
    fraction_pct: dict[int, float] = field(default_factory=dict)  # order -> % of fund
    thd_pct: float = float("nan")


def analyze(volts, fs: float, f0: float, n_harmonics: int = 13
            ) -> HarmonicResult | None:
    """Per-harmonic Vrms + THD from a real waveform sampled at ``fs``.

    Uses ``upmu.burstfft.spectrum`` (Hann, coherent-gain corrected, single-sided
    Vrms). Each harmonic amplitude is the max spectral bin within ±0.4·f0 of the
    target frequency, which tolerates a slightly off-nominal fundamental and
    window leakage.
    """
    volts = np.asarray(volts, dtype=np.float64)
    if volts.size < 16 or fs <= 0 or f0 <= 0:
        return None
    spectrum = import_upmu()["burstfft"].spectrum
    freqs, mag = spectrum(volts, fs)
    if freqs.size < 4:
        return None
    fmax = float(freqs[-1])
    half = 0.4 * f0

    def amp_at(f: float) -> float:
        if f >= fmax:
            return 0.0
        sel = (freqs >= f - half) & (freqs <= f + half)
        return float(mag[sel].max()) if np.any(sel) else 0.0

    v1 = amp_at(f0)
    orders = [k for k in range(2, n_harmonics + 1) if k * f0 < fmax]
    vk = {k: amp_at(k * f0) for k in orders}
    if v1 <= 0:
        return HarmonicResult(f0=f0, fundamental_vrms=0.0, orders=orders, vrms=vk)
    frac = {k: v / v1 * 100.0 for k, v in vk.items()}
    thd = 100.0 * (sum(v * v for v in vk.values())) ** 0.5 / v1
    return HarmonicResult(f0=f0, fundamental_vrms=v1, orders=orders,
                          vrms=vk, fraction_pct=frac, thd_pct=thd)
