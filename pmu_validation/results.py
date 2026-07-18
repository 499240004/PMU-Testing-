"""Turn raw sweep readings into rows, a CSV, a printed summary and plots.

The comparison logic is deliberately explicit: the DMM is the primary amplitude
reference, the scope is the primary frequency reference (and a secondary
amplitude reference), and the DMM-vs-scope agreement is reported too so you can
see when the *references* disagree rather than blaming the PMU.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .sequencer import PointResult, TestPoint


def _pct(meas, ref):
    if meas is None or ref in (None, 0):
        return None
    return (meas - ref) / ref * 100.0


def _mhz(meas, ref):
    if meas is None or ref is None:
        return None
    return (meas - ref) * 1000.0


def build_row(res: PointResult) -> dict:
    """Flatten one PointResult into a row with derived error columns."""
    pt = res.point
    dmm = res.dmm_vrms
    s = res.scope or {}
    p = res.pmu or {}
    scope_vrms = s.get("vrms")
    scope_freq = s.get("freq")
    pmu_vmag = p.get("vmag")
    pmu_freq = p.get("freq")

    return {
        "label": pt.label,
        "cmd_freq_hz": pt.freq_hz,
        "cmd_vrms": pt.vrms,
        "dmm_vrms": dmm,
        "scope_vrms": scope_vrms,
        "scope_freq_hz": scope_freq,
        "pmu_vmag_vrms": pmu_vmag,
        "pmu_freq_hz": pmu_freq,
        "pmu_phase_deg": p.get("phase"),
        "pmu_rocof_hz_s": p.get("rocof"),
        "pmu_tve_pct": p.get("tve"),
        "pmu_n": p.get("n", 0),
        "synced": p.get("synced", False),
        # --- derived comparisons ---
        "vmag_err_vs_dmm_pct": _pct(pmu_vmag, dmm),
        "vmag_err_vs_scope_pct": _pct(pmu_vmag, scope_vrms),
        "dmm_vs_scope_pct": _pct(scope_vrms, dmm),   # reference agreement
        "freq_err_vs_cmd_mhz": _mhz(pmu_freq, pt.freq_hz),
        "freq_err_vs_scope_mhz": _mhz(pmu_freq, scope_freq),
        # per-point front-end correction factor (dmm / pmu)
        "vpc_correction": (dmm / pmu_vmag) if (dmm and pmu_vmag) else None,
        "note": res.note,
    }


# Column order is defined by the shape of build_row() on a representative row.
COLUMNS = list(build_row(
    PointResult(point=TestPoint("x", 60.0, 1.0), dmm_vrms=None)).keys())


def write_csv(rows: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def _stats(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    n = len(vals)
    mean = sum(vals) / n
    if n > 1:
        var = sum((v - mean) ** 2 for v in vals) / (n - 1)
        std = var ** 0.5
    else:
        std = 0.0
    return {"n": n, "mean": mean, "std": std,
            "min": min(vals), "max": max(vals),
            "absmax": max(abs(v) for v in vals)}


@dataclass
class Summary:
    kind: str
    text: str
    recommended_vpc: float | None = None


def summarize(kind: str, rows: list[dict], current_vpc: float) -> Summary:
    lines = []
    dmm_scope = _stats([r["dmm_vs_scope_pct"] for r in rows])
    if dmm_scope:
        lines.append(f"reference agreement (DMM vs scope): "
                     f"{dmm_scope['mean']:+.3f}% mean, {dmm_scope['absmax']:.3f}% worst")

    recommended = None
    if kind == "amplitude":
        corr = _stats([r["vpc_correction"] for r in rows])
        vmag_err = _stats([r["vmag_err_vs_dmm_pct"] for r in rows])
        if corr:
            recommended = current_vpc * corr["mean"]
            lines.append(
                f"volts_per_count: current {current_vpc:.8g} -> recommended "
                f"{recommended:.8g}  (x{corr['mean']:.5f}, spread "
                f"{corr['std']/corr['mean']*100:.3f}% over {corr['n']} pts)")
        if vmag_err:
            lines.append(f"PMU magnitude error vs DMM (uncalibrated): "
                         f"{vmag_err['absmax']:.3f}% worst, {vmag_err['mean']:+.3f}% mean")
        # At a fixed nominal frequency, TVE is a valid conformance metric.
        tve = _stats([r["pmu_tve_pct"] for r in rows])
        if tve:
            lines.append(f"PMU TVE: {tve['absmax']:.3f}% worst, {tve['mean']:.3f}% mean "
                         f"(C37.118 M-class limit 1.0%)")
    else:  # frequency
        ferr_cmd = _stats([r["freq_err_vs_cmd_mhz"] for r in rows])
        ferr_scope = _stats([r["freq_err_vs_scope_mhz"] for r in rows])
        vmag_err = _stats([r["vmag_err_vs_dmm_pct"] for r in rows])
        if ferr_cmd:
            lines.append(f"PMU frequency error vs commanded: "
                         f"{ferr_cmd['absmax']:.2f} mHz worst, {ferr_cmd['mean']:+.2f} mHz mean")
        if ferr_scope:
            lines.append(f"PMU frequency error vs scope: "
                         f"{ferr_scope['absmax']:.2f} mHz worst")
        if vmag_err:
            lines.append(f"PMU magnitude error vs DMM: "
                         f"{vmag_err['absmax']:.3f}% worst, {vmag_err['mean']:+.3f}% mean")
        # TVE here is computed against a STATIC top-of-second reference phasor,
        # which is only meaningful at the nominal frequency (the host's
        # documented off-nominal rotation caveat). Report it near nominal only.
        nominal = [r for r in rows if abs(r["cmd_freq_hz"] - 60.0) < 0.25]
        tve_nom = _stats([r["pmu_tve_pct"] for r in nominal])
        if tve_nom:
            lines.append(f"PMU TVE at nominal (60 Hz): {tve_nom['absmax']:.3f}% "
                         f"(C37.118 M-class limit 1.0%)")
        off = [r for r in rows if abs(r["cmd_freq_hz"] - 60.0) >= 0.25]
        if off:
            lines.append("note: off-nominal TVE uses a static reference phasor and "
                         "is NOT a conformance metric here (see architecture caveat); "
                         "judge off-nominal points by frequency & magnitude error.")

    unlocked = [r["label"] for r in rows if not r["synced"] or r["pmu_n"] == 0]
    if unlocked:
        lines.append(f"WARNING: {len(unlocked)} point(s) never locked: "
                     f"{', '.join(unlocked)}")

    return Summary(kind=kind, text="\n".join("  " + ln for ln in lines),
                   recommended_vpc=recommended)


def plot(kind: str, rows: list[dict], path: str | Path) -> Path | None:
    """Error-vs-sweep-variable PNG. Returns None if matplotlib is unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "amplitude":
        x = [r["cmd_vrms"] for r in rows]
        xlabel = "commanded Vrms"
        series = [("PMU vs DMM mag err %", "vmag_err_vs_dmm_pct"),
                  ("DMM vs scope %", "dmm_vs_scope_pct")]
        y2 = ("TVE %", "pmu_tve_pct")
    else:
        x = [r["cmd_freq_hz"] for r in rows]
        xlabel = "commanded frequency (Hz)"
        series = [("PMU freq err vs cmd (mHz)", "freq_err_vs_cmd_mhz"),
                  ("PMU freq err vs scope (mHz)", "freq_err_vs_scope_mhz")]
        y2 = ("TVE %", "pmu_tve_pct")

    fig, ax = plt.subplots(figsize=(8, 5))
    for label, key in series:
        xs = [xi for xi, r in zip(x, rows) if r[key] is not None]
        ys = [r[key] for r in rows if r[key] is not None]
        if xs:
            ax.plot(xs, ys, "o-", label=label)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("error")
    ax.axhline(0, color="k", lw=0.5)
    ax.grid(True, alpha=0.3)

    ax2 = ax.twinx()
    xs = [xi for xi, r in zip(x, rows) if r[y2[1]] is not None]
    ys = [r[y2[1]] for r in rows if r[y2[1]] is not None]
    if xs:
        ax2.plot(xs, ys, "s--", color="tab:red", alpha=0.6, label=y2[0])
        ax2.axhline(1.0, color="tab:red", lw=0.5, ls=":")   # M-class TVE limit
    ax2.set_ylabel(y2[0])

    lines1, lab1 = ax.get_legend_handles_labels()
    lines2, lab2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, lab1 + lab2, loc="best", fontsize=8)
    ax.set_title(f"micro-PMU validation: {kind} sweep")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
