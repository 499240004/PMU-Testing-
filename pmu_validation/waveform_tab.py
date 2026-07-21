"""Waveform tab: overlay the PMU's reconstructed waveform against the scope.

Captures one time-domain record from each -- the scope on the chosen channel
(CH1 is the 200:1 diff probe on L-N, grid volts) and the PMU's continuous ADC
stream (counts * volts_per_count, also grid volts) -- resamples both onto a
common grid, aligns them by cross-correlation, overlays them and reports the
residual (RMS + peak error, and each signal's Vrms). Both are grid-referred, so
a good calibration makes them sit on top of each other.

Worker thread + queue, like the other tabs.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


class WaveformFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last = None            # (tg, vs, vp, metrics)
        self._build_vars()
        self._build()
        self.after(120, self._drain)

    def _build_vars(self):
        self.channel = tk.StringVar(value="1")
        self.f0 = tk.StringVar(value="60")
        self.cycles = tk.StringVar(value="4")
        self.pmu_secs = tk.StringVar(value="0.2")
        self.status = tk.StringVar(value="Capture to overlay PMU vs scope.")

    # ---------------------------------------------------------------- layout
    def _build(self):
        ctl = ttk.LabelFrame(self, text="PMU vs scope waveform")
        ctl.pack(fill="x", padx=8, pady=6)

        def lab(t, c):
            ttk.Label(ctl, text=t).grid(row=0, column=c, sticky="e", padx=(8, 2), pady=4)

        lab("Scope ch", 0)
        ttk.Combobox(ctl, textvariable=self.channel, width=4, state="readonly",
                     values=["1", "2", "3", "4"]).grid(row=0, column=1, sticky="w")
        lab("f0 (Hz)", 2)
        ttk.Entry(ctl, textvariable=self.f0, width=6).grid(row=0, column=3, sticky="w")
        lab("cycles", 4)
        ttk.Entry(ctl, textvariable=self.cycles, width=5).grid(row=0, column=5, sticky="w")
        lab("PMU window (s)", 6)
        ttk.Entry(ctl, textvariable=self.pmu_secs, width=6).grid(row=0, column=7, sticky="w")
        self.run_btn = ttk.Button(ctl, text="▶ Capture", command=self._on_run)
        self.run_btn.grid(row=0, column=8, padx=8)
        ttk.Button(ctl, text="Save PNG", command=self._save).grid(row=0, column=9, padx=2)
        ttk.Label(ctl, text="CH1 = diff probe on L-N (matches DMM/PMU); CH4 = PMU ADC input",
                  foreground="#777", font=("Segoe UI", 8)).grid(
            row=1, column=0, columnspan=10, sticky="w", padx=8, pady=(0, 4))

        self.metrics = ttk.Label(self, text="", font=("Consolas", 10, "bold"))
        self.metrics.pack(anchor="w", padx=10, pady=(2, 0))

        pf = ttk.Frame(self)
        pf.pack(fill="both", expand=True, padx=8, pady=(2, 4))
        self.fig = Figure(figsize=(7, 5), dpi=100)
        self.ax = self.fig.add_subplot(211)
        self.ax_r = self.fig.add_subplot(212, sharex=self.ax)
        self._reset_plot()
        self.canvas = FigureCanvasTkAgg(self.fig, master=pf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        ttk.Label(self, textvariable=self.status, foreground="#555").pack(
            anchor="w", padx=10, pady=(0, 6))

    # ------------------------------------------------------------------- run
    def _on_run(self):
        if self._worker and self._worker.is_alive():
            return
        try:
            s = {
                "simulate": self.app.simulate.get(),
                "scope_ip": self.app.scope_ip.get(),
                "scope_ch": int(self.channel.get()),
                "scope_probe": float(self.app.scope_probe.get() or 1),
                "pmu_port": self.app.pmu_port.get(),
                "vpc": float(self.app.vpc.get()) if self.app.vpc.get().strip() else None,
                "f0": float(self.f0.get()),
                "cycles": int(self.cycles.get()),
                "pmu_secs": float(self.pmu_secs.get()),
            }
        except ValueError as exc:
            messagebox.showerror("Bad input", str(exc)); return
        self.run_btn.configure(state="disabled")
        self.status.set("capturing…")
        self._worker = threading.Thread(target=self._work, args=(s,), daemon=True)
        self._worker.start()

    def _work(self, s: dict):
        from .instruments import make_scope, make_pmu
        from .virtualbench import VirtualBench
        import time
        bench = VirtualBench() if s["simulate"] else None
        opened = []
        try:
            scope = make_scope(s["simulate"], ip=s["scope_ip"], channel=s["scope_ch"],
                               probe_atten=s["scope_probe"], bench=bench)
            pmu = make_pmu(s["simulate"], port=s["pmu_port"], volts_per_count=s["vpc"],
                           bench=bench)
            for inst in (scope, pmu):
                inst.open(); opened.append(inst)
            if s["simulate"]:
                bench.set_signal(s["f0"], 120.0, 0.0)
                pmu.arm(s["f0"], 120.0, 0.0)
                time.sleep(2.0)
            else:
                time.sleep(0.5)     # let the PMU ring buffer fill

            ts, vs = scope.capture_waveform(channel=s["scope_ch"], cycles=s["cycles"],
                                            f0=s["f0"])
            prec = pmu.capture_waveform(seconds=s["pmu_secs"])
            if prec is None:
                raise RuntimeError("PMU produced no samples (no lock / stream empty)")
            tp, vp = prec
            result = _align_and_score(np.asarray(ts, float), np.asarray(vs, float),
                                      np.asarray(tp, float), np.asarray(vp, float),
                                      f0=s["f0"], cycles=s["cycles"])
            self._q.put(("done", result))
        except Exception as exc:                              # noqa: BLE001
            self._q.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            for inst in reversed(opened):
                try:
                    inst.close()
                except Exception:                             # noqa: BLE001
                    pass

    # ------------------------------------------------------------ queue pump
    def _drain(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] == "done":
                    self._show(msg[1])
                elif msg[0] == "error":
                    self.run_btn.configure(state="normal")
                    self.status.set("error")
                    messagebox.showerror("Capture failed", msg[1])
        except queue.Empty:
            pass
        self.after(120, self._drain)

    # --------------------------------------------------------------- display
    def _show(self, r):
        self.run_btn.configure(state="normal")
        self._last = r
        tg_ms = r["tg"] * 1000.0
        self.ax.clear(); self.ax_r.clear()
        self.ax.plot(tg_ms, r["vs"], color="#1565c0", lw=1.4, label="scope")
        self.ax.plot(tg_ms, r["vp"], color="#0a9c6b", lw=1.2, label="PMU (aligned)")
        self.ax.set_ylabel("volts"); self.ax.grid(True, alpha=0.3)
        self.ax.legend(fontsize=8, loc="upper right")
        self.ax.set_title("PMU vs scope waveform")
        self.ax_r.plot(tg_ms, r["resid"], color="#b00020", lw=1.0)
        self.ax_r.axhline(0, color="k", lw=0.5)
        self.ax_r.set_ylabel("residual (V)"); self.ax_r.set_xlabel("time (ms)")
        self.ax_r.grid(True, alpha=0.3)
        self.fig.tight_layout()
        self.canvas.draw_idle()
        self.metrics.configure(
            text=f"scope {r['vrms_s']:.3f} Vrms   PMU {r['vrms_p']:.3f} Vrms   "
                 f"Δmag {r['mag_pct']:+.3f}%   residual RMS {r['rms_err']:.3f} V "
                 f"({r['rms_pct']:.3f}% of scope)   peak {r['peak_err']:.3f} V")
        self.status.set(f"done — lag {r['lag_ms']:+.3f} ms aligned")

    def _reset_plot(self):
        for a in (self.ax, self.ax_r):
            a.clear(); a.grid(True, alpha=0.3)
        self.ax.set_title("capture to overlay PMU vs scope")
        self.ax.set_ylabel("volts"); self.ax_r.set_ylabel("residual (V)")
        self.ax_r.set_xlabel("time (ms)")
        if hasattr(self, "canvas"):
            self.canvas.draw_idle()

    def _save(self):
        if not self._last:
            self.status.set("nothing to save yet — capture first"); return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path("results"); out.mkdir(parents=True, exist_ok=True)
        path = out / f"waveform_pmu_vs_scope_{stamp}.png"
        self.fig.savefig(path, dpi=120)
        self.status.set(f"saved {path.name}")


def _align_and_score(ts, vs, tp, vp, *, f0: float, cycles: int, npts: int = 2000):
    """Resample scope & PMU onto a common grid, align by cross-correlation, and
    return overlay arrays + error metrics."""
    win = min(cycles / f0, ts[-1] - ts[0], tp[-1] - tp[0])
    tg = np.linspace(0.0, win, npts)
    dt = win / (npts - 1)
    g_s = np.interp(tg, ts - ts[0], vs)
    g_p = np.interp(tg, tp - tp[0], vp)

    # Cross-correlate (mean-removed) to find the PMU->scope lag, then roll.
    a = g_p - g_p.mean()
    b = g_s - g_s.mean()
    corr = np.correlate(a, b, mode="full")
    lag = int(corr.argmax() - (npts - 1))
    g_p_al = np.roll(g_p, -lag)

    # Score on the central 80% to avoid the circular-roll wrap at the edges.
    lo, hi = int(0.1 * npts), int(0.9 * npts)
    sl = slice(lo, hi)
    resid = g_p_al - g_s
    rms_err = float(np.sqrt(np.mean(resid[sl] ** 2)))
    peak_err = float(np.max(np.abs(resid[sl])))
    vrms_s = float(np.sqrt(np.mean((g_s[sl] - g_s[sl].mean()) ** 2)))
    vrms_p = float(np.sqrt(np.mean((g_p_al[sl] - g_p_al[sl].mean()) ** 2)))
    rms_pct = (rms_err / vrms_s * 100.0) if vrms_s else float("nan")
    mag_pct = ((vrms_p - vrms_s) / vrms_s * 100.0) if vrms_s else float("nan")
    return {"tg": tg, "vs": g_s, "vp": g_p_al, "resid": resid,
            "rms_err": rms_err, "peak_err": peak_err, "rms_pct": rms_pct,
            "vrms_s": vrms_s, "vrms_p": vrms_p, "mag_pct": mag_pct,
            "lag_ms": lag * dt * 1000.0}
