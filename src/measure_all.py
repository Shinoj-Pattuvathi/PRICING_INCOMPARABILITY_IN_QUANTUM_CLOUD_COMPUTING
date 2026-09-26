#!/usr/bin/env python3
"""measure_all.py -- ONE program that measures EVERYTHING the 9-circuit suite
measures, using ONE architecture-fair circuit family.

The fair circuit: a LINEAR-CHAIN MIRROR circuit. It entangles only
nearest-neighbour qubits in a line (q0-q1, q1-q2, ...), then undoes
everything. Ideal output: all zeros. Why it is fair: every quantum computer's
qubit layout -- IBM's heavy-hex grid, Rigetti's lattice, IonQ's all-to-all --
contains a simple line inside it. So this circuit compiles onto ANY machine
with no extra SWAP operations: its difficulty is the same everywhere
(depth_inflation stays ~1). GHZ and long-range circuits deliberately change
difficulty across machines; this one deliberately does not.

For each (width, depth) point it records the FULL measurement vector:

  Structure (known before running):
    width, logical_depth, gates_1q, gates_2q
  Compilation (known after transpiling to the target machine):
    transpiled_depth, transpiled gates, depth_inflation  (connectivity cost)
  Execution (known only after running -- the empirical conversion data):
    success_prob            (fidelity: fraction of all-zeros outcomes)
    error_per_layer         (derived decay rate: 1 - fid**(1/depth))
    qpu_seconds, shots_per_second   (speed -- the CLOPS-like bridge to time cost)
  Economics (the same run priced under ALL THREE billing meters):
    cost_time_usd     (IBM meter:    seconds x rate)
    cost_volume_usd   (Braket meter: task + shots x rate)
    cost_gates_usd    (IonQ meter:   gate-shots x rates)
    cps_time / cps_volume / cps_gates  (each meter's $ per correct answer)

The demonstration in one file: identical physical run, three simultaneous
bills that disagree -- and disagree by different factors at different
(width, depth) points. Even with an architecture-fair workload, the meters
are incommensurable.

Usage:
  python measure_all.py --provider local                     # free sanity test
  python measure_all.py --provider ibm --shots 1000
  python measure_all.py --provider braket_ionq_forte --shots 200
  python measure_all.py --provider ionq_direct --shots 200
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np
from qiskit import QuantumCircuit, transpile

from circuits import gate_counts
from pricing import DEFAULT_RATES

try:
    import yaml
    _cfg = pathlib.Path(__file__).parent / "config.yaml"
    RATES = yaml.safe_load(_cfg.read_text()).get("rates", DEFAULT_RATES) if _cfg.exists() else DEFAULT_RATES
except Exception:
    RATES = DEFAULT_RATES


# ---------------------------------------------------------------- fair circuit
def fair_mirror(width: int, depth: int, seed: int = 7) -> QuantumCircuit:
    """Linear-chain mirror circuit: same difficulty on every architecture."""
    rng = np.random.default_rng(seed)
    qc = QuantumCircuit(width, width)
    u = QuantumCircuit(width)
    for layer in range(depth):
        for q in range(width):
            u.rz(float(rng.uniform(0, 2 * np.pi)), q)
            u.sx(q)
            u.rz(float(rng.uniform(0, 2 * np.pi)), q)
        for q in range(layer % 2, width - 1, 2):  # nearest-neighbour ONLY
            u.cx(q, q + 1)
    qc.compose(u, inplace=True)
    qc.barrier()  # blocks U·U† cross-seam cancellation by optimizing compilers
    qc.compose(u.inverse(), inplace=True)
    qc.measure(range(width), range(width))
    qc.name = f"fair_w{width}_d{depth}"
    return qc


# ---------------------------------------------------------------- measurement
def full_measurement(qc, tqc, counts, shots, qpu_seconds) -> dict:
    total = sum(counts.values()) or 1
    fid = counts.get("0" * qc.num_qubits, 0) / total
    g_log, g_tr = gate_counts(qc), gate_counts(tqc)
    depth = max(qc.depth(), 1)

    # --- the same run, priced under all three meters ---
    cost_time = qpu_seconds * RATES["ibm"]["usd_per_qpu_second"]
    cost_volume = (RATES["braket_ionq_forte"]["usd_per_task"]
                   + shots * RATES["braket_ionq_forte"]["usd_per_shot"])
    cost_gates = max(
        shots * (g_tr["1q"] * RATES["ionq_direct"]["usd_per_1q_gate_shot"]
                 + g_tr["2q"] * RATES["ionq_direct"]["usd_per_2q_gate_shot"]),
        RATES["ionq_direct"]["usd_min_per_job"],
    )
    successes = max(shots * fid, 1e-9)
    return {
        # structure
        "circuit": qc.name, "width": qc.num_qubits,
        "logical_depth": qc.depth(), "gates_1q": g_log["1q"], "gates_2q": g_log["2q"],
        # compilation / connectivity
        "transpiled_depth": tqc.depth(),
        "transpiled_gates": g_tr,
        "depth_inflation": round(tqc.depth() / depth, 3),
        # execution
        "shots": shots,
        "success_prob": round(fid, 4),
        "error_per_layer": round(1 - fid ** (1 / depth), 5) if fid > 0 else None,
        "qpu_seconds": round(qpu_seconds, 4),
        "shots_per_second": round(shots / qpu_seconds, 1) if qpu_seconds > 0 else None,
        # economics: three meters, one run
        "cost_time_usd": round(cost_time, 4),
        "cost_volume_usd": round(cost_volume, 4),
        "cost_gates_usd": round(cost_gates, 4),
        "cps_time": round(cost_time / successes, 6),
        "cps_volume": round(cost_volume / successes, 6),
        "cps_gates": round(cost_gates / successes, 6),
    }


# ---------------------------------------------------------------- execution
def run_local(qc, shots):
    from qiskit.providers.basic_provider import BasicProvider
    backend = BasicProvider().get_backend("basic_simulator")
    tqc = transpile(qc, backend)
    t0 = time.time()
    counts = backend.run(tqc, shots=shots).result().get_counts()
    return tqc, counts, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="local",
                    choices=["local", "ibm", "braket_ionq_forte", "braket_rigetti",
                             "braket_iqm_garnet", "braket_iqm_emerald", "ionq_direct"])
    ap.add_argument("--widths", default="3,5,8")
    ap.add_argument("--depths", default="2,8,24")
    ap.add_argument("--shots", type=int, default=1000)
    ap.add_argument("--backend", default=None,
                    help="override backend name / device ARN (e.g. Braket SV1 for free tests)")
    args = ap.parse_args()

    widths = [int(x) for x in args.widths.split(",")]
    depths = [int(x) for x in args.depths.split(",")]

    runner = None
    if args.provider != "local":
        from runners import get_runner
        runner = get_runner(args.provider, args.backend)

    records = []
    for w in widths:
        for d in depths:
            qc = fair_mirror(w, d)
            print(f"running {qc.name} on {args.provider} ...", flush=True)
            if runner is None:
                tqc, counts, secs = run_local(qc, args.shots)
            else:
                rec = runner.run(qc, args.shots)
                counts, secs = rec["counts"], rec["qpu_seconds"]
                # optimization_level=0: the mirror circuit is mathematically the
                # identity, so any optimization cancels it and fakes the gate meter
                tqc = transpile(qc, basis_gates=["rz", "sx", "x", "cx"], optimization_level=0)
            row = full_measurement(qc, tqc, counts, args.shots, secs)
            row["provider"] = args.provider
            records.append(row)

    outdir = pathlib.Path(__file__).parent / "results"
    outdir.mkdir(exist_ok=True)
    out = outdir / f"measure_all_{args.provider}.json"
    out.write_text(json.dumps(records, indent=2))

    # ----- report -----
    print(f"\n{'circuit':14s} {'inflate':>7s} {'fid':>6s} {'err/layer':>9s} "
          f"{'sh/s':>7s} {'$time':>8s} {'$vol':>8s} {'$gates':>8s}")
    for r in records:
        print(f"{r['circuit']:14s} {r['depth_inflation']:>7.2f} {r['success_prob']:>6.3f} "
              f"{str(r['error_per_layer']):>9s} {str(r['shots_per_second']):>7s} "
              f"{r['cost_time_usd']:>8.2f} {r['cost_volume_usd']:>8.2f} {r['cost_gates_usd']:>8.2f}")

    print("\n--- Three bills for the identical run ---")
    for r in records:
        c = sorted([("time", r["cps_time"]), ("volume", r["cps_volume"]), ("gates", r["cps_gates"])],
                   key=lambda x: x[1])
        print(f"{r['circuit']:14s} cheapest meter: {c[0][0]:6s} | "
              f"time:volume:gates = 1 : {r['cps_volume']/max(r['cps_time'],1e-12):.1f} : "
              f"{r['cps_gates']/max(r['cps_time'],1e-12):.1f}")
    print("\nIf the three ratios above were constant across circuits, the meters")
    print("would be convertible. Watch them drift as width and depth change --")
    print("with an architecture-FAIR circuit. That drift is pricing")
    print(f"incomparability, measured. Saved: {out}")


if __name__ == "__main__":
    main()
