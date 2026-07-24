"""Calibration tab: auto-calibrate the PMU's volts_per_count against the DMM.

Press **Auto-calibrate** and the tab captures N points (default 20) back-to-back
at the current line voltage, averages the DMM/PMU ratio, and computes

    new_vpc = current_vpc * mean(DMM_vrms / PMU_vmag)

then **auto-updates and applies it live**:
  * saves it to ``pmu_validation/calibration.json`` (every future PMU open() loads it),
  * pushes it into the running PMU's config -- the engine reads volts_per_count on
    each ADC block, so the new scale takes effect immediately (no board flashing;
    the firmware streams raw counts and holds no calibration constant), and
  * pushes it into the shared 'volts/count' field so the rest of the GUI uses it.

Finally it re-reads a few points at the new scale to verify the PMU now matches
the DMM. Runs on a worker thread; a stop Event ends the averaging early.
"""
from __future__ import annotations

import queue
import statistics
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import calibration as cal
from .sequencer import capture_manual

CCOLS = [("n", "#", 40), ("dmm", "DMM Vrms", 100), ("pmu", "PMU Vrms", 100),
         ("ratio", "DMM/PMU", 90), ("corr", "→ vpc", 110)]


class CalibrationFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_vpc: float | None = None   # last successful result (for board write)
        self._last_sim = False
        self._build_vars()
        self._build()
        self._refresh_current()
        self.after(120, self._drain)

    def _build_vars(self):
        self.points = tk.StringVar(value="20")
        self.settle = tk.StringVar(value="0.2")
        self.sim_line = tk.StringVar(value="120")
        self.status = tk.StringVar(value="Set the Variac to your operating point, "
                                          "then Auto-calibrate.")
        self.current = tk.StringVar(value="")

    # ---------------------------------------------------------------- layout
    def _build(self):
        top = ttk.LabelFrame(self, text="Auto-calibrate volts_per_count (DMM = reference)")
        top.pack(fill="x", padx=8, pady=6)

        def lab(t, c):
            ttk.Label(top, text=t).grid(row=0, column=c, sticky="e", padx=(8, 2), pady=6)

        lab("points to average", 0)
        ttk.Entry(top, textvariable=self.points, width=6).grid(row=0, column=1, sticky="w")
        lab("settle (s)", 2)
        ttk.Entry(top, textvariable=self.settle, width=6).grid(row=0, column=3, sticky="w")
        self.run_btn = ttk.Button(top, text="▶ Auto-calibrate", command=self._on_run)
        self.run_btn.grid(row=0, column=4, padx=(12, 4))
        self.stop_btn = ttk.Button(top, text="■ Stop", command=self._on_stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=5, padx=2)
        # Provision the result into the board's flash (sector 7) over the
        # Nucleo's ST-LINK; enabled after a successful hardware calibration.
        self.write_btn = ttk.Button(top, text="⚡ Write to board",
                                    command=self._on_write_board, state="disabled")
        self.write_btn.grid(row=0, column=8, padx=(12, 4))

        self._sim_lbl = ttk.Label(top, text="Sim line Vrms:")
        self._sim_lbl.grid(row=0, column=6, sticky="e", padx=(16, 2))
        self._sim_entry = ttk.Entry(top, textvariable=self.sim_line, width=8)
        self._sim_entry.grid(row=0, column=7, sticky="w")

        self.progress = ttk.Progressbar(top, mode="determinate", length=220)
        self.progress.grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(top, textvariable=self.current, foreground="#0a6",
                  font=("Consolas", 9)).grid(row=1, column=4, columnspan=4,
                                             sticky="w", padx=8, pady=(0, 4))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        cols = [c[0] for c in CCOLS]
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=12)
        for key, title, w in CCOLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=w, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        ttk.Scrollbar(body, orient="vertical", command=self.tree.yview).pack(
            side="right", fill="y")

        self.summary = ttk.Label(self, text="", font=("Consolas", 10, "bold"))
        self.summary.pack(anchor="w", padx=10, pady=(2, 0))
        ttk.Label(self, textvariable=self.status, foreground="#555").pack(
            anchor="w", padx=10, pady=(0, 6))

    # ------------------------------------------------------------- current cal
    def _refresh_current(self):
        meta = cal.load_meta()
        if meta and meta.get("volts_per_count"):
            when = meta.get("calibrated_local", "?")
            ref = meta.get("dmm_ref_vrms")
            refs = f", ref {ref:.2f} Vrms" if isinstance(ref, (int, float)) else ""
            self.current.set(f"stored: volts_per_count={meta['volts_per_count']:.8g} "
                             f"({when}{refs})")
        else:
            self.current.set("stored: none yet (PMU uses the firmware default)")

    # --------------------------------------------------------------- controls
    def _on_run(self):
        if self._worker and self._worker.is_alive():
            return
        try:
            n = int(self.points.get()); settle = float(self.settle.get())
            if n < 1:
                raise ValueError("points must be >= 1")
        except ValueError as exc:
            messagebox.showerror("Bad input", str(exc)); return
        self.tree.delete(*self.tree.get_children())
        self.summary.configure(text="")
        self.progress.configure(maximum=n, value=0)
        s = {
            "simulate": self.app.simulate.get(),
            "dmm_port": self.app.dmm_port.get(), "dmm_baud": int(self.app.dmm_baud.get()),
            "dmm_parity": self.app.dmm_parity.get(),
            "pmu_port": self.app.pmu_port.get(),
            "vpc": float(self.app.vpc.get()) if self.app.vpc.get().strip() else None,
            "n": n, "settle": settle,
            "sim_line": float(self.sim_line.get() or 120),
        }
        self._stop.clear()
        self._last_vpc = None
        self._last_sim = s["simulate"]
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.write_btn.configure(state="disabled")
        self.status.set(f"averaging {n} points…")
        self._worker = threading.Thread(target=self._calibrate, args=(s,), daemon=True)
        self._worker.start()

    def _on_stop(self):
        self._stop.set()
        self.stop_btn.configure(state="disabled")
        self.status.set("stopping — will average the points captured so far…")

    def _on_write_board(self):
        """Provision the last calibration into the board's flash (sector 7)."""
        if self._last_vpc is None:
            return
        vpc = self._last_vpc
        if not messagebox.askokcancel(
                "Write calibration to board",
                f"Write volts_per_count = {vpc:.8g} into the PMU's flash "
                f"(sector 7) via the on-board ST-LINK?\n\n"
                f"The board resets afterwards, so any live stream (monitor, "
                f"waveform) will drop for a few seconds and reconnect."):
            return
        self.write_btn.configure(state="disabled")
        self.status.set("writing calibration to board via ST-LINK…")

        def work():
            from . import boardcal
            try:
                boardcal.write_to_board(vpc)
                self._q.put(("board_ok", vpc))
            except Exception as exc:                      # noqa: BLE001
                self._q.put(("board_err", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    # ----------------------------------------------------------- worker thread
    def _calibrate(self, s: dict):
        from .instruments import make_dmm, make_pmu
        from .virtualbench import VirtualBench
        import time

        bench = VirtualBench() if s["simulate"] else None
        opened = []
        try:
            dmm = make_dmm(s["simulate"], port=s["dmm_port"], baud=s["dmm_baud"],
                           parity=s["dmm_parity"], bench=bench)
            pmu = make_pmu(s["simulate"], port=s["pmu_port"],
                           volts_per_count=s["vpc"], bench=bench)
            for inst in (dmm, pmu):
                inst.open(); opened.append(inst)
            vpc0 = pmu.volts_per_count
            if s["simulate"]:
                bench.set_signal(60.0, s["sim_line"], 0.0)
                pmu.arm(60.0, s["sim_line"], 0.0)
                time.sleep(2.0)

            ratios = []
            dmms = []
            for i in range(s["n"]):
                if self._stop.is_set():
                    break
                # Light per-point averaging -- the 20-point average does the
                # noise reduction, so keep each capture fast (~1-2 s).
                # require_sync=False: the vpc ratio is amplitude-only, and GPS
                # sync gates nothing about magnitude -- without it a benchtop
                # with no PPS can never calibrate.
                res = capture_manual(dmm, None, pmu, label="", read_scope=False,
                                     read_freq=False, dmm_navg=1, pmu_navg=3,
                                     require_sync=False)
                dmm_v = res.dmm_vrms
                pmu_v = (res.pmu or {}).get("vmag")
                if dmm_v and pmu_v:
                    ratio = dmm_v / pmu_v
                    ratios.append(ratio); dmms.append(dmm_v)
                    self._q.put(("point", len(ratios), dmm_v, pmu_v,
                                 ratio, vpc0 * ratio))
                else:
                    self._q.put(("skip", res.note or "no lock"))
                if s["settle"] and i < s["n"] - 1:
                    time.sleep(s["settle"])

            if not ratios:
                self._q.put(("error", "no valid points captured (PMU never locked?)"))
                return

            mean_ratio = statistics.fmean(ratios)
            new_vpc = vpc0 * mean_ratio
            spread = (statistics.pstdev(ratios) / mean_ratio * 100.0
                      if len(ratios) > 1 else 0.0)
            dmm_ref = statistics.fmean(dmms)

            # --- auto-update: persist + apply live -------------------------
            cal.save_calibration(new_vpc, dmm_ref_vrms=dmm_ref, n_points=len(ratios),
                                 spread_pct=spread, note="auto-calibration vs 34401A")
            pmu.cfg.volts_per_count = new_vpc     # engine picks it up next block

            # --- verify at the new scale -----------------------------------
            time.sleep(max(0.3, s["settle"]))
            checks = []
            for _ in range(min(5, s["n"])):
                if self._stop.is_set():
                    break
                res = capture_manual(dmm, None, pmu, label="", read_scope=False,
                                     read_freq=False, require_sync=False)
                dmm_v = res.dmm_vrms
                pmu_v = (res.pmu or {}).get("vmag")
                if dmm_v and pmu_v:
                    checks.append((pmu_v - dmm_v) / dmm_v * 100.0)
                time.sleep(0.1)
            resid = statistics.fmean(checks) if checks else float("nan")

            self._q.put(("result", {"vpc0": vpc0, "new_vpc": new_vpc,
                                     "mean_ratio": mean_ratio, "spread": spread,
                                     "dmm_ref": dmm_ref, "n": len(ratios),
                                     "resid": resid}))
        except Exception as exc:                              # noqa: BLE001
            self._q.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            for inst in reversed(opened):
                try:
                    inst.close()
                except Exception:                             # noqa: BLE001
                    pass
            self._q.put(("done", None))

    # ------------------------------------------------------------ queue pump
    def _drain(self):
        try:
            while True:
                self._handle(self._q.get_nowait())
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "point":
            _, n, dmm_v, pmu_v, ratio, corr = msg
            self.tree.insert("", "end", values=(
                n, f"{dmm_v:.4f}", f"{pmu_v:.4f}", f"{ratio:.5f}", f"{corr:.8g}"))
            self.tree.see(self.tree.get_children()[-1])
            self.progress.configure(value=n)
            self.status.set(f"captured {n} (DMM {dmm_v:.4f} Vrms)")
        elif kind == "skip":
            self.status.set(f"point skipped: {msg[1]}")
        elif kind == "result":
            self._apply_result(msg[1])
        elif kind == "error":
            messagebox.showerror("Calibration failed", msg[1])
            self.status.set("error")
        elif kind == "done":
            self.run_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
        elif kind == "board_ok":
            self.status.set(f"calibration {msg[1]:.8g} written to board flash — "
                            f"board is resetting, reconnect in a few seconds")
            messagebox.showinfo(
                "Board provisioned",
                f"volts_per_count = {msg[1]:.8g} written to flash sector 7 and "
                f"verified.\n\nThe board reset; once it re-enumerates, every "
                f"host that connects will pick this value up from STATUS "
                f"(shown as 'cal=…' in the PMU identify string).")
            self.write_btn.configure(state="normal")
        elif kind == "board_err":
            messagebox.showerror("Board write failed", msg[1])
            self.status.set("board write failed")
            self.write_btn.configure(state="normal")

    def _apply_result(self, r):
        # Push the new scale into the shared field so every other tab uses it.
        self.app.vpc.set(f"{r['new_vpc']:.8g}")
        self._refresh_current()
        self._last_vpc = r["new_vpc"]
        if not self._last_sim:      # provisioning needs real hardware (ST-LINK)
            self.write_btn.configure(state="normal")
        resid = r["resid"]
        resid_s = f"{resid:+.3f}%" if resid == resid else "n/a"
        self.summary.configure(
            text=f"volts_per_count {r['vpc0']:.8g} → {r['new_vpc']:.8g}  "
                 f"(×{r['mean_ratio']:.5f}, spread {r['spread']:.3f}% over {r['n']} pts)   "
                 f"post-cal PMU vs DMM: {resid_s}")
        self.status.set(f"calibrated, saved & applied live — PMU now within "
                        f"{resid_s} of the DMM")
        messagebox.showinfo(
            "Calibration applied",
            f"volts_per_count = {r['new_vpc']:.8g}\n"
            f"averaged over {r['n']} points (spread {r['spread']:.3f}%)\n"
            f"reference DMM {r['dmm_ref']:.4f} Vrms\n\n"
            f"Saved to calibration.json, applied to the live PMU, and set as the "
            f"session scale. Post-calibration PMU vs DMM: {resid_s}.")
