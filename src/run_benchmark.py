#!/usr/bin/env python3
"""Run the incomparability benchmark on real hardware.

Usage:
  python run_benchmark.py --provider ibm --dry-run
  python run_benchmark.py --provider ibm --shots 1000
  python run_benchmark.py --provider braket_ionq_forte --shots 1000
  python run_benchmark.py --provider ionq_direct --shots 1000
  python analyze.py results/*.json          # after 2+ providers have run

--dry-run estimates cost WITHOUT submitting anything. Note what dry-run can
and cannot estimate per provider -- that asymmetry is itself the paper's point:
  braket_*    : exact (tasks x shots are known a priori)
  ionq_direct : exact-ish (gate counts known after transpilation)
  ibm         : UNKNOWABLE a priori (billed by QPU time, which depends on
                CLOPS-like execution speed you only learn by running)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

from circuits import workload_suite, gate_counts
from pricing import DEFAULT_RATES, cost_braket, cost_ionq_direct


def load_config() -> dict:
    p = pathlib.Path(__file__).parent / "config.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text())
    return {"rates": DEFAULT_RATES, "workloads": {}, "shots": 1000}


def dry_run(provider: str, suite, shots: int, rates: dict):
    print(f"\n== DRY RUN: {provider} | {len(suite)} circuits x {shots} shots ==\n")
    total = 0.0
    for qc in suite:
        if provider.startswith("braket"):
            c = cost_braket(1, shots, rates[provider])
            note = "exact: meter counts tasks+shots"
        elif provider == "ionq_direct":
            g = gate_counts(qc)
            c = cost_ionq_direct(g["1q"], g["2q"], shots, rates["ionq_direct"])
            note = f"gate-based: {g['1q']} 1q + {g['2q']} 2q gates"
        elif provider == "ibm":
            c = float("nan")
            note = "NOT ESTIMABLE: billed by QPU seconds, unknown until run"
        else:
            sys.exit(f"unknown provider {provider}")
        total += 0 if c != c else c
        print(f"  {qc.name:20s}  ${c:>10.2f}   ({note})")
    if provider == "ibm":
        print("\n  Total: unknowable from the price sheet -- this is the demonstration.")
        print("  (IBM Open Plan gives 10 free min/28 days; use it to calibrate.)")
    else:
        print(f"\n  Total: ${total:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True,
                    choices=["ibm", "braket_ionq_forte", "braket_rigetti", "ionq_direct"])
    ap.add_argument("--shots", type=int, default=None)
    ap.add_argument("--backend", default=None, help="override backend name/ARN")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-circuits", type=int, default=None, help="run only first N circuits")
    args = ap.parse_args()

    cfg = load_config()
    rates = cfg.get("rates", DEFAULT_RATES)
    shots = args.shots or cfg.get("shots", 1000)
    suite = workload_suite(cfg.get("workloads", {}))
    if args.max_circuits:
        suite = suite[: args.max_circuits]

    if args.dry_run:
        dry_run(args.provider, suite, shots, rates)
        return

    from runners import get_runner

    runner = get_runner(args.provider, args.backend)
    outdir = pathlib.Path(__file__).parent / "results"
    outdir.mkdir(exist_ok=True)
    results = []
    for i, qc in enumerate(suite, 1):
        print(f"[{i}/{len(suite)}] {qc.name} on {args.provider} ...", flush=True)
        try:
            rec = runner.run(qc, shots)
            results.append(rec)
            print(f"    success_prob={rec['success_prob']:.3f}  "
                  f"depth {rec['logical_depth']}->{rec['transpiled_depth']} "
                  f"(x{rec['depth_inflation']})")
        except Exception as e:
            print(f"    FAILED: {e}")
        # checkpoint after every circuit -- hardware jobs are expensive
        out = outdir / f"{args.provider}.json"
        out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved {len(results)} results to {out}")
    print("Run the other providers, then:  python analyze.py results/*.json")


if __name__ == "__main__":
    main()
