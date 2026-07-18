"""Harmonics tab for the validation GUI.

Drive a harmonic-rich stimulus and compare per-harmonic content three ways:

* **theoretical** — the exact Fourier series of the selected 3325B waveform
  shape (or, in Simulate mode, a custom harmonic mix you type in),
* **scope** — the FFT of the scope's captured waveform (the reference), and
* **PMU** — the FFT of the PMU's own continuous ADC stream (the DUT).

Shows a grouped bar chart (% of fundamental per order), the three THD figures,
and a per-order table with the PMU's error vs the scope. Runs on a worker thread
with its own queue, reusing the shared connection settings from the Instruments
bar. The DMM isn't used here (it can't resolve individual harmonics).
"""
from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from . import harmonics as H

HCOLS = [("order", "Order", 60), ("theo", "Theoretical %", 110),
         ("scope", "Scope %", 90), ("pmu", "PMU %", 90),
         ("err", "PMU−scope (pp)", 110)]


class HarmonicsFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last = None            # (shape, f0, theo, scope_res, pmu_res)
        self._build_vars()
        self._build()
        self.after(120, self._drain)

    def _build_vars(self):
        self.freq = tk.StringVar(value="60")
        self.vrms = tk.StringVar(value="5")
        self.shape = tk.StringVar(value="Square")
        self.nharm = tk.StringVar(value="13")
        self.settle = tk.StringVar(value="2.5")
        self.custom = tk.StringVar(value="")
        self.status = tk.StringVar(value="pick a stimulus and press Analyze")

    # ---------------------------------------------------------------- layout
    def _build(self):
        ctl = ttk.LabelFrame(self, text="Stimulus")
        ctl.pack(fill="x", padx=8, pady=6)

        def lab(t, c, r=0):
            ttk.Label(ctl, text=t).grid(row=r, column=c, sticky="e", padx=(8, 2), pady=3)

        lab("Shape", 0)
        ttk.Combobox(ctl, textvariable=self.shape, width=10, state="readonly",
                     values=H.SHAPES).grid(row=0, column=1, sticky="w")
        lab("Fund. freq (Hz)", 2)
        ttk.Entry(ctl, textvariable=self.freq, width=8).grid(row=0, column=3, sticky="w")
        lab("Fund. Vrms", 4)
        ttk.Entry(ctl, textvariable=self.vrms, width=8).grid(row=0, column=5, sticky="w")
        lab("# harmonics", 6)
        ttk.Entry(ctl, textvariable=self.nharm, width=5).grid(row=0, column=7, sticky="w")
        lab("settle (s)", 8)
        ttk.Entry(ctl, textvariable=self.settle, width=6).grid(row=0, column=9, sticky="w")

        lab("Custom (sim only)  order:frac,…", 0, r=1)
        ttk.Entry(ctl, textvariable=self.custom, width=30).grid(
            row=1, column=1, columnspan=5, sticky="w", pady=(0, 4))
        self.run_btn = ttk.Button(ctl, text="▶ Analyze", command=self._on_run)
        self.run_btn.grid(row=1, column=6, columnspan=2, padx=6)
        ttk.Button(ctl, text="Save CSV/PNG", command=self._save).grid(
            row=1, column=8, columnspan=2)

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        # table
        tf = ttk.Frame(body)
        cols = [c[0] for c in HCOLS]
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=14)
        for key, title, w in HCOLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=w, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview).pack(side="right", fill="y")
        body.add(tf, weight=3)
        # plot
        pf = ttk.Frame(body)
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self._reset_plot()
        self.canvas = FigureCanvasTkAgg(self.fig, master=pf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        body.add(pf, weight=3)

        thd = ttk.Frame(self)
        thd.pack(fill="x", padx=10, pady=(0, 2))
        self.thd_lbl = ttk.Label(thd, text="", font=("Consolas", 10, "bold"))
        self.thd_lbl.pack(side="left")
        ttk.Label(self, textvariable=self.status, foreground="#555").pack(
            anchor="w", padx=10, pady=(0, 6))

    # ------------------------------------------------------------------- run
    def _on_run(self):
        if self._worker and self._worker.is_alive():
            return
        try:
            f0 = float(self.freq.get()); v = float(self.vrms.get())
            n = int(self.nharm.get()); settle = float(self.settle.get())
        except ValueError as exc:
            messagebox.showerror("Bad input", str(exc)); return
        custom = self._parse_custom()
        if custom is not None and not self.app.simulate.get():
            messagebox.showinfo(
                "Custom harmonics",
                "An arbitrary harmonic mix can't be produced by one 3325B on real "
                "hardware — the stimulus will use the selected waveform shape "
                "instead. (Custom mixes work in Simulate mode.)")
            custom = None

        self.tree.delete(*self.tree.get_children())
        self._reset_plot(); self.thd_lbl.configure(text="")
        self.run_btn.configure(state="disabled")
        self.status.set("running…")
        s = {
            "sim": self.app.simulate.get(),
            "f0": f0, "vrms": v, "shape": self.shape.get(), "n": n,
            "settle": settle, "custom": custom,
            "source_port": self.app.source_port.get(),
            "source_baud": int(self.app.source_baud.get()),
            "scope_ip": self.app.scope_ip.get(),
            "scope_ch": int(self.app.scope_ch.get()),
            "use_scope": self.app.use_scope.get(),
            "pmu_port": self.app.pmu_port.get(),
        }
        self._worker = threading.Thread(target=self._work, args=(s,), daemon=True)
        self._worker.start()

    def _parse_custom(self):
        text = self.custom.get().strip()
        if not text:
            return None
        out = {}
        for part in text.replace(";", ",").split(","):
            if not part.strip():
                continue
            k, _, val = part.partition(":")
            out[int(k)] = float(val)
        return out or None

    def _work(self, s: dict):
        from .instruments import make_source, make_scope, make_pmu
        from .virtualbench import VirtualBench
        import time
        bench = VirtualBench() if s["sim"] else None
        use_scope = s["use_scope"] and (s["sim"] or s["scope_ip"])
        opened = []
        try:
            source = make_source(s["sim"], port=s["source_port"],
                                 baud=s["source_baud"], bench=bench)
            scope = (make_scope(s["sim"], ip=s["scope_ip"], channel=s["scope_ch"],
                                bench=bench) if use_scope else None)
            pmu = make_pmu(s["sim"], port=s["pmu_port"], bench=bench)
            for inst in (source, scope, pmu):
                if inst is not None:
                    inst.open(); opened.append(inst)

            source.set_stimulus(s["f0"], s["vrms"], s["shape"], harmonics=s["custom"])
            pmu.arm(s["f0"], s["vrms"], 0.0)
            time.sleep(s["settle"])

            scope_res = scope.read_spectrum(s["f0"], s["n"]) if scope else None
            pmu_res = pmu.read_spectrum(s["f0"], s["n"], seconds=1.5)
            theo = (dict(s["custom"]) if s["custom"] is not None
                    else H.theoretical_fractions(s["shape"], s["n"]))
            self._q.put(("hdone", s["shape"], s["f0"], theo, scope_res, pmu_res))
        except Exception as exc:                              # noqa: BLE001
            self._q.put(("herror", f"{type(exc).__name__}: {exc}"))
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
                if msg[0] == "hdone":
                    self._show(*msg[1:])
                elif msg[0] == "herror":
                    self.run_btn.configure(state="normal")
                    self.status.set("error")
                    messagebox.showerror("Analyze failed", msg[1])
        except queue.Empty:
            pass
        self.after(120, self._drain)

    # --------------------------------------------------------------- display
    def _show(self, shape, f0, theo, scope_res, pmu_res):
        self.run_btn.configure(state="normal")
        self._last = (shape, f0, theo, scope_res, pmu_res)
        orders = sorted(set(list(theo.keys())
                            + (scope_res.orders if scope_res else [])
                            + (pmu_res.orders if pmu_res else [])))
        sc = scope_res.fraction_pct if scope_res else {}
        pm = pmu_res.fraction_pct if pmu_res else {}
        for k in orders:
            t = theo.get(k, 0.0) * 100.0
            scv = sc.get(k)
            pmv = pm.get(k)
            err = (pmv - scv) if (scv is not None and pmv is not None) else None
            self.tree.insert("", "end", values=(
                k, f"{t:.2f}",
                "--" if scv is None else f"{scv:.2f}",
                "--" if pmv is None else f"{pmv:.2f}",
                "--" if err is None else f"{err:+.2f}"))

        theo_thd = H.theoretical_thd_pct(theo)
        sthd = scope_res.thd_pct if scope_res else float("nan")
        pthd = pmu_res.thd_pct if pmu_res else float("nan")
        self.thd_lbl.configure(
            text=f"THD   theoretical {theo_thd:5.2f}%    scope {sthd:5.2f}%    "
                 f"PMU {pthd:5.2f}%")
        self._plot(orders, theo, sc, pm, shape, f0)
        self.status.set(f"done — {shape} @ {f0:g} Hz")

    def _reset_plot(self):
        self.ax.clear()
        self.ax.set_title("harmonic content (% of fundamental)")
        self.ax.set_xlabel("harmonic order"); self.ax.set_ylabel("% of fundamental")
        self.ax.grid(True, axis="y", alpha=0.3)
        if hasattr(self, "canvas"):
            self.canvas.draw_idle()

    def _plot(self, orders, theo, sc, pm, shape, f0):
        self.ax.clear()
        import numpy as np
        x = np.arange(len(orders))
        w = 0.27
        self.ax.bar(x - w, [theo.get(k, 0.0) * 100 for k in orders], w,
                    label="theoretical", color="#9aa7b4")
        self.ax.bar(x, [sc.get(k, 0.0) for k in orders], w,
                    label="scope", color="#1565c0")
        self.ax.bar(x + w, [pm.get(k, 0.0) for k in orders], w,
                    label="PMU", color="#0a9c6b")
        self.ax.set_xticks(x)
        self.ax.set_xticklabels([str(k) for k in orders])
        self.ax.set_xlabel("harmonic order"); self.ax.set_ylabel("% of fundamental")
        self.ax.set_title(f"{shape} @ {f0:g} Hz — harmonic content")
        self.ax.grid(True, axis="y", alpha=0.3)
        self.ax.legend(fontsize=8)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    # ---------------------------------------------------------------- export
    def _save(self):
        if not self._last:
            self.status.set("nothing to save yet — run an analysis first"); return
        shape, f0, theo, scope_res, pmu_res = self._last
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path("results"); out.mkdir(parents=True, exist_ok=True)
        base = out / f"harmonics_{shape}_{f0:g}Hz_{stamp}"
        orders = sorted(set(list(theo.keys())
                            + (scope_res.orders if scope_res else [])
                            + (pmu_res.orders if pmu_res else [])))
        sc = scope_res.fraction_pct if scope_res else {}
        pm = pmu_res.fraction_pct if pmu_res else {}
        with (base.with_suffix(".csv")).open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["order", "theoretical_pct", "scope_pct", "pmu_pct", "pmu_minus_scope_pp"])
            for k in orders:
                t = theo.get(k, 0.0) * 100
                scv, pmv = sc.get(k), pm.get(k)
                err = (pmv - scv) if (scv is not None and pmv is not None) else ""
                w.writerow([k, f"{t:.4f}", "" if scv is None else f"{scv:.4f}",
                            "" if pmv is None else f"{pmv:.4f}",
                            "" if err == "" else f"{err:.4f}"])
        self.fig.savefig(base.with_suffix(".png"), dpi=120)
        self.status.set(f"saved {base.name}.csv and .png")
