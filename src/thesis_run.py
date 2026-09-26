#!/usr/bin/env python3
"""thesis_run.py -- the definitive, budget-guarded execution programme.

Runs the exact workload set that tests H1-H4 within a GBP 200 hard cap.
Money is spent ONLY where it buys fidelity measurements (H3, H4); price
surfaces under shot/gate meters are exact from formulas and cost nothing.

Usage:
  python3 thesis_run.py --plan                 # show full plan + budget (default)
  python3 thesis_run.py --phase 1 --execute    # run one phase for real
  python3 thesis_run.py --phase 4 --estimate   # Azure estimate-only pass
  python3 thesis_run.py --ledger               # spend so far

Every job appends one line to results/ledger.csv BEFORE submission
(committed spend), so the budget guard survives crashes.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import pathlib
import sys

from circuits import ghz_circuit
from measure_all import fair_mirror

BUDGET_GBP = 200.0
USD_PER_GBP = 1.28  # update on run day
BUDGET_USD = BUDGET_GBP * USD_PER_GBP

HERE = pathlib.Path(__file__).parent
RESULTS = HERE / "results"
LEDGER = RESULTS / "ledger.csv"

# ---------------------------------------------------------------- the plan
# grid: widths x depths chosen so every hypothesis has the data it needs.
GRID_W = [3, 8, 12]
GRID_D = [8, 16, 32]

def mirror_set(ws, ds):
    return [("mirror", w, d) for w in ws for d in ds]

def ghz_set(ws):
    return [("ghz", w, 0) for w in ws]

PLAN = [
    dict(phase=0, name="Pipeline validation (free)", provider="local", channel="local",
         circuits=mirror_set([3], [8]) + ghz_set([3]), shots=1000, reps=1,
         est_usd=0.0, hypotheses="none (plumbing)",
         notes="Also run azure_runner.py --target ionq.simulator / rigetti.sim.qvm manually; "
               "and --estimate-only on every QPU target (knowability data, free)."),
    dict(phase=1, name="IBM full grid, 2 reps (free tier)", provider="ibm", channel="ibm",
         circuits=mirror_set(GRID_W, GRID_D) + ghz_set(GRID_W), shots=1000, reps=2,
         shots_override={(12, 32): 4000, (8, 32): 4000},
         est_usd=0.0, hypotheses="H3 (gamma fit, most data), H1, H4",
         notes="Time meter anchor. Spread reps across different days. Uses ~free Open Plan minutes; "
               "check remaining quota on the IBM dashboard between reps."),
    dict(phase=2, name="Rigetti via Braket, full grid, 1 rep (verbatim rerun)", provider="braket_rigetti", channel="braket",
         circuits=mirror_set(GRID_W, GRID_D) + ghz_set([8]), shots=1000, reps=1,
         est_usd=7.5, hypotheses="H1, H3, H4; H2 pair-half",
         notes="Volume meter. Post-barrier VERBATIM rerun 2026-08-16: 10 tasks x $0.30 + 10k shots "
               "x $0.000425. Pre-barrier 2-rep data in results/pre_barrier/ (pricing-valid only)."),
    dict(phase=3, name="IQM Garnet via Braket, w8 column, 2 reps", provider="braket_iqm_garnet", channel="braket",
         circuits=mirror_set([8], GRID_D), shots=1000, reps=2,
         est_usd=11.0, hypotheses="H3 replication (2nd superconducting vendor), H4",
         notes="6 tasks x $0.30 + 6k shots x $0.00145 (verified). Weekday availability windows."),
    dict(phase=4, name="IonQ Forte-1 via Azure, flip anchor", provider="azure_ionq", channel="azure",
         azure_target="ionq.qpu.forte-1",
         circuits=[("mirror", 3, 8)], shots=1000, reps=1,
         est_usd=47.28, hypotheses="H2 EXECUTED same-machine flip: Forte-1 Azure (gate meter, $47.28) "
                                    "vs Forte-1 Braket (volume meter, $80.30 exact ex ante)",
         notes="REDESIGNED 2026-08-16: no rigetti.qpu target exists on Azure (sim only), "
               "but ionq.qpu.forte-1 does -> same-machine flip moves to IonQ. Mitigation OFF."),
    dict(phase=5, name="IonQ via Braket, decay anchors", provider="braket_ionq_forte", channel="braket",
         circuits=[("mirror", 8, 16), ("mirror", 8, 32)], shots=580, reps=1,
         est_usd=93.40, hypotheses="H3 on trapped ion (2-point decay), H4 QFC",
         notes="Volume meter; price exact ex ante ($46.70/point = $0.30 + 580 x $0.08). 580 shots "
               "sized so remaining ~$97.46 AWS credits cover full metered cost with margin; "
               "binomial SE at expected ion fidelities ~+/-0.02, thesis-adequate."),
    dict(phase=6, name="IonQ Forte-1 via Azure, floor-regime point", provider="azure_ionq", channel="azure",
         azure_target="ionq.qpu.forte-1",
         circuits=[("mirror", 3, 8)], shots=50, reps=1,
         est_usd=12.42, hypotheses="H1/H2: same circuit as phase 4 but 50 shots -> floor binds. "
                                    "The shots dial crosses the minimum-charge boundary.",
         notes="Mitigation OFF. 50-shot gate cost ~$8.42 post-compilation-calibrated < $12.4166 "
               "floor -> floor binds; completes the floor-crossing demonstration. Rest of the "
               "Azure gate-price surface computed analytically -- free and exact."),
]

# ---------------------------------------------------------------- helpers
def build_circuit(kind, w, d):
    return fair_mirror(w, d) if kind == "mirror" else ghz_circuit(w)

def plan_cost(entry):
    return entry["est_usd"]

def committed_spend():
    if not LEDGER.exists():
        return 0.0
    with open(LEDGER) as f:
        return sum(float(r["est_usd"]) for r in csv.DictReader(f))

def ledger_append(row):
    RESULTS.mkdir(exist_ok=True)
    new = not LEDGER.exists()
    with open(LEDGER, "a", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=["ts", "phase", "provider", "circuit", "shots", "rep", "est_usd", "status"])
        if new:
            wtr.writeheader()
        wtr.writerow(row)

def show_plan():
    total = 0.0
    print(f"{'ph':>2s} {'name':44s} {'jobs':>4s} {'est USD':>8s}  hypotheses")
    for e in PLAN:
        njobs = len(e["circuits"]) * e["reps"]
        total += e["est_usd"]
        print(f"{e['phase']:>2d} {e['name']:44s} {njobs:>4d} {e['est_usd']:>8.2f}  {e['hypotheses']}")
    print(f"\nTotal estimated cash: ${total:.2f} = £{total/USD_PER_GBP:.0f}  (cap £{BUDGET_GBP:.0f})")
    print(f"Committed so far:     ${committed_spend():.2f}")
    print("\nPer-phase notes:")
    for e in PLAN:
        print(f"  [{e['phase']}] {e['notes']}")

def run_phase(phase, execute=False, estimate=False):
    e = next(x for x in PLAN if x["phase"] == phase)
    per_job = e["est_usd"] / max(len(e["circuits"]) * e["reps"], 1)
    spent = committed_spend()
    if spent + e["est_usd"] > BUDGET_USD:
        sys.exit(f"BUDGET GUARD: phase {phase} (${e['est_usd']:.2f}) would exceed "
                 f"£{BUDGET_GBP:.0f} cap (committed ${spent:.2f}). Aborting.")
    # choose runner
    if e["channel"] == "azure":
        from azure_runner import AzureRunner
        target = {"azure_rigetti": None, "azure_ionq": None}  # resolved below
        # resolve current QPU target names from the workspace
        from azure.quantum.qiskit import AzureQuantumProvider
        import os
        prov = AzureQuantumProvider(resource_id=os.environ["AZURE_QUANTUM_RESOURCE_ID"],
                                    location=os.environ.get("AZURE_QUANTUM_LOCATION", "eastus"))
        target = e.get("azure_target")
        if not target:
            fam = "rigetti" if e["provider"] == "azure_rigetti" else "ionq"
            qpus = [(b.name() if callable(b.name) else b.name) for b in prov.backends()]
            qpus = [n for n in qpus if n.startswith(f"{fam}.qpu")]
            if not qpus:
                sys.exit(f"no {fam} QPU target visible in workspace")
            target = qpus[0]
        runner = AzureRunner(target)
        print(f"Azure target: {target}")
    elif e["channel"] == "braket" or e["channel"] == "ibm":
        from runners import get_runner
        runner = get_runner(e["provider"])  # user's runners.py handles IQM ARNs natively
    else:
        runner = None  # local

    results = []
    for rep in range(1, e["reps"] + 1):
        for kind, w, d in e["circuits"]:
            shots = e.get("shots_override", {}).get((w, d), e["shots"])
            qc = build_circuit(kind, w, d)
            label = f"{qc.name} rep{rep}"
            if estimate and e["channel"] == "azure":
                est, note = runner.estimate(qc, shots)
                print(f"  {label:24s} pre-quote: {est} ({note})")
                continue
            if not execute:
                print(f"  would run {label:24s} shots={shots} est ${per_job:.2f}")
                continue
            ledger_append(dict(ts=datetime.datetime.now().isoformat(timespec='seconds'),
                               phase=phase, provider=e["provider"], circuit=qc.name,
                               shots=shots, rep=rep, est_usd=round(per_job, 4), status="submitted"))
            print(f"  [{len(results)+1}] {label} ...", flush=True)
            if e["channel"] == "local":
                from measure_all import run_local, full_measurement
                tqc, counts, secs = run_local(qc, shots)
                rec = full_measurement(qc, tqc, counts, shots, secs)
                rec["provider"] = "local"
            elif e["channel"] == "azure":
                rec = runner.run(qc, shots, mitigation=False)
            else:
                rec = runner.run(qc, shots)
            rec["rep"], rec["phase"] = rep, phase
            rec["run_date"] = datetime.date.today().isoformat()
            results.append(rec)
            out = RESULTS / f"thesis_{e['provider']}.json"
            existing = json.loads(out.read_text()) if out.exists() else []
            existing.append(rec)
            out.write_text(json.dumps(existing, indent=2, default=str))
    if execute:
        print(f"\nphase {phase} complete: {len(results)} records -> results/thesis_{e['provider']}.json")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--ledger", action="store_true")
    ap.add_argument("--phase", type=int)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    args = ap.parse_args()
    if args.ledger:
        print(f"committed spend: ${committed_spend():.2f} of ${BUDGET_USD:.2f} (£{BUDGET_GBP:.0f})")
        return
    if args.phase is None or args.plan:
        show_plan()
        return
    run_phase(args.phase, execute=args.execute, estimate=args.estimate)

if __name__ == "__main__":
    main()
