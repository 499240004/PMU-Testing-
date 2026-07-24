"""Monitor tab: track the PMU's accuracy against the bench references over time.

The point of this tab is to answer one question at a glance -- *how accurate is
the PMU right now, and how steady is it?* -- so it leads with three big live
error readouts and backs them with error-vs-time graphs:

    * **Magnitude error**  PMU vmag vs the DMM (the amplitude reference), in %.
    * **Frequency error**  PMU freq vs the DMM's FREQ counter (the frequency
      reference -- a reciprocal counter good to a few mHz; the scope's
      single-shot edge measurement is ~100 mHz-class on the attenuated line
      sine, so it is only the fallback), in mHz.
    * **Phase**            the PMU's own reported phasor angle (IEEE C37.118).
      Per the spec, frequency is the derivative of this angle, so at steady
      state its drift rate IS the residual frequency error (1 deg/s = 2.78
      mHz); the card shows the live angle and the fitted drift. No scope
      channel is involved -- the phasor comes straight from the PMU stream.
    * **TVE**              the PMU's reported total vector error vs its static
      top-of-second reference, with the live error budget: a phase error dphi
      contributes 2*sin(dphi/2)*100 % (~1.745 %/deg, so 1 % TVE = 0.573 deg),
      the magnitude error contributes its own %, and TVE ~ rss of the two.
      Only meaningful at nominal frequency -- off-nominal the phasor rotates
      against the static reference (the documented host caveat).

Each metric shows the **live** (latest) value and the **running average ± 1σ**
so both the instantaneous reading and its steadiness are visible. The graphs
plot the *errors* (auto-scaled to each error's own range, with a zero line and
the running mean) instead of the raw ~115 V magnitudes, which all sit on top of
each other and hide the very deviations we care about.

Every sample is appended to a CSV as it is taken, so a multi-hour run survives a
crash. Worker thread + queue; a threading.Event stops it between samples.
"""
from __future__ import annotations

import csv
import math
import queue
import statistics
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .paths import results_dir

CSV_FIELDS = ["t_s", "wall_clock", "dmm_vrms", "dmm_freq_hz", "scope_vrms",
              "scope_freq_hz", "pmu_vmag", "pmu_freq_hz", "pmu_phase_deg",
              "pmu_tve_pct", "synced", "mag_err_pct", "scope_mag_err_pct",
              "freq_err_mhz", "freq_err_pct", "freq_ref"]

# Soft tolerances -- only used to colour the live readouts green/amber.
TOL_MAG_PCT = 0.5
TOL_FREQ_MHZ = 50.0
TOL_FREQ_PCT = TOL_FREQ_MHZ / 600.0      # same limit in % of 60 Hz (0.083%)

GREEN, AMBER, GRAY = "#0a9c6b", "#d17a00", "#999"


class MonitorFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._t: list[float] = []
        # error series that drive the readouts + graphs
        self._series: dict[str, list] = {k: [] for k in
                                         ("mag", "smag", "freq", "phase", "tve")}
        self._csv_path: Path | None = None
        self._cards: dict[str, dict] = {}
        self._build_vars()
        self._build()
        self.after(150, self._drain)

    def _build_vars(self):
        self.interval = tk.StringVar(value="5")
        self.duration = tk.StringVar(value="0")     # minutes; 0 = until Stop
        self.use_scope = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Set an interval and press Start.")
        self.raw = tk.StringVar(value="")

    # ---------------------------------------------------------------- layout
    def _build(self):
        ctl = ttk.LabelFrame(self, text="Long-term accuracy monitor (DMM · scope · PMU)")
        ctl.pack(fill="x", padx=8, pady=6)

        def lab(t, c):
            ttk.Label(ctl, text=t).grid(row=0, column=c, sticky="e", padx=(8, 2), pady=4)

        lab("interval (s)", 0)
        ttk.Entry(ctl, textvariable=self.interval, width=6).grid(row=0, column=1, sticky="w")
        lab("duration (min, 0=∞)", 2)
        ttk.Entry(ctl, textvariable=self.duration, width=6).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(ctl, text="use scope", variable=self.use_scope).grid(
            row=0, column=4, padx=10)
        self.start_btn = ttk.Button(ctl, text="▶ Start", command=self._on_start)
        self.start_btn.grid(row=0, column=7, padx=6)
        self.stop_btn = ttk.Button(ctl, text="■ Stop", command=self._on_stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=8, padx=2)

        # --- big live error readouts -------------------------------------
        cards = ttk.Frame(self)
        cards.pack(fill="x", padx=8, pady=(2, 4))
        for i, (key, title, sub) in enumerate((
                ("mag", "Magnitude error", "PMU vs DMM"),
                ("freq", "Frequency error", "PMU vs DMM freq counter"),
                ("phase", "Phasor angle", "PMU phasor (C37.118) — drift ⇒ freq err"),
                ("tve", "TVE", "vs static top-of-second ref (nominal f only)"))):
            cards.columnconfigure(i, weight=1)
            self._cards[key] = self._make_card(cards, i, title, sub)

        ttk.Label(self, textvariable=self.raw, font=("Consolas", 9),
                  foreground="#444").pack(anchor="w", padx=10, pady=(0, 2))

        # --- error-vs-time graphs ----------------------------------------
        pf = ttk.Frame(self)
        pf.pack(fill="both", expand=True, padx=8, pady=(2, 4))
        self.fig = Figure(figsize=(7, 5.4), dpi=100)
        self.ax_mag = self.fig.add_subplot(311)
        self.ax_freq = self.fig.add_subplot(312, sharex=self.ax_mag)
        self.ax_phase = self.fig.add_subplot(313, sharex=self.ax_mag)
        self._reset_plot()
        self.canvas = FigureCanvasTkAgg(self.fig, master=pf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        ttk.Label(self, textvariable=self.status, foreground="#555").pack(
            anchor="w", padx=10, pady=(0, 6))

    def _make_card(self, parent, col, title, sub):
        box = ttk.LabelFrame(parent, text=title)
        box.grid(row=0, column=col, sticky="nsew", padx=4)
        subl = ttk.Label(box, text=sub, foreground="#777", font=("Segoe UI", 8))
        subl.pack(anchor="w", padx=8)
        live = tk.Label(box, text="-- ", font=("Segoe UI", 22, "bold"),
                        foreground=GRAY)
        live.pack(anchor="w", padx=8)
        avg = tk.Label(box, text="avg  --", font=("Consolas", 10),
                       foreground="#555")
        avg.pack(anchor="w", padx=8)
        extra = tk.Label(box, text="", font=("Consolas", 9), foreground="#777")
        extra.pack(anchor="w", padx=8, pady=(0, 4))
        return {"box": box, "sub": subl, "live": live, "avg": avg, "extra": extra}

    # --------------------------------------------------------------- controls
    def _on_start(self):
        if self._worker and self._worker.is_alive():
            return
        try:
            interval = max(0.5, float(self.interval.get()))
            duration = float(self.duration.get())
        except ValueError as exc:
            messagebox.showerror("Bad input", str(exc)); return
        self._t.clear()
        for v in self._series.values():
            v.clear()
        use_scope = self.use_scope.get()
        self._reset_plot(); self.raw.set("")
        for c in self._cards.values():
            c["live"].configure(text="-- ", foreground=GRAY)
            c["avg"].configure(text="avg  --")
            c["extra"].configure(text="")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = results_dir()
        self._csv_path = out / f"monitor_{'sim' if self.app.simulate.get() else 'hw'}_{stamp}.csv"
        s = {
            "simulate": self.app.simulate.get(),
            "dmm_port": self.app.dmm_port.get(), "dmm_baud": int(self.app.dmm_baud.get()),
            "dmm_parity": self.app.dmm_parity.get(),
            "scope_ip": self.app.scope_ip.get(), "scope_ch": int(self.app.scope_ch.get()),
            "scope_probe": float(self.app.scope_probe.get() or 1),
            "use_scope": use_scope,
            "pmu_port": self.app.pmu_port.get(),
            "vpc": float(self.app.vpc.get()) if self.app.vpc.get().strip() else None,
            "interval": interval, "duration_s": duration * 60.0,
        }
        self._stop.clear()
        self._worker = threading.Thread(target=self._work, args=(s,), daemon=True)
        self._worker.start()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.set(f"monitoring every {interval:g}s → {self._csv_path.name}")

    def _on_stop(self):
        self._stop.set()
        self.stop_btn.configure(state="disabled")
        self.status.set("stopping after current sample…")

    # ----------------------------------------------------------- worker thread
    def _work(self, s: dict):
        from .instruments import make_dmm, make_scope, make_pmu
        from .virtualbench import VirtualBench
        bench = VirtualBench() if s["simulate"] else None
        use_scope = s["use_scope"] and (s["simulate"] or s["scope_ip"])
        opened = []
        fh = None
        try:
            dmm = make_dmm(s["simulate"], port=s["dmm_port"], baud=s["dmm_baud"],
                           parity=s["dmm_parity"], bench=bench)
            scope = (make_scope(s["simulate"], ip=s["scope_ip"], channel=s["scope_ch"],
                                probe_atten=s["scope_probe"], bench=bench)
                     if use_scope else None)
            pmu = make_pmu(s["simulate"], port=s["pmu_port"], volts_per_count=s["vpc"],
                           bench=bench)
            for inst in (dmm, scope, pmu):
                if inst is not None:
                    inst.open(); opened.append(inst)
            if s["simulate"]:
                bench.set_signal(60.0, 120.0, 0.0)
                pmu.arm(60.0, 120.0, 0.0)
                time.sleep(1.5)

            fh = self._csv_path.open("w", newline="")
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()

            t0 = time.monotonic()
            while not self._stop.is_set():
                trel = time.monotonic() - t0
                row = self._sample(dmm, scope, pmu, trel, s)
                writer.writerow(row); fh.flush()
                self._q.put(("sample", row))
                if s["duration_s"] and trel >= s["duration_s"]:
                    break
                if self._stop.wait(s["interval"]):
                    break
        except Exception as exc:                              # noqa: BLE001
            self._q.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            if fh is not None:
                fh.close()
            for inst in reversed(opened):
                try:
                    inst.close()
                except Exception:                             # noqa: BLE001
                    pass
            self._q.put(("done", None))

    def _sample(self, dmm, scope, pmu, trel: float, s: dict) -> dict:
        def safe(fn, default=None):
            try:
                return fn()
            except Exception:                                 # noqa: BLE001
                return default
        dmm_v = safe(lambda: dmm.read_vrms(navg=2))
        dmm_f = safe(lambda: dmm.read_freq(navg=1))
        sc = safe(lambda: scope.read(navg=1), {}) if scope is not None else {}
        pr = safe(lambda: pmu.read(navg=4, timeout_s=6.0, require_sync=False), {}) or {}
        pmu_v = pr.get("vmag"); pmu_f = pr.get("freq")
        sc_v = sc.get("vrms") if sc else None
        sc_f = sc.get("freq") if sc else None

        def isnum(x):
            return isinstance(x, (int, float)) and x == x   # not NaN
        pct = lambda m, r: ((m - r) / r * 100.0) if (isnum(m) and isnum(r) and r) else None
        # Frequency reference: the DMM's reciprocal counter (34401A FREQ,
        # ~+/-6 mHz at 60 Hz) -- the scope's single-shot edge measurement on the
        # attenuated line sine is ~+/-100 mHz (observed: CH1 vs the PMU ZC
        # pulses on CH3 disagreed by ~98 mHz on the same acquisition), so the
        # scope is only the fallback when the DMM read fails.
        freq_ref_val = dmm_f if isnum(dmm_f) else sc_f
        freq_ref = "dmm" if isnum(dmm_f) else "scope"
        have_f = isnum(pmu_f) and isnum(freq_ref_val) and freq_ref_val
        freq_err = (pmu_f - freq_ref_val) * 1000.0 if have_f else None
        freq_err_pct = ((pmu_f - freq_ref_val) / freq_ref_val * 100.0
                        if have_f else None)
        return {
            "t_s": round(trel, 3),
            "wall_clock": datetime.now().isoformat(timespec="seconds"),
            "dmm_vrms": dmm_v, "dmm_freq_hz": dmm_f,
            "scope_vrms": sc_v, "scope_freq_hz": sc_f,
            "pmu_vmag": pmu_v, "pmu_freq_hz": pmu_f, "pmu_phase_deg": pr.get("phase"),
            "pmu_tve_pct": pr.get("tve"), "synced": pr.get("synced", False),
            "mag_err_pct": pct(pmu_v, dmm_v),
            "scope_mag_err_pct": pct(sc_v, dmm_v),
            "freq_err_mhz": freq_err, "freq_err_pct": freq_err_pct,
            "freq_ref": freq_ref,
        }

    # ------------------------------------------------------------ queue pump
    def _drain(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] == "sample":
                    self._add(msg[1])
                elif msg[0] == "error":
                    messagebox.showerror("Monitor error", msg[1])
                    self.status.set("error")
                elif msg[0] == "done":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    n = len(self._t)
                    self.status.set(f"stopped — {n} sample(s)"
                                    + (f", wrote {self._csv_path.name}"
                                       if self._csv_path and n else ""))
        except queue.Empty:
            pass
        self.after(150, self._drain)

    # --------------------------------------------------------------- display
    def _add(self, row: dict):
        self._t.append(row["t_s"])
        self._series["mag"].append(row["mag_err_pct"])
        self._series["smag"].append(row["scope_mag_err_pct"])
        self._series["freq"].append(row["freq_err_pct"])
        self._series["phase"].append(row["pmu_phase_deg"])
        self._series["tve"].append(row["pmu_tve_pct"])
        self._update_cards()
        self._redraw()
        self._update_raw(row)

    # ------- live readout cards --------------------------------------------
    @staticmethod
    def _stats(vals):
        vals = [v for v in vals if isinstance(v, (int, float)) and v == v]
        if not vals:
            return None, None, None, 0
        mean = statistics.fmean(vals)
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        return vals[-1], mean, sd, len(vals)

    def _update_cards(self):
        def paint(key, unit, fmt, tol=None):
            live, mean, sd, n = self._stats(self._series[key])
            c = self._cards[key]
            if live is None:
                c["live"].configure(text="-- ", foreground=GRAY)
                c["avg"].configure(text="avg  --")
                return
            color = GREEN if (tol is None or abs(live) <= tol) else AMBER
            c["live"].configure(text=fmt.format(live) + unit, foreground=color)
            c["avg"].configure(
                text=f"avg {fmt.format(mean)} ± {fmt.format(sd).lstrip('+')}{unit}  n={n}")
        paint("mag", " %", "{:+.3f}", TOL_MAG_PCT)
        paint("freq", " %", "{:+.4f}", TOL_FREQ_PCT)
        paint("tve", " %", "{:.3f}", 1.0)      # C37.118 M-class limit
        self._paint_phase()
        self._paint_tve_budget()

    def _paint_tve_budget(self):
        """Error-budget lines: express the phase error in TVE-% terms.

        A phase error dphi contributes 2*sin(dphi/2) (x100 %) to TVE and the
        magnitude error contributes |mag_err_pct| directly; for small errors
        TVE ~ sqrt(mag% ** 2 + phase% ** 2). Painting the live breakdown next
        to the PMU's own reported TVE shows which term dominates.
        """
        num = lambda v: isinstance(v, (int, float)) and v == v
        mags = [v for v in self._series["mag"] if num(v)]
        _, phs = self._phase_xy()
        pc = self._cards["phase"]["extra"]
        tc = self._cards["tve"]["extra"]
        if not phs:
            pc.configure(text="")
            tc.configure(text="")
            return
        phase_pct = abs(200.0 * math.sin(math.radians(phs[-1]) / 2.0))
        pc.configure(text=f"≙ TVE contribution {phase_pct:.3f} %")
        if mags:
            m = abs(mags[-1])
            rss = math.hypot(m, phase_pct)
            tc.configure(text=f"mag {m:.3f}% ⊕ phase {phase_pct:.3f}% → {rss:.3f}%")
        else:
            tc.configure(text=f"phase contribution {phase_pct:.3f} %")

    def _phase_xy(self):
        """(t, phase) pairs for the valid phasor-angle samples, matched up."""
        pairs = [(ti, v) for ti, v in zip(self._t, self._series["phase"])
                 if isinstance(v, (int, float)) and v == v]
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def _paint_phase(self):
        """Phasor-angle card: live angle + fitted drift rate.

        Per C37.118 frequency is the derivative of the phasor angle, so the
        drift doubles as an independent frequency-error estimate:
        1 deg/s = (1/360) Hz = 2.78 mHz. Colour by that implied error against
        the same tolerance as the frequency card. Note the sampling limit: the
        unwrap is only unambiguous while |freq err| < 500/interval_s mHz
        (at the default 5 s interval, +/-100 mHz).
        """
        c = self._cards["phase"]
        xs, ys = self._phase_xy()
        if not ys:
            c["live"].configure(text="-- ", foreground=GRAY)
            c["avg"].configure(text="avg  --")
            return
        drift = None
        if len(ys) >= 2:
            unwrapped = np.degrees(np.unwrap(np.radians(ys)))
            drift = float(np.polyfit(xs, unwrapped, 1)[0])       # deg/s
        # 1 deg/s = (1/360) Hz = 0.00463% of 60 Hz
        pct = drift / 0.36 / 600.0 if drift is not None else None
        color = GREEN if (pct is None or abs(pct) <= TOL_FREQ_PCT) else AMBER
        c["live"].configure(text=f"{ys[-1]:+.2f}°", foreground=color)
        c["avg"].configure(
            text=(f"drift {drift:+.3f}°/s ≙ {pct:+.4f} %  n={len(ys)}"
                  if drift is not None else f"n={len(ys)}"))

    def _update_raw(self, row):
        def f(x, u, d=2):
            return f"{x:.{d}f}{u}" if isinstance(x, (int, float)) and x == x else "--"
        self.raw.set(
            f"DMM {f(row['dmm_vrms'],'V')} / PMU {f(row['pmu_vmag'],'V')}"
            f" / scope {f(row['scope_vrms'],'V')}    "
            f"freq PMU {f(row['pmu_freq_hz'],'Hz',3)} (ref {row['freq_ref']})    "
            f"sample {len(self._t)} @ {row['t_s']:.0f}s"
            + ("" if row["synced"] else "   [PMU unsynced]"))

    # ------- error graphs --------------------------------------------------
    def _reset_plot(self):
        for a in (self.ax_mag, self.ax_freq, self.ax_phase):
            a.clear()
        self.ax_mag.set_ylabel("mag err\n(%)")
        self.ax_freq.set_ylabel("freq err\n(%)")
        self.ax_phase.set_ylabel("phase\n(°)")
        self.ax_phase.set_xlabel("elapsed (s)")
        for a in (self.ax_mag, self.ax_freq, self.ax_phase):
            a.grid(True, alpha=0.3)
        self.ax_mag.set_title("PMU error over time")
        if hasattr(self, "canvas"):
            self.fig.tight_layout()
            self.canvas.draw_idle()

    def _redraw(self):
        t = self._t

        def xy(key):
            xs = [ti for ti, v in zip(t, self._series[key])
                  if isinstance(v, (int, float)) and v == v]
            ys = [v for v in self._series[key]
                  if isinstance(v, (int, float)) and v == v]
            return xs, ys

        def panel(ax, key, color, label, zero_ref=True):
            ax.clear()
            xs, ys = xy(key)
            if xs:
                ax.plot(xs, ys, "-", color=color, lw=1.3, marker=".", ms=3,
                        label=label)
                mean = statistics.fmean(ys)
                ax.axhline(mean, color=color, lw=0.8, ls="--", alpha=0.6,
                           label=f"mean {mean:+.3g}")
            if zero_ref:
                ax.axhline(0, color="k", lw=0.6)
            ax.grid(True, alpha=0.3)
            if xs:
                ax.legend(fontsize=7, loc="upper right", ncol=2)

        panel(self.ax_mag, "mag", GREEN, "PMU vs DMM")
        # overlay the scope's own magnitude error vs DMM as a faint cross-check
        sxs, sys = xy("smag")
        if sxs:
            self.ax_mag.plot(sxs, sys, "-", color="#1565c0", lw=0.9, alpha=0.5,
                             label="scope vs DMM")
            self.ax_mag.legend(fontsize=7, loc="upper right", ncol=2)
        panel(self.ax_freq, "freq", "#b06a00", "PMU freq err")
        # Phase: the PMU's phasor angle, unwrapped so the drift slope (the
        # residual frequency error, f = f0 + (1/360) dphi/dt) reads directly
        # instead of sawtoothing at the +/-180 deg wraps.
        self.ax_phase.clear()
        pxs, pys = self._phase_xy()
        if pxs:
            unwrapped = np.degrees(np.unwrap(np.radians(pys)))
            self.ax_phase.plot(pxs, unwrapped, "-", color="#7b3fbf", lw=1.3,
                               marker=".", ms=3, label="PMU phasor angle")
            if len(pxs) >= 2:
                k, b = np.polyfit(pxs, unwrapped, 1)
                self.ax_phase.plot(pxs, [k * x + b for x in pxs], "--",
                                   color="#7b3fbf", lw=0.8, alpha=0.6,
                                   label=f"drift {k:+.3g}°/s ≙ {k / 0.36 / 600.0:+.4f} %")
            self.ax_phase.legend(fontsize=7, loc="upper right", ncol=2)
        self.ax_phase.grid(True, alpha=0.3)

        self.ax_mag.set_ylabel("mag err\n(%)")
        self.ax_freq.set_ylabel("freq err\n(%)")
        self.ax_phase.set_ylabel("phase\n(°)")
        self.ax_phase.set_xlabel("elapsed (s)")
        self.ax_mag.set_title("PMU error over time")
        self.fig.tight_layout()
        self.canvas.draw_idle()
