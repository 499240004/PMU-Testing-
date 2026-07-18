"""Interactive bench setup guide (a tab in the validation GUI).

Shows how to physically wire the four instruments together for a validation run:

* a **wiring diagram** (Canvas) of the signal path and the control/data links,
* a **step-by-step checklist** you tick off as you make each connection, with
  the real cautions from each instrument app's README, and
* a **"Test → *IDN?"** button per instrument that opens it with the connection
  settings from the Instruments bar and reads its identity, so you can confirm
  each hookup as you go (in Simulate mode these return the simulated identity).

The frame reads the shared connection variables off the parent app and drives
tests through ``app.start_instrument_test(name)``; results arrive back as
``("test", name, ok, msg)`` messages on the app's queue.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

SIGNAL = "#1565c0"      # signal-path wires (thick)
CONTROL = "#8a8f98"     # control / data links (thin dashed)
NODE_FILL = "#fff3cd"
BOX_FILL = "#eef2f7"

# Each instrument section: (key, title, [steps], [cautions]).
# key is None for non-testable sections (the shared node / grounding).
SECTIONS = [
    (None, "Common injection node & grounding", [
        "Pick ONE tie point (a small terminal block or BNC tee) as the injection node.",
        "The 3325B output drives this node; the DMM, scope and PMU all tap it in PARALLEL.",
        "Bond all instrument grounds to this one node (single-point ground) to avoid ground loops.",
        "Keep the 3325B output OFF / at 0 V while wiring; raise it only once everything is connected.",
    ], [
        "Confirm the injection level suits your PMU front end: the 3325B does 10 Vpp "
        "(40 Vpp with HV option), and the front end maps its full input to the ADC's "
        "range. Inject where your divider/transducer expects it, and stay in range.",
    ]),
    ("source", "HP 3325B — function generator (stimulus)", [
        "Main OUTPUT (front BNC) → injection node (this is the signal under test).",
        "Rear RS-232 (DB-25) → PC COM port via a NULL-MODEM (crossover) cable.",
        "Rear DIP switches set baud/parity; match them to the Instruments bar (factory = 300/7E1).",
    ], [
        "Do NOT drive the 3325B over HP-IB and RS-232 at the same time.",
        "4800 baud (switches 3&4 down) is the max and makes the link snappier.",
    ]),
    ("dmm", "HP 34401A — bench DMM (amplitude reference)", [
        "Front HI / LO terminals across the injection node; press the FRONT button to select them.",
        "Rear RS-232 → PC COM via a NULL-MODEM cable (hardware DTR/DSR handshake, 2 stop bits).",
        "Front panel I/O menu: INTERFACE = RS-232, set BAUD + PARITY, LANGUAGE = SCPI.",
    ], [
        "Do NOT use RS-232 if the meter is set to output pass/fail on pins 1 & 9 — "
        "per the manual it can damage the RS-232 circuitry.",
        "No handshake lines on your cable? Drop to a slower baud and disable handshake.",
    ]),
    ("scope", "MSO8104A — oscilloscope (waveform / frequency reference)", [
        "CH1 (BNC/probe) across the injection node; set coupling (DC50 or 1 MΩ) and a sane V/div.",
        "Scope LAN port → your network / PC (note its IP: Utilities → I/O → LAN).",
        "Enter the scope IP in the Instruments bar; 'Test' confirms VISA can reach it.",
    ], [
        "The scope's Windows Firewall must be OFF for VISA to connect "
        "(run scope-setup\\'Enable Remote Access.bat' on the scope, ideally at startup).",
    ]),
    ("pmu", "micro-PMU — device under test", [
        "Analog front-end input ← injection node (same node as DMM/scope).",
        "User USB (CN13) → PC. This enumerates as the CDC COM port — NOT the ST-LINK VCP.",
        "Board straps first: AD7606C PAR/SER SEL = high (serial), OS2=OS1=OS0 = 1 (software mode).",
        "GPS antenna with sky view is OPTIONAL for magnitude+frequency; REQUIRED for phase/TVE.",
    ], [
        "'auto' finds the board by USB VID:PID 0483:5740. If STATUS shows ID_OK=0 the "
        "AD7606C link is bad — recheck the straps and SPI wiring.",
    ]),
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
        ttk.Label(left, text="Bench wiring", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(left, width=560, height=470, background="white",
                                highlightthickness=1, highlightbackground="#ccc")
        self.canvas.pack()
        self._draw_diagram()
        self._legend(left)

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=(3, 6), pady=6)
        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="Connection checklist",
                  font=("Segoe UI", 10, "bold")).pack(side="left", anchor="w")
        self.progress_lbl = ttk.Label(head, text="", foreground="#555")
        self.progress_lbl.pack(side="right")
        self._build_checklist(right)

    def _draw_diagram(self):
        c = self.canvas

        def box(x, y, w, h, title, fill=BOX_FILL, outline="#5b6b7b"):
            c.create_rectangle(x, y, x + w, y + h, fill=fill, outline=outline, width=2)
            c.create_text(x + w / 2, y + h / 2, text=title, justify="center",
                          font=("Segoe UI", 9, "bold"))
            return (x + w / 2, y + h / 2, x, y, w, h)

        pc = box(210, 12, 150, 44, "PC / laptop", fill="#e8eef7")
        gen = box(15, 110, 155, 54, "HP 3325B\nfunction generator")
        dmm = box(15, 250, 155, 54, "HP 34401A\nbench DMM")
        node = box(255, 210, 95, 52, "INJECTION\nNODE", fill=NODE_FILL, outline="#b8860b")
        scope = box(390, 110, 155, 54, "MSO8104A\noscilloscope")
        pmu = box(390, 250, 155, 54, "micro-PMU\n(device under test)")

        def line(a, b, color, width, dash=None, label=None, lx=0, ly=0):
            c.create_line(a[0], a[1], b[0], b[1], fill=color, width=width,
                          dash=dash, arrow="last", arrowshape=(9, 11, 4))
            if label:
                mx, my = (a[0] + b[0]) / 2 + lx, (a[1] + b[1]) / 2 + ly
                c.create_text(mx, my, text=label, fill=color, font=("Segoe UI", 8))

        # Signal path (thick blue): 3325B -> node -> {DMM, scope, PMU}
        line((170, 137), (255, 226), SIGNAL, 3, label="sine", ly=-8)
        line((255, 246), (170, 277), SIGNAL, 3, label="HI / LO", ly=-8)
        line((350, 226), (390, 137), SIGNAL, 3, label="CH1", lx=6, ly=-6)
        line((350, 246), (390, 277), SIGNAL, 3, label="analog in", ly=-8)

        # Ground symbol under the node.
        c.create_line(302, 262, 302, 284, fill="#444", width=2)
        for i, wgnd in enumerate((22, 14, 6)):
            c.create_line(302 - wgnd, 284 + i * 4, 302 + wgnd, 284 + i * 4,
                          fill="#444", width=2)
        c.create_text(302, 302, text="single-point ground", fill="#444",
                      font=("Segoe UI", 7))

        # Control / data links (thin gray dashed) to the PC.
        line((92, 110), (250, 52), CONTROL, 1, dash=(4, 3), label="RS-232*", ly=-6)
        line((92, 250), (232, 56), CONTROL, 1, dash=(4, 3), label="RS-232*", lx=-24)
        line((468, 110), (330, 52), CONTROL, 1, dash=(4, 3), label="LAN", ly=-6)
        line((468, 250), (346, 56), CONTROL, 1, dash=(4, 3), label="USB CDC", lx=26)

    def _legend(self, parent):
        f = ttk.Frame(parent)
        f.pack(anchor="w", pady=(4, 0))
        cv = tk.Canvas(f, width=360, height=22, highlightthickness=0)
        cv.pack()
        cv.create_line(6, 8, 34, 8, fill=SIGNAL, width=3)
        cv.create_text(40, 8, anchor="w", text="signal path", font=("Segoe UI", 8))
        cv.create_line(130, 8, 158, 8, fill=CONTROL, width=1, dash=(4, 3))
        cv.create_text(164, 8, anchor="w", text="control / data", font=("Segoe UI", 8))
        cv.create_text(270, 8, anchor="w", text="* null-modem cable",
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

        for key, title, steps, cautions in SECTIONS:
            sec = ttk.LabelFrame(inner, text=title)
            sec.pack(fill="x", expand=True, padx=4, pady=4)
            for step in steps:
                var = tk.BooleanVar(value=False)
                self._check_vars.append(var)
                ttk.Checkbutton(sec, text=step, variable=var, command=self._update_progress
                                ).pack(anchor="w", padx=6, pady=1)
            for caution in cautions:
                row = ttk.Frame(sec)
                row.pack(fill="x", padx=6, pady=1)
                ttk.Label(row, text="⚠", foreground="#b06a00").pack(side="left", anchor="n")
                ttk.Label(row, text=caution, foreground="#8a5a00", wraplength=420,
                          justify="left").pack(side="left", fill="x", expand=True)
            if key is not None:
                bar = ttk.Frame(sec)
                bar.pack(fill="x", padx=6, pady=(2, 4))
                ttk.Button(bar, text="Test → *IDN?",
                           command=lambda k=key: self.app.start_instrument_test(k)
                           ).pack(side="left")
                lbl = ttk.Label(bar, text="not tested", foreground="#888")
                lbl.pack(side="left", padx=8)
                self.app.register_test_label(key, lbl)
        self._update_progress()

    def _update_progress(self):
        done = sum(1 for v in self._check_vars if v.get())
        self.progress_lbl.configure(text=f"{done}/{len(self._check_vars)} steps checked")
