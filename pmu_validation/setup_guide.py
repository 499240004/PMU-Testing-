"""Interactive bench setup guide (a tab in the validation GUI).

Wired for the **as-designed PMU** (schematic 01-0001 Rev I): the board is a
mains-connected, self-powered instrument. Its IEC/L-N input BOTH powers the
board (isolated AC/DC modules) AND is the measured quantity (via a galvanically
isolated LEM LV25-P transducer). So the stimulus is a **Variac at mains level**,
not the function generator — a 10 Vpp generator can neither reach ~120 VAC nor
power the board.

This guide covers the **full-chain (regime A)** hookup:

    Variac → PMU IEC/L-N input   (powers + is measured, incl. the transducer)
    DMM across L-N               (true-RMS Vac + line frequency = the reference)
    Scope across L-N             (optional; DIFFERENTIAL/HV probe + isolation)

⚠ A Variac is a NON-ISOLATED autotransformer — its output is at mains potential.
The safety notes below are not optional.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

MAINS = "#c62828"       # mains-level signal path (thick red = HIGH VOLTAGE)
CONTROL = "#8a8f98"     # control / data links (thin dashed)
NODE_FILL = "#fff3cd"
SIG_FILL = "#fde8e8"
CTL_FILL = "#eef1f4"

# Phases: (letter, title, hint, [(step_text, sub_note_or_None), ...])
PHASES = [
    ("A", "Mains input — the Variac powers AND is measured by the PMU",
     "the thick red lines in the top picture (MAINS LEVEL)", [
        ("Variac to ZERO, then plug the Variac into wall mains.", None),
        ("Variac output  →  PMU IEC (C14) inlet, via a standard IEC power cord.",
         "this both powers the board and is the measured signal"),
        ("Bring the Variac slowly up to ~120 VAC; the PMU powers up and its USB "
         "port enumerates.", "input fuse is 250 mA; the MOV clamps above ~430 V"),
    ]),
    ("B", "Reference taps across the Variac output (L–N)",
     None, [
        ("DMM front HI / LO across the Variac output (HI = L, LO = N); set VAC.",
         "the 34401A also reads line FREQUENCY — your frequency reference"),
        ("(Optional) Scope across L–N with a DIFFERENTIAL or 10× HV probe, and an "
         "ISOLATION TRANSFORMER on the Variac.",
         "no differential/HV probe or isolation? skip the scope — the DMM is the reference"),
        ("GPS antenna → PMU, with a clear sky view.",
         "needed for UTC time-sync / phase / TVE; NOT needed for magnitude or frequency"),
    ]),
    ("C", "Control cables & verify", None, [
        ("PMU user USB (CN13)  →  PC.", "CN13 is the user USB, NOT the ST-LINK USB"),
        ("DMM rear RS-232  →  PC COM  (NULL-MODEM cable); front panel I/O menu: "
         "RS-232, set BAUD + PARITY, LANGUAGE = SCPI.", None),
        ("Enter the DMM port and PMU port in the Instruments bar; click each "
         "Test → *IDN? for a green ✓.", None),
    ]),
]

# Instruments with a Test button (the Variac isn't a controllable instrument).
TESTABLE = [("dmm", "DMM"), ("scope", "Scope"), ("pmu", "PMU")]

CAUTIONS = [
    "DANGER: a Variac is a NON-ISOLATED autotransformer — its output and the PMU "
    "L-N input sit at MAINS potential. Use an isolation transformer; treat all "
    "input wiring as live and lethal.",
    "Scope ground is earth-referenced — NEVER clip it to L or N. Use a "
    "differential or 10× HV probe (1 MΩ), never DC50. If unsure, skip the scope.",
    "The PMU's measurement side is isolated (LV25-P + isolated supplies), but the "
    "IEC / L-N INPUT terminals are at mains — the isolation is inside the board.",
    "Bring the Variac up slowly and watch the current; the input fuse is 250 mA.",
    "The 3325B is NOT used in this hookup: driving the PMU input needs mains-level "
    "voltage and power a function generator can't supply. Its low-level "
    "signal-injection mode (regime B) needs board access and is deferred.",
]


class SetupGuideFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._check_vars: list[tk.BooleanVar] = []
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self):
        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(6, 3), pady=6)
        ttk.Label(left, text="How it's wired (Variac / full-chain)",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(left, width=560, height=470, background="white",
                                highlightthickness=1, highlightbackground="#ccc")
        self.canvas.pack()

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=(3, 6), pady=6)
        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="Follow these steps in order",
                  font=("Segoe UI", 10, "bold")).pack(side="left", anchor="w")
        self.progress_lbl = ttk.Label(head, text="", foreground="#555")
        self.progress_lbl.pack(side="right")
        self._draw_diagram()
        self._build_checklist(right)

    # --------------------------------------------------------------- diagram
    def _box(self, x, y, w, h, title, fill, outline="#5b6b7b"):
        self.canvas.create_rectangle(x, y, x + w, y + h, fill=fill, outline=outline, width=2)
        self.canvas.create_text(x + w / 2, y + h / 2, text=title, justify="center",
                                 font=("Segoe UI", 9, "bold"))

    def _link(self, a, b, color, width, dash=None, label=None, lx=0, ly=0):
        self.canvas.create_line(a[0], a[1], b[0], b[1], fill=color, width=width,
                                dash=dash, arrow="last", arrowshape=(9, 11, 4))
        if label:
            self.canvas.create_text((a[0] + b[0]) / 2 + lx, (a[1] + b[1]) / 2 + ly,
                                    text=label, fill=color, font=("Segoe UI", 8))

    def _draw_diagram(self):
        c = self.canvas
        # ---- Panel ① : mains input (Variac powers + is measured) ----------
        c.create_text(12, 12, anchor="w",
                      text="① Mains input — Variac powers AND is measured",
                      font=("Segoe UI", 9, "bold"), fill=MAINS)
        self._box(15, 40, 110, 30, "wall mains\n120 VAC", "#eeeeee")
        self._box(15, 92, 110, 48, "VARIAC\n0–140 VAC", "#eeeeee")
        self._box(232, 90, 118, 54, "PMU\nIEC / L–N input", NODE_FILL, outline="#b8860b")
        self._box(415, 38, 145, 46, "DMM across L–N\n(Vac + freq ref)", SIG_FILL)
        self._box(415, 150, 145, 46, "Scope across L–N\n(diff probe · optional)", SIG_FILL)
        c.create_line(70, 70, 70, 92, fill="#999", width=1, arrow="last")
        self._link((125, 116), (232, 116), MAINS, 3, label="IEC cord · 120 VAC", ly=-8)
        self._link((350, 108), (415, 61), MAINS, 3, label="L–N", lx=6, ly=-6)
        self._link((350, 126), (415, 173), MAINS, 3, label="L–N", lx=6, ly=8)
        c.create_text(288, 165, text="⚡ MAINS LEVEL — non-isolated", fill=MAINS,
                      font=("Segoe UI", 8, "bold"))

        c.create_line(8, 205, 552, 205, fill="#ddd")

        # ---- Panel ② : control + timing ----------------------------------
        c.create_text(12, 220, anchor="w", text="② Control cables + timing",
                      font=("Segoe UI", 9, "bold"), fill="#555")
        self._box(250, 300, 96, 44, "PC / laptop", "#e8eef7")
        self._box(20, 285, 112, 38, "micro-PMU", CTL_FILL)
        self._box(150, 285, 120, 38, "GPS antenna", CTL_FILL)
        self._box(20, 360, 112, 38, "HP 34401A", CTL_FILL)
        self._box(415, 300, 145, 40, "MSO8104A (opt)", CTL_FILL)
        self._link((150, 304), (132, 304), CONTROL, 1, label="1PPS/NMEA", ly=-8)
        self._link((132, 308), (250, 318), CONTROL, 1, dash=(4, 3), label="USB", ly=-7)
        self._link((132, 379), (250, 332), CONTROL, 1, dash=(4, 3), label="RS-232*", ly=8)
        self._link((415, 320), (346, 322), CONTROL, 1, dash=(4, 3), label="LAN", ly=-7)
        c.create_text(12, 415, anchor="w", text="* null-modem (crossover) cable",
                      fill="#8a5a00", font=("Segoe UI", 8))
        c.create_text(12, 435, anchor="w",
                      text="Variac is the stimulus — not a PC-controlled instrument.",
                      fill="#555", font=("Segoe UI", 8, "italic"))

    # -------------------------------------------------------------- checklist
    def _build_checklist(self, parent):
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True, pady=(4, 0))
        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        canvas.bind("<Enter>", lambda e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        for letter, title, hint, steps in PHASES:
            sec = ttk.LabelFrame(inner, text=f"  {letter} · {title}  ")
            sec.pack(fill="x", expand=True, padx=4, pady=5)
            if hint:
                ttk.Label(sec, text=f"({hint})", foreground="#777",
                          font=("Segoe UI", 8, "italic")).pack(anchor="w", padx=8, pady=(2, 0))
            for n, (text, note) in enumerate(steps, 1):
                var = tk.BooleanVar(value=False)
                self._check_vars.append(var)
                ttk.Checkbutton(sec, text=f"{letter}{n}.  {text}", variable=var,
                                command=self._update_progress).pack(anchor="w", padx=6, pady=1)
                if note:
                    ttk.Label(sec, text=f"       ↳ {note}", foreground="#777",
                              font=("Segoe UI", 8)).pack(anchor="w", padx=6)
            if letter == "C":
                self._build_test_row(sec)

        warn = ttk.LabelFrame(inner, text="  ⚠ Safety — read before powering up  ")
        warn.pack(fill="x", expand=True, padx=4, pady=(6, 8))
        for caution in CAUTIONS:
            row = ttk.Frame(warn)
            row.pack(fill="x", padx=6, pady=1)
            ttk.Label(row, text="⚠", foreground="#b00020").pack(side="left", anchor="n")
            ttk.Label(row, text=caution, foreground="#8a2a2a", wraplength=430,
                      justify="left").pack(side="left", fill="x", expand=True)
        self._update_progress()

    def _build_test_row(self, parent):
        box = ttk.Frame(parent)
        box.pack(fill="x", padx=6, pady=(4, 6))
        for key, short in TESTABLE:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=1)
            ttk.Button(row, text=f"Test {short} → *IDN?", width=18,
                       command=lambda k=key: self.app.start_instrument_test(k)
                       ).pack(side="left")
            lbl = ttk.Label(row, text="not tested", foreground="#888")
            lbl.pack(side="left", padx=8)
            self.app.register_test_label(key, lbl)

    def _update_progress(self):
        done = sum(1 for v in self._check_vars if v.get())
        self.progress_lbl.configure(text=f"{done}/{len(self._check_vars)} steps done")
