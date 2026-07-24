"""The test sequencer: drive the source, settle, read every instrument.

For each :class:`TestPoint` the sequencer:

1. commands the HP 3325B to the point's frequency / amplitude / phase,
2. arms the PMU (re-points the DUT at the new signal; no-op on real hardware),
3. waits ``settle_s`` for everything to stabilise and the PMU to lock,
4. reads the DMM (avg N), the scope (avg N) and the PMU (avg N reports),
5. returns the raw readings as a :class:`PointResult`.

Error math lives in :mod:`pmu_validation.results` so the sequencer stays a thin,
instrument-agnostic loop.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class TestPoint:
    label: str
    freq_hz: float
    vrms: float
    phase_deg: float = 0.0
    settle_s: float = 2.5
    dmm_navg: int = 4
    scope_navg: int = 2
    pmu_navg: int = 8
    pmu_timeout_s: float = 10.0


@dataclass
class PointResult:
    point: TestPoint
    dmm_vrms: float | None
    scope: dict = field(default_factory=dict)
    pmu: dict = field(default_factory=dict)
    dmm_freq: float | None = None      # 34401A FREQ reading (manual/regime A)
    note: str = ""


ProgressFn = Callable[[int, int, "PointResult"], None]


def run_sweep(source, dmm, scope, pmu, points: list[TestPoint],
              on_progress: ProgressFn | None = None,
              read_scope: bool = True, stop_event=None) -> list[PointResult]:
    """Execute every point in order; return one :class:`PointResult` each.

    Instruments are assumed already opened by the caller. Any per-instrument
    read error is captured in the row's ``note`` rather than aborting the run,
    so one flaky point does not throw away the whole sweep.

    ``source`` may be ``None`` for a source-less (Variac / regime-A) bench: with
    no programmable stimulus to command, each point instead becomes a
    DMM-referenced capture at the *current* line condition (the same read the
    manual Variac flow does), so DMM/scope/PMU are still compared apples-to-
    apples. Settling still applies between points.

    ``stop_event`` (a :class:`threading.Event`) lets a GUI cancel the sweep
    between points; already-collected results are returned.
    """
    results: list[PointResult] = []
    total = len(points)
    for i, pt in enumerate(points, 1):
        if stop_event is not None and stop_event.is_set():
            break

        if source is None:
            # No stimulus to command: settle, then capture with the DMM as the
            # reference (freq from its FREQ function). Reuses the manual path.
            time.sleep(pt.settle_s)
            res = capture_manual(dmm, scope, pmu, label=pt.label,
                                 dmm_navg=pt.dmm_navg, scope_navg=pt.scope_navg,
                                 pmu_navg=pt.pmu_navg, read_scope=read_scope,
                                 pmu_timeout_s=pt.pmu_timeout_s)
            results.append(res)
            if on_progress:
                on_progress(i, total, res)
            continue

        note = ""
        source.set_signal(pt.freq_hz, pt.vrms, pt.phase_deg)
        pmu.arm(pt.freq_hz, pt.vrms, pt.phase_deg)
        time.sleep(pt.settle_s)

        try:
            dmm_vrms = dmm.read_vrms(navg=pt.dmm_navg)
        except Exception as exc:                       # noqa: BLE001
            dmm_vrms, note = None, f"dmm:{exc}; "

        scope_r: dict = {}
        if read_scope:
            try:
                scope_r = scope.read(navg=pt.scope_navg)
            except Exception as exc:                   # noqa: BLE001
                note += f"scope:{exc}; "

        try:
            pmu_r = pmu.read(navg=pt.pmu_navg, timeout_s=pt.pmu_timeout_s)
        except Exception as exc:                       # noqa: BLE001
            pmu_r, note = {"n": 0, "synced": False}, note + f"pmu:{exc}; "
        if pmu_r.get("n", 0) == 0:
            note += "pmu:no locked report in timeout; "

        res = PointResult(point=pt, dmm_vrms=dmm_vrms, scope=scope_r,
                          pmu=pmu_r, note=note.strip())
        results.append(res)
        if on_progress:
            on_progress(i, total, res)
    return results


def capture_manual(dmm, scope, pmu, label: str, *, dmm_navg: int = 4,
                   scope_navg: int = 2, pmu_navg: int = 8, read_scope: bool = True,
                   read_freq: bool = True, pmu_timeout_s: float = 8.0,
                   require_sync: bool = True) -> PointResult:
    """One manual capture for the Variac (regime A) flow: read the DMM (the
    amplitude reference and, via its FREQ function, the frequency reference), the
    optional scope, and the PMU. There is no commanded setpoint — the operator
    sets the Variac by hand — so the DMM reading *is* the reference.

    ``require_sync=False`` accepts unsynced PMU reports: amplitude-only captures
    (calibration) don't need the GPS/UTC anchor, only phase/TVE do.
    """
    note = ""
    try:
        dmm_vrms = dmm.read_vrms(navg=dmm_navg)
    except Exception as exc:                               # noqa: BLE001
        dmm_vrms, note = None, f"dmm:{exc}; "

    dmm_freq = None
    if read_freq:
        try:
            dmm_freq = dmm.read_freq(navg=max(1, dmm_navg // 2))
        except Exception as exc:                           # noqa: BLE001
            note += f"dmmfreq:{exc}; "

    scope_r: dict = {}
    if read_scope:
        try:
            scope_r = scope.read(navg=scope_navg)
        except Exception as exc:                           # noqa: BLE001
            note += f"scope:{exc}; "

    try:
        pmu_r = pmu.read(navg=pmu_navg, timeout_s=pmu_timeout_s,
                         require_sync=require_sync)
    except Exception as exc:                               # noqa: BLE001
        pmu_r, note = {"n": 0, "synced": False}, note + f"pmu:{exc}; "
    if pmu_r.get("n", 0) == 0:
        note += "pmu:no locked report; "

    # Synthesize a point whose 'commanded' fields hold the measured reference, so
    # the existing row/plot code (x = cmd_vrms) works with DMM as the reference.
    pt = TestPoint(label=label, freq_hz=(dmm_freq or 60.0), vrms=(dmm_vrms or 0.0))
    return PointResult(point=pt, dmm_vrms=dmm_vrms, scope=scope_r, pmu=pmu_r,
                       dmm_freq=dmm_freq, note=note.strip())
