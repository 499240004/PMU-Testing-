# PMU Validation Bench

Automated validation for the **Elastic Energy micro-PMU** using the lab
instruments we already have driver software for. An HP 3325B function generator
provides the stimulus; the PMU's reported magnitude / frequency / TVE are
cross-checked against an **HP 34401A** bench DMM (the accurate amplitude
reference) and an **MSO8104A** oscilloscope (the waveform / frequency reference).

Everything runs **with no hardware** via a coordinated simulator, so the
framework can be developed and demonstrated before touching the bench.

## How it fits together

The four instrument apps live **unmodified** as git submodules under `apps/` —
each is still fully usable on its own for other projects. This repo imports only
their *driver* layers (never their GUIs) and adds an orchestrator on top.

| Role | App (submodule) | Driver used |
|---|---|---|
| Stimulus | `apps/hp3325` | `HP3325B` |
| Amplitude reference | `apps/hp34401` | `HP34401A` |
| Waveform / freq reference | `apps/scope` | `MSO8104A` |
| Device under test | `apps/power-brick` | `upmu` (`PmuEngine`) |

```
pmu_validation/
  _vendor.py        # puts the four submodules on sys.path, imports their drivers
  virtualbench.py   # shared "signal node" for simulate mode (coordinated sims)
  instruments/      # one adapter per instrument: real + simulate, same interface
  sequencer.py      # per-point loop: command -> settle -> read DMM/scope/PMU
  plan.py           # built-in test plans (amplitude, frequency)
  results.py        # error math, CSV, printed summary, plots
  cli.py            # `pmu-validate` entry point
apps/               # the four instrument apps as submodules
```

Why the shared `VirtualBench`? On the real bench all instruments observe one
physical node — that shared observation is the whole point of the cross-check.
The apps' individual simulators are independent and would never agree, so in
`--simulate` the source adapter *writes* the commanded signal into a shared
bench and the DMM/scope/PMU adapters *read* it back with realistic, independent
errors. It also seeds a ~5% PMU front-end scale error so the calibration test
recovers a real correction rather than trivially returning 1.0.

## Install

```powershell
git clone --recurse-submodules <this repo>
# if you forgot --recurse-submodules:
git submodule update --init --recursive

pip install -e .                 # core (numpy)
pip install -e ".[serial,visa,plot]"   # + real-hardware transports and plots
```

The instrument transports are optional extras — install only what you have
wired: `serial` (3325B, DMM, PMU CDC), `visa` (scope), `plot` (result PNGs).

## Desktop GUI

A Tkinter control panel wraps the same engine, with three tabs sharing one
Instruments bar:

1. **Setup Guide** — a wiring diagram (signal path + control cables) and a
   numbered A/B/C hookup checklist, with a per-instrument **Test → *IDN?**
   button to confirm each link.
2. **Run Validation** — the amplitude/frequency plans, a live results table, an
   embedded error/TVE plot, and a summary with a one-click **"Use this
   volts/count"** to load the recommended calibration back in for a re-run.
3. **Harmonics** — drive a waveform shape (or a custom mix in Simulate) and
   compare per-harmonic content **theoretical vs scope vs PMU** as a grouped bar
   chart + table, with the three THD figures.

```powershell
pmu-validate-gui            # or: python -m pmu_validation.gui
```

### Harmonics

A single 3325B can't synthesize an arbitrary harmonic mix, but its non-sine
**shapes are exact Fourier series** and double as a reference: square → odd
harmonics at 1/n (THD ~48%), triangle → odd at 1/n² (~12%), ramp → all at 1/n
(~80%). The Harmonics tab drives the shape, then analyzes the **scope** waveform
and the **PMU's own continuous stream** with the same FFT the PMU host uses
(`upmu.burstfft`), reporting per-order amplitude and THD against the theoretical
values. In Simulate mode you can also inject an arbitrary `order:fraction` mix.

## Quick start — no hardware (CLI)

```powershell
pmu-validate --simulate amplitude     # front-end scale (volts_per_count) sweep
pmu-validate --simulate frequency     # frequency accuracy across 57–63 Hz
```

Each run writes a timestamped CSV and PNG under `results/` plus a printed
summary. Example (amplitude, simulate mode):

```
  volts_per_count: current 0.00207 -> recommended 0.00217452  (x1.05049, spread 0.013% over 8 pts)
  PMU magnitude error vs DMM (uncalibrated): 4.83% worst
```

## The calibrate → verify workflow

1. **Amplitude sweep** → recommends a `volts_per_count` (the one front-end scale
   constant to calibrate, per the PMU README).
2. Re-run any plan with that value to confirm it closes the error:

```powershell
pmu-validate --simulate frequency --freqs 60 --volts-per-count 0.00217452
#   -> PMU vmag matches DMM to 0.008%, TVE 0.05% (< 1% M-class limit)
```

## Real bench

Wire the 3325B output, the DMM, and one scope channel to the **same injection
node**; connect the PMU on its USB-CDC port. Command amplitudes are in **Vrms**
at that node.

```powershell
pmu-validate amplitude `
    --source-port COM5 --dmm-port COM10 --scope-ip 169.254.220.205 `
    --pmu-port auto
```

Notes / bench cautions:
- Keep commanded amplitude within your 3325B output range (10 Vpp std / 40 Vpp
  with HV option 002) **and** your PMU front-end input range. The 3325B range
  vs. your front-end divider sets what fraction of full-scale you can exercise.
- The DMM and 3325B are RS-232 (each needs a null-modem cable — see their app
  READMEs); the scope is Ethernet/VISA; the PMU is USB CDC. All independent
  buses, so there is no contention.
- For magnitude + frequency the PMU does **not** need a GPS fix. Absolute phase
  and TVE do (see roadmap).

## Known caveat: off-nominal TVE

The PMU host computes TVE against a **static** top-of-second reference phasor
(it uses the reference magnitude and phase, not its frequency). This is only a
valid conformance metric at the nominal frequency — at off-nominal frequencies
the measured phasor rotates relative to the static reference and TVE becomes
meaningless (this is the host's own documented "off-nominal rotation" caveat).
The frequency plan therefore reports TVE only at nominal and judges off-nominal
points by **frequency and magnitude error** instead.

## Roadmap

v1 (this) covers steady-state **magnitude + frequency**. Next:
- **Absolute phase / TVE** by feeding the PMU's 1PPS (or zero-crossing) into a
  scope channel, giving an independent phase-vs-UTC reference.
- **ROCOF** via 3325B linear frequency sweeps at a known ramp rate.
- **Dynamic (modulation) tests** via the 3325B AM/PM modes.
- Pass/fail limits from IEEE C37.118 as a conformance report.

Done: steady-state magnitude + frequency (Run Validation tab) and **harmonics /
THD** (Harmonics tab).
```
