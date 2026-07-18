"""Command-line entry point for the micro-PMU validation bench.

Examples
--------
  # No hardware -- full framework check against the coordinated simulator:
  pmu-validate --simulate amplitude
  pmu-validate --simulate frequency

  # Real bench (wire the 3325B/DMM/scope to one node, PMU on its CDC port):
  pmu-validate amplitude \
      --source-port COM5 --dmm-port COM10 --scope-ip 169.254.220.205 \
      --pmu-port auto

  # Skip the scope (only DMM as reference):
  pmu-validate --simulate frequency --no-scope
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import plan as plans
from .results import build_row, write_csv, summarize, plot
from .sequencer import run_sweep


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pmu-validate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("plan", choices=sorted(plans.PLANS), help="test plan to run")
    p.add_argument("--simulate", action="store_true",
                   help="run against the coordinated virtual bench (no hardware)")

    hw = p.add_argument_group("hardware")
    hw.add_argument("--source-port", help="HP 3325B RS-232 COM port")
    hw.add_argument("--source-baud", type=int, default=4800)
    hw.add_argument("--dmm-port", help="HP 34401A RS-232 COM port")
    hw.add_argument("--dmm-baud", type=int, default=9600)
    hw.add_argument("--dmm-parity", default="N", choices=["N", "E", "O"])
    hw.add_argument("--scope-ip", help="MSO8104A IP address")
    hw.add_argument("--scope-channel", type=int, default=1)
    hw.add_argument("--pmu-port", default="auto",
                    help="micro-PMU CDC port, or 'auto' (USB 0483:5740)")
    hw.add_argument("--no-scope", action="store_true",
                    help="omit the scope (use only the DMM as reference)")

    cfg = p.add_argument_group("config")
    cfg.add_argument("--volts-per-count", type=float, default=None,
                     help="override the PMU front-end scale for this run")
    cfg.add_argument("--report-rate", type=float, default=10.0)

    sw = p.add_argument_group("sweep overrides (optional)")
    sw.add_argument("--freqs", type=float, nargs="+",
                    help="frequency-plan points (Hz)")
    sw.add_argument("--vrms", type=float, default=None,
                    help="frequency-plan amplitude (Vrms)")
    sw.add_argument("--vrms-steps", type=float, nargs="+",
                    help="amplitude-plan levels (Vrms)")
    sw.add_argument("--amp-freq", type=float, default=60.0,
                    help="amplitude-plan frequency (Hz)")
    sw.add_argument("--settle", type=float, default=None,
                    help="override per-point settle time (s)")

    out = p.add_argument_group("output")
    out.add_argument("--out-dir", default="results", help="output directory")
    out.add_argument("--tag", default=None, help="label for output filenames")
    out.add_argument("--no-plot", action="store_true")
    return p


def _build_points(args):
    if args.plan == "amplitude":
        kwargs = {"freq_hz": args.amp_freq}
        if args.vrms_steps:
            kwargs["levels"] = tuple(args.vrms_steps)
        if args.settle is not None:
            kwargs["settle_s"] = args.settle
        return plans.amplitude_plan(**kwargs)
    kwargs = {}
    if args.vrms is not None:
        kwargs["vrms"] = args.vrms
    if args.freqs:
        kwargs["freqs"] = tuple(args.freqs)
    if args.settle is not None:
        kwargs["settle_s"] = args.settle
    return plans.frequency_plan(**kwargs)


def _fmt(x, spec=".4f"):
    return format(x, spec) if x is not None else "  --  "


def _progress(i, total, res):
    p = res.pmu or {}
    s = res.scope or {}
    lock = "LOCK" if p.get("synced") else "----"
    line = (f"[{i:>2}/{total}] {res.point.label:<12} [{lock}] "
            f"DMM={_fmt(res.dmm_vrms)}Vrms scope={_fmt(s.get('vrms'))}Vrms  "
            f"PMU V={_fmt(p.get('vmag'))}Vrms f={_fmt(p.get('freq'))}Hz "
            f"TVE={_fmt(p.get('tve'), '.3f')}%")
    print(line + (f"  ! {res.note}" if res.note else ""))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # Import adapters lazily so --help works without the submodules present.
    from .instruments import make_source, make_dmm, make_scope, make_pmu
    from .virtualbench import VirtualBench

    bench = VirtualBench() if args.simulate else None
    use_scope = not args.no_scope and (args.simulate or args.scope_ip)

    source = make_source(args.simulate, port=args.source_port,
                         baud=args.source_baud, bench=bench)
    dmm = make_dmm(args.simulate, port=args.dmm_port, baud=args.dmm_baud,
                   parity=args.dmm_parity, bench=bench)
    scope = (make_scope(args.simulate, ip=args.scope_ip,
                        channel=args.scope_channel, bench=bench)
             if use_scope else None)
    pmu = make_pmu(args.simulate, port=args.pmu_port,
                   volts_per_count=args.volts_per_count,
                   report_rate=args.report_rate, bench=bench)

    points = _build_points(args)
    mode = "SIMULATE" if args.simulate else "HARDWARE"
    print(f"micro-PMU validation | plan={args.plan} | {mode} | "
          f"{len(points)} points | scope={'on' if scope else 'off'}")

    opened = []
    try:
        for name, inst in [("source", source), ("dmm", dmm),
                           ("scope", scope), ("pmu", pmu)]:
            if inst is None:
                continue
            inst.open()
            opened.append(inst)
            try:
                print(f"  {name}: {inst.identify()}")
            except Exception as exc:                    # noqa: BLE001
                print(f"  {name}: (no *IDN? -- {exc})")

        # A no-op scope so the sequencer signature stays uniform when omitted.
        class _NullScope:
            def read(self, navg=1):
                return {}
        results = run_sweep(source, dmm, scope or _NullScope(), pmu, points,
                            on_progress=_progress, read_scope=scope is not None)
    finally:
        for inst in reversed(opened):
            try:
                inst.close()
            except Exception:                           # noqa: BLE001
                pass

    rows = [build_row(r) for r in results]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = args.tag or f"{args.plan}_{'sim' if args.simulate else 'hw'}"
    out_dir = Path(args.out_dir)
    csv_path = write_csv(rows, out_dir / f"validate_{tag}_{stamp}.csv")
    print(f"\nwrote {csv_path}")

    summ = summarize(args.plan, rows, current_vpc=pmu.volts_per_count)
    print(f"\n=== summary ({args.plan}) ===")
    print(summ.text or "  (no valid points)")
    if summ.recommended_vpc is not None:
        print(f"\n  -> set volts_per_count = {summ.recommended_vpc:.8g} "
              f"(e.g. upmu --volts-per-count {summ.recommended_vpc:.8g})")

    if not args.no_plot:
        png = plot(args.plan, rows, out_dir / f"validate_{tag}_{stamp}.png")
        if png:
            print(f"\nwrote {png}")
        else:
            print("\n(plot skipped: matplotlib not installed -- pip install "
                  "'pmu-validation[plot]')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
