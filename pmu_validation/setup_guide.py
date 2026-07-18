"""Interactive bench setup guide (a tab in the validation GUI).

Rebuilt for clarity: the hookup is presented as a plain-language, numbered
sequence in three phases —

    A. Signal wiring   — get the test signal to one shared node
    B. Control cables  — connect each instrument to the PC
    C. Configure & verify

— matched by a two-panel diagram (signal path on top, control cables below), so
each picture stays uncluttered. A "Test → *IDN?" button per instrument (in
phase C) opens it with the settings from the Instruments bar and reports its
identity, so each link can be confirmed before a run.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

SIGNAL = "#1565c0"      # signal-path wires (thick)
CONTROL = "#8a8f98"     # control / data links (thin dashed)
NODE_FILL = "#fff3cd"
SIG_FILL = "#e7f0fb"
CTL_FILL = "#eef1f4"

# Phases: (letter, title, hint, [(step_text, sub_note_or_None), ...])
PHASES = [
    ("A", "Signal wiring — carry the test signal to ONE node",
     "the thick blue lines in the top picture", [
        ("Make an injection node (a BNC tee or small terminal block).",
         "every instrument connects to this ONE point"),
        ("3325B front OUTPUT (BNC)  →  injection node.", "this is the signal under test"),
        ("Scope Channel 1 (BNC)  →  injection node.", None),
        ("DMM front HI and LO terminals  →  injection node.",
         "HI = signal, LO = ground; press the meter's FRONT button"),
        ("PMU analog input  →  injection node.", None),
        ("Tie every instrument's ground together at the node (one common ground).", None),
        ("Keep the 3325B output OFF until all the wiring above is done.", None),
    ]),
    ("B", "Control cables — connect each instrument to the PC",
     "the dashed lines in the bottom picture", [
        ("3325B rear RS-232  →  a PC COM port  (NULL-MODEM cable).", None),
        ("DMM rear RS-232  →  a PC COM port  (NULL-MODEM cable).", None),
        ("Scope LAN port  →  your network, or straight to the PC.", None),
        ("PMU user USB (CN13)  →  the PC.", "CN13 is the user USB, NOT the ST-LINK USB"),
    ]),
    ("C", "Configure & verify", None, [
        ("3325B: rear DIP switches set baud/parity (factory = 300 baud, 7E1).", None),
        ("DMM front panel: I/O menu → RS-232, set BAUD + PARITY, LANGUAGE = SCPI.", None),
        ("Scope: note its IP (Utilities → I/O → LAN); make sure its firewall is off.", None),
        ("PMU: set the AD7606C straps — PAR/SER SEL = high, OS2 = OS1 = OS0 = 1.", None),
        ("In the Instruments bar at the top, type each port / IP to match.", None),
        ("Click each Test button below — you want a green ✓ from all four.", None),
    ]),
]

# Instruments to show a Test button for, in phase C.
TESTABLE = [("source", "3325B"), ("dmm", "DMM"), ("scope", "Scope"), ("pmu", "PMU")]

CAUTIONS = [
    "Both RS-232 links (3325B and DMM) need a NULL-MODEM / crossover cable — a "
    "straight-through cable will not talk.",
    "Don't drive the 3325B over HP-IB and RS-232 at the same time.",
    "Don't use the DMM's RS-232 if it's set to output pass/fail on pins 1 & 9 "
    "(the manual warns it can damage the port).",
    "Signal level: stay within the 3325B's output range AND your PMU front-end "
    "input range.",
    "A GPS antenna is only needed for phase/TVE later — not for magnitude/frequency.",
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
        ttk.Label(left, text="How it's wired", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(left, width=560, height=470, background="white",
                                highlightthickness=1, highlightbackground="#ccc")
        self.canvas.pack()
        self._draw_diagram()

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=(3, 6), pady=6)
        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="Follow these steps in order",
                  font=("Segoe UI", 10, "bold")).pack(side="left", anchor="w")
        self.progress_lbl = ttk.Label(head, text="", foreground="#555")
        self.progress_lbl.pack(side="right")
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
        # ---- Panel ① : signal wiring -------------------------------------
        c.create_text(12, 12, anchor="w", text="① Signal wiring — one shared node",
                      font=("Segoe UI", 9, "bold"), fill=SIGNAL)
        self._box(15, 92, 120, 44, "HP 3325B\nOUTPUT", SIG_FILL)
        self._box(232, 92, 86, 44, "INJECTION\nNODE", NODE_FILL, outline="#b8860b")
        self._box(400, 30, 150, 40, "Scope — CH1", SIG_FILL)
        self._box(400, 92, 150, 44, "DMM — HI / LO", SIG_FILL)
        self._box(400, 160, 150, 40, "PMU — analog in", SIG_FILL)
        self._link((135, 114), (232, 114), SIGNAL, 3, label="sine", ly=-8)
        self._link((318, 108), (400, 55), SIGNAL, 3)
        self._link((318, 114), (400, 114), SIGNAL, 3)
        self._link((318, 122), (400, 178), SIGNAL, 3)
        # ground under the node
        c.create_line(275, 136, 275, 150, fill="#444", width=2)
        for i, wg in enumerate((20, 13, 6)):
            c.create_line(275 - wg, 150 + i * 4, 275 + wg, 150 + i * 4, fill="#444", width=2)
        c.create_text(275, 170, text="common ground", fill="#444", font=("Segoe UI", 7))

        c.create_line(8, 218, 552, 218, fill="#ddd")

        # ---- Panel ② : control cables ------------------------------------
        c.create_text(12, 232, anchor="w", text="② Control cables — each instrument → PC",
                      font=("Segoe UI", 9, "bold"), fill="#555")
        self._box(232, 322, 86, 44, "PC / laptop", "#e8eef7")
        self._box(15, 300, 120, 40, "HP 3325B", CTL_FILL)
        self._box(15, 380, 120, 40, "HP 34401A", CTL_FILL)
        self._box(400, 300, 150, 40, "MSO8104A", CTL_FILL)
        self._box(400, 380, 150, 40, "micro-PMU", CTL_FILL)
        self._link((232, 338), (135, 320), CONTROL, 1, dash=(4, 3), label="RS-232*", ly=-7)
        self._link((232, 350), (135, 400), CONTROL, 1, dash=(4, 3), label="RS-232*", ly=8)
        self._link((318, 338), (400, 320), CONTROL, 1, dash=(4, 3), label="LAN", ly=-7)
        self._link((318, 350), (400, 400), CONTROL, 1, dash=(4, 3), label="USB", ly=8)
        c.create_text(12, 440, anchor="w", text="* null-modem (crossover) cable",
                      fill="#8a5a00", font=("Segoe UI", 8))

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

        # Heads-up box (kept out of the numbered flow to reduce clutter).
        warn = ttk.LabelFrame(inner, text="  Heads-up  ")
        warn.pack(fill="x", expand=True, padx=4, pady=(6, 8))
        for caution in CAUTIONS:
            row = ttk.Frame(warn)
            row.pack(fill="x", padx=6, pady=1)
            ttk.Label(row, text="⚠", foreground="#b06a00").pack(side="left", anchor="n")
            ttk.Label(row, text=caution, foreground="#8a5a00", wraplength=430,
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
