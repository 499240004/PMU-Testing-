"""Desktop control panel for the micro-PMU validation bench (Tkinter).

A soft front panel over the same engine the CLI uses: pick Simulate or wire up
real instruments, choose a plan (amplitude / frequency), hit Run, and watch each
test point fill into a live table and an embedded error plot. On completion it
writes the same CSV/PNG as the CLI and prints the summary (including the
recommended ``volts_per_count`` for the amplitude plan).

Run it with ``pmu-validate-gui`` or ``python -m pmu_validation.gui``.

Design notes
------------
* The sweep blocks (it sleeps to settle and polls the PMU), so it runs on a
  worker thread. The worker only touches instruments and a queue; every Tk
  widget update happens on the main thread via ``root.after`` draining the queue.
* Stop sets a ``threading.Event`` the sequencer checks between points.
* The plot uses a bare ``Figure`` + ``FigureCanvasTkAgg`` (no pyplot globals).
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from . import plan as plans
from .results import build_row, write_csv, summarize, plot as save_plot
from .sequencer import run_sweep
from .setup_guide import SetupGuideFrame
from .harmonics_tab import HarmonicsFrame

# Which derived columns to plot per plan (label, row-key, axis).
_PLOT_SERIES = {
    "amplitude": {
        "x": ("cmd_vrms", "commanded Vrms"),
        "left": [("PMU vs DMM mag err %", "vmag_err_vs_dmm_pct"),
                 ("DMM vs scope %", "dmm_vs_scope_pct")],
    },
    "frequency": {
        "x": ("cmd_freq_hz", "commanded frequency (Hz)"),
        "left": [("PMU freq err vs cmd (mHz)", "freq_err_vs_cmd_mhz"),
                 ("PMU freq err vs scope (mHz)", "freq_err_vs_scope_mhz")],
    },
}

TABLE_COLS = [
    ("label", "Point", 90),
    ("dmm", "DMM Vrms", 90),
    ("scope", "Scope Vrms", 90),
    ("pmu_v", "PMU Vrms", 90),
    ("pmu_f", "PMU f (Hz)", 90),
    ("tve", "TVE %", 70),
    ("lock", "Lock", 55),
    ("note", "Note", 220),
]


class ValidationGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("micro-PMU Validation Bench")
        root.geometry("1180x720")
        root.minsize(980, 620)

        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._rows: list[dict] = []
        self._kind = "amplitude"
        self._current_vpc = 0.0
        self._test_status: dict = {}      # instrument key -> status Label (setup tab)

        self._build_vars()
        self._build_layout()
        self._on_mode_change()
        self.root.after(100, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ vars
    def _build_vars(self):
        self.simulate = tk.BooleanVar(value=True)
        self.plan_name = tk.StringVar(value="amplitude")
        # connection
        self.source_port = tk.StringVar(value="COM5")
        self.source_baud = tk.StringVar(value="4800")
        self.dmm_port = tk.StringVar(value="COM10")
        self.dmm_baud = tk.StringVar(value="9600")
        self.dmm_parity = tk.StringVar(value="N")
        self.scope_ip = tk.StringVar(value="169.254.220.205")
        self.scope_ch = tk.StringVar(value="1")
        self.use_scope = tk.BooleanVar(value=True)
        self.pmu_port = tk.StringVar(value="auto")
        # test params
        self.points_text = tk.StringVar(value="1,2,3,4,5,6,8,10")
        self.amp_freq = tk.StringVar(value="60")
        self.settle = tk.StringVar(value="2.5")
        self.vpc = tk.StringVar(value="")
        self.status = tk.StringVar(value="ready")

    # ---------------------------------------------------------------- layout
    def _build_layout(self):
        # Connection settings are shared across both tabs -> keep them on top.
        self._build_connection_bar(self.root)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=6, pady=(0, 2))
        self._nb = nb

        setup_tab = SetupGuideFrame(nb, self)
        nb.add(setup_tab, text="  1 · Setup Guide  ")

        run_tab = ttk.Frame(nb)
        nb.add(run_tab, text="  2 · Run Validation  ")
        self._build_test_bar(run_tab)
        body = ttk.Panedwindow(run_tab, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._build_table(body)
        self._build_plot(body)
        self._build_summary(run_tab)

        harm_tab = HarmonicsFrame(nb, self)
        nb.add(harm_tab, text="  3 · Harmonics  ")
        self._harm_tab = harm_tab

        self._build_statusbar()

    def _labeled(self, parent, text, var, width, col, row=0, values=None):
        ttk.Label(parent, text=text).grid(row=row, column=col, sticky="e", padx=(8, 2), pady=2)
        if values:
            w = ttk.Combobox(parent, textvariable=var, width=width, values=values,
                             state="readonly")
        else:
            w = ttk.Entry(parent, textvariable=var, width=width)
        w.grid(row=row, column=col + 1, sticky="w", pady=2)
        return w

    def _build_connection_bar(self, parent):
        f = ttk.LabelFrame(parent, text="Instruments")
        f.pack(fill="x", padx=8, pady=6)
        ttk.Checkbutton(f, text="Simulate (no hardware)", variable=self.simulate,
                        command=self._on_mode_change).grid(row=0, column=0, columnspan=2,
                                                           sticky="w", padx=8, pady=4)
        self._hw = []
        self._hw.append(self._labeled(f, "3325B port", self.source_port, 8, 2))
        self._hw.append(self._labeled(f, "baud", self.source_baud, 7, 4))
        self._hw.append(self._labeled(f, "DMM port", self.dmm_port, 8, 6))
        self._hw.append(self._labeled(f, "baud", self.dmm_baud, 7, 8))
        self._hw.append(self._labeled(f, "parity", self.dmm_parity, 4, 10,
                                      values=["N", "E", "O"]))
        self._hw.append(self._labeled(f, "Scope IP", self.scope_ip, 16, 2, row=1))
        self._hw.append(self._labeled(f, "ch", self.scope_ch, 4, 4, row=1))
        ttk.Checkbutton(f, text="use scope", variable=self.use_scope).grid(
            row=1, column=6, columnspan=2, sticky="w", padx=8)
        self._hw.append(self._labeled(f, "PMU port", self.pmu_port, 8, 8, row=1))

    def _build_test_bar(self, parent):
        f = ttk.LabelFrame(parent, text="Test plan")
        f.pack(fill="x", padx=8, pady=(6, 6))
        ttk.Radiobutton(f, text="Amplitude (calibrate volts/count)",
                        variable=self.plan_name, value="amplitude",
                        command=self._on_plan_change).grid(row=0, column=0, sticky="w", padx=8)
        ttk.Radiobutton(f, text="Frequency (accuracy sweep)",
                        variable=self.plan_name, value="frequency",
                        command=self._on_plan_change).grid(row=0, column=1, sticky="w", padx=8)

        self._pts_label = ttk.Label(f, text="Levels (Vrms):")
        self._pts_label.grid(row=1, column=0, sticky="e", padx=(8, 2))
        ttk.Entry(f, textvariable=self.points_text, width=40).grid(
            row=1, column=1, columnspan=2, sticky="w")
        self._ampfreq_lbl = ttk.Label(f, text="at freq (Hz):")
        self._ampfreq_lbl.grid(row=1, column=3, sticky="e", padx=(8, 2))
        self._ampfreq_entry = ttk.Entry(f, textvariable=self.amp_freq, width=8)
        self._ampfreq_entry.grid(row=1, column=4, sticky="w")

        self._labeled(f, "settle (s)", self.settle, 6, 5, row=1)
        self._labeled(f, "volts/count", self.vpc, 12, 7, row=1)

        self.run_btn = ttk.Button(f, text="▶ Run", command=self._on_run)
        self.run_btn.grid(row=0, column=5, padx=6)
        self.stop_btn = ttk.Button(f, text="■ Stop", command=self._on_stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=6, padx=2)

    def _build_table(self, parent):
        frame = ttk.Frame(parent)
        cols = [c[0] for c in TABLE_COLS]
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        for key, title, width in TABLE_COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="center" if key != "note" else "w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("bad", foreground="#b00020")
        self.tree.tag_configure("nolock", foreground="#b06a00")
        parent.add(frame, weight=3)

    def _build_plot(self, parent):
        frame = ttk.Frame(parent)
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax2 = self.ax.twinx()
        self._reset_plot()
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        parent.add(frame, weight=2)

    def _build_summary(self, parent):
        f = ttk.LabelFrame(parent, text="Summary")
        f.pack(fill="x", padx=8, pady=(0, 4))
        self.summary = tk.Text(f, height=6, wrap="word", state="disabled",
                               font=("Consolas", 9))
        self.summary.pack(fill="x", padx=4, pady=4)
        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=4, pady=(0, 4))
        self.reco = ttk.Label(bar, text="", font=("Consolas", 9, "bold"),
                              foreground="#0a6")
        self.reco.pack(side="left")
        self.apply_btn = ttk.Button(bar, text="Use this volts/count",
                                    command=self._apply_reco, state="disabled")
        self.apply_btn.pack(side="right")
        self._reco_value = None

    def _build_statusbar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(bar, mode="determinate", length=200)
        self.progress.pack(side="left", padx=8, pady=3)
        ttk.Label(bar, textvariable=self.status).pack(side="left", padx=8)

    # ------------------------------------------------------------- callbacks
    def _on_mode_change(self):
        state = "disabled" if self.simulate.get() else "normal"
        for w in self._hw:
            try:
                w.configure(state=state if w["state"] != "readonly" or state == "disabled"
                            else "readonly")
            except tk.TclError:
                w.configure(state=state)

    def _on_plan_change(self):
        if self.plan_name.get() == "amplitude":
            self._pts_label.configure(text="Levels (Vrms):")
            self.points_text.set("1,2,3,4,5,6,8,10")
            self.amp_freq.set("60")
            self.settle.set("2.5")
            self._ampfreq_lbl.grid()
            self._ampfreq_entry.grid()
        else:
            self._pts_label.configure(text="Freqs (Hz):")
            self.points_text.set("57,58,59,59.5,60,60.5,61,62,63")
            self.settle.set("3.0")
            self._ampfreq_lbl.grid_remove()
            self._ampfreq_entry.grid_remove()

    def _parse_points(self):
        vals = [float(x) for x in self.points_text.get().replace(";", ",").split(",")
                if x.strip()]
        settle = float(self.settle.get())
        if self.plan_name.get() == "amplitude":
            return plans.amplitude_plan(freq_hz=float(self.amp_freq.get()),
                                        levels=tuple(vals), settle_s=settle)
        return plans.frequency_plan(freqs=tuple(vals), settle_s=settle)

    def _on_run(self):
        if self._worker and self._worker.is_alive():
            return
        try:
            points = self._parse_points()
        except ValueError as exc:
            messagebox.showerror("Bad input", f"Could not parse points/settle:\n{exc}")
            return
        if not points:
            messagebox.showerror("Bad input", "No test points specified.")
            return

        self._kind = self.plan_name.get()
        self._rows = []
        self.tree.delete(*self.tree.get_children())
        self._reset_plot()
        self._set_summary("")
        self.reco.configure(text="")
        self.apply_btn.configure(state="disabled")
        self._reco_value = None
        self.progress.configure(maximum=len(points), value=0)
        self._stop.clear()
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.set(f"running {self._kind} sweep, {len(points)} points...")

        settings = {
            "simulate": self.simulate.get(),
            "source_port": self.source_port.get(), "source_baud": int(self.source_baud.get()),
            "dmm_port": self.dmm_port.get(), "dmm_baud": int(self.dmm_baud.get()),
            "dmm_parity": self.dmm_parity.get(),
            "scope_ip": self.scope_ip.get(), "scope_ch": int(self.scope_ch.get()),
            "use_scope": self.use_scope.get(),
            "pmu_port": self.pmu_port.get(),
            "vpc": float(self.vpc.get()) if self.vpc.get().strip() else None,
        }
        self._worker = threading.Thread(target=self._run_worker,
                                        args=(settings, points), daemon=True)
        self._worker.start()

    def _on_stop(self):
        self._stop.set()
        self.status.set("stopping after current point...")
        self.stop_btn.configure(state="disabled")

    # --------------------------------------------------------------- worker
    def _run_worker(self, s: dict, points):
        from .instruments import make_source, make_dmm, make_scope, make_pmu
        from .virtualbench import VirtualBench

        bench = VirtualBench() if s["simulate"] else None
        use_scope = s["use_scope"] and (s["simulate"] or s["scope_ip"])
        opened = []
        try:
            source = make_source(s["simulate"], port=s["source_port"],
                                 baud=s["source_baud"], bench=bench)
            dmm = make_dmm(s["simulate"], port=s["dmm_port"], baud=s["dmm_baud"],
                           parity=s["dmm_parity"], bench=bench)
            scope = (make_scope(s["simulate"], ip=s["scope_ip"], channel=s["scope_ch"],
                                bench=bench) if use_scope else None)
            pmu = make_pmu(s["simulate"], port=s["pmu_port"], volts_per_count=s["vpc"],
                           bench=bench)
            for inst in (source, dmm, scope, pmu):
                if inst is not None:
                    inst.open()
                    opened.append(inst)
            self._q.put(("vpc", pmu.volts_per_count))

            class _NullScope:
                def read(self, navg=1):
                    return {}

            def on_progress(i, total, res):
                self._q.put(("point", i, total, res))

            run_sweep(source, dmm, scope or _NullScope(), pmu, points,
                      on_progress=on_progress, read_scope=scope is not None,
                      stop_event=self._stop)
            self._q.put(("done", pmu.volts_per_count))
        except Exception as exc:                          # noqa: BLE001
            self._q.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            for inst in reversed(opened):
                try:
                    inst.close()
                except Exception:                         # noqa: BLE001
                    pass

    # --------------------------------------------------- setup-tab hooks
    def register_test_label(self, key: str, label):
        """The setup guide registers its per-instrument status Label here."""
        self._test_status[key] = label

    def start_instrument_test(self, key: str):
        """Open one instrument with the current settings and read its identity."""
        lbl = self._test_status.get(key)
        if lbl is not None:
            lbl.configure(text="testing…", foreground="#666")
        threading.Thread(target=self._test_worker, args=(key,), daemon=True).start()

    def _test_worker(self, key: str):
        from .instruments import make_source, make_dmm, make_scope, make_pmu
        from .virtualbench import VirtualBench
        sim = self.simulate.get()
        bench = VirtualBench() if sim else None
        try:
            if key == "source":
                inst = make_source(sim, port=self.source_port.get(),
                                   baud=int(self.source_baud.get()), bench=bench)
            elif key == "dmm":
                inst = make_dmm(sim, port=self.dmm_port.get(),
                                baud=int(self.dmm_baud.get()),
                                parity=self.dmm_parity.get(), bench=bench)
            elif key == "scope":
                inst = make_scope(sim, ip=self.scope_ip.get(),
                                  channel=int(self.scope_ch.get()), bench=bench)
            elif key == "pmu":
                inst = make_pmu(sim, port=self.pmu_port.get(), bench=bench)
            else:
                return
            inst.open()
            try:
                idn = inst.identify()
            finally:
                inst.close()
            self._q.put(("test", key, True, idn))
        except Exception as exc:                              # noqa: BLE001
            self._q.put(("test", key, False, f"{type(exc).__name__}: {exc}"))

    # ------------------------------------------------------------ queue pump
    def _drain_queue(self):
        try:
            while True:
                msg = self._q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "test":
            _, key, ok, text = msg
            lbl = self._test_status.get(key)
            if lbl is not None:
                short = text if len(text) <= 48 else text[:45] + "…"
                lbl.configure(text=("✓ " if ok else "✗ ") + short,
                              foreground="#0a6" if ok else "#b00020")
        elif kind == "vpc":
            self._current_vpc = msg[1]
        elif kind == "point":
            _, i, total, res = msg
            self._add_row(res)
            self.progress.configure(value=i)
            self.status.set(f"point {i}/{total}: {res.point.label}")
        elif kind == "done":
            self._finish(msg[1])
        elif kind == "error":
            self._finish_error(msg[1])

    def _add_row(self, res):
        row = build_row(res)
        self._rows.append(row)
        p = res.pmu or {}
        s = res.scope or {}
        fmt = lambda v, sp=".4f": (format(v, sp) if v is not None else "--")
        locked = bool(p.get("synced")) and p.get("n", 0) > 0
        vals = (row["label"], fmt(res.dmm_vrms), fmt(s.get("vrms")),
                fmt(p.get("vmag")), fmt(p.get("freq")), fmt(p.get("tve"), ".3f"),
                "LOCK" if locked else "----", res.note)
        tag = "" if locked and not res.note else ("nolock" if not locked else "bad")
        self.tree.insert("", "end", values=vals, tags=(tag,) if tag else ())
        self.tree.see(self.tree.get_children()[-1])
        self._redraw_plot()

    # --------------------------------------------------------------- plotting
    def _reset_plot(self):
        self.ax.clear()
        self.ax2.clear()
        self.ax.set_title("run a sweep to see errors")
        self.ax.grid(True, alpha=0.3)
        if hasattr(self, "canvas"):
            self.canvas.draw_idle()

    def _redraw_plot(self):
        rows = self._rows
        if not rows:
            return
        spec = _PLOT_SERIES[self._kind]
        xkey, xlabel = spec["x"]
        self.ax.clear()
        self.ax2.clear()
        x = [r[xkey] for r in rows]
        for label, key in spec["left"]:
            xs = [xi for xi, r in zip(x, rows) if r[key] is not None]
            ys = [r[key] for r in rows if r[key] is not None]
            if xs:
                self.ax.plot(xs, ys, "o-", label=label)
        self.ax.axhline(0, color="k", lw=0.5)
        self.ax.set_xlabel(xlabel)
        self.ax.set_ylabel("error")
        self.ax.grid(True, alpha=0.3)
        # TVE on the right axis with the M-class 1% line.
        xs = [xi for xi, r in zip(x, rows) if r["pmu_tve_pct"] is not None]
        ys = [r["pmu_tve_pct"] for r in rows if r["pmu_tve_pct"] is not None]
        if xs:
            self.ax2.plot(xs, ys, "s--", color="tab:red", alpha=0.6, label="TVE %")
            self.ax2.axhline(1.0, color="tab:red", lw=0.5, ls=":")
        self.ax2.set_ylabel("TVE %")
        h1, l1 = self.ax.get_legend_handles_labels()
        h2, l2 = self.ax2.get_legend_handles_labels()
        if h1 or h2:
            self.ax.legend(h1 + h2, l1 + l2, loc="best", fontsize=8)
        self.ax.set_title(f"{self._kind} sweep")
        self.fig.tight_layout()
        self.canvas.draw_idle()

    # ----------------------------------------------------------------- finish
    def _finish(self, current_vpc):
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if not self._rows:
            self.status.set("stopped (no points collected)")
            return
        summ = summarize(self._kind, self._rows, current_vpc=current_vpc)
        self._set_summary(summ.text or "(no valid points)")
        if summ.recommended_vpc is not None:
            self._reco_value = summ.recommended_vpc
            self.reco.configure(text=f"recommended volts_per_count = "
                                     f"{summ.recommended_vpc:.8g}")
            self.apply_btn.configure(state="normal")
        # Persist CSV + PNG alongside the CLI outputs.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = f"{self._kind}_{'sim' if self.simulate.get() else 'hw'}_gui"
        out = Path("results")
        csv_path = write_csv(self._rows, out / f"validate_{tag}_{stamp}.csv")
        png = save_plot(self._kind, self._rows, out / f"validate_{tag}_{stamp}.png")
        self.status.set(f"done — wrote {csv_path}"
                        + (f" and {png.name}" if png else ""))

    def _finish_error(self, text):
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status.set("error")
        messagebox.showerror("Run failed", text)

    def _apply_reco(self):
        if self._reco_value is not None:
            self.vpc.set(f"{self._reco_value:.8g}")
            self.status.set(f"volts_per_count set to {self._reco_value:.8g} — "
                            f"re-run to verify it closes the error")

    def _set_summary(self, text):
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def _on_close(self):
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        self.root.destroy()


def main(argv=None) -> int:
    import sys
    if argv is None:
        argv = sys.argv[1:]
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")     # native on Windows; falls back below
    except tk.TclError:
        pass
    app = ValidationGui(root)
    if "--self-test" in argv:              # construct-only smoke check
        root.update_idletasks()
        root.destroy()
        print("gui self-test OK")
        return 0
    if "--demo" in argv:                   # auto-run a short simulate sweep
        app.simulate.set(True)
        app.plan_name.set("amplitude")
        app._on_plan_change()
        app.points_text.set("1,3,5,8")
        app.settle.set("1.2")
        root.after(700, app._on_run)
    if "--demo-setup" in argv:             # run the four connection tests (sim)
        for i, key in enumerate(("source", "dmm", "scope", "pmu")):
            root.after(500 + i * 150, lambda k=key: app.start_instrument_test(k))
    if "--demo-harm" in argv:              # analyze a square-wave stimulus (sim)
        app.simulate.set(True)
        app._nb.select(2)
        app._harm_tab.shape.set("Square")
        root.after(700, app._harm_tab._on_run)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
