"""Azure Quantum runner + Azure billing meters for qcost-bench.

Adds the 2026 hyperscaler-divergence channel to the instrument:
  - IonQ on Azure     -> gate-token meter (AQT formula), vs shot meter on Braket
  - Rigetti on Azure  -> time meter, vs shot meter on Braket
Same machines, different doors, different meters: the channel-flip experiment.

Setup (see the Methodology & Execution Protocol document):
  pip install "azure-quantum[qiskit]" azure-identity
  az login                              # one-time browser auth
  export AZURE_QUANTUM_RESOURCE_ID="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Quantum/Workspaces/<ws>"
  export AZURE_QUANTUM_LOCATION="eastus"

Targets (list yours with `python azure_runner.py --list`):
  ionq.simulator (free)   ionq.qpu.aria-1 / ionq.qpu.forte-1 (check availability)
  rigetti.sim.qvm (free)  rigetti.qpu.<current-device>
  quantinuum.sim.h1-1e    quantinuum.qpu.h1-1 (credits recommended)

Every run also captures backend.estimate_cost() BEFORE submission -- that
pre-quote (exact / estimate / absent) is knowability-gradient data.
"""
from __future__ import annotations

import os
import time

from circuits import gate_counts, success_probability

# ---------------- Azure billing meters (rates in config.yaml; defaults Aug 2026) ----------------
AZURE_RATES = {
    # DEVICE-SPECIFIC constants (verified against Microsoft pricing.md 2026-08-18):
    # Aria and Forte carry DIFFERENT gate rates and DIFFERENT minima. The earlier
    # single-block version applied Aria constants to Forte executions -- the error
    # that produced the spurious "floor billed at 2x documented" and "3.56x
    # compilation factor" interpretations. Both invoices reconcile exactly under
    # the correct constants: phase-4 (1000 shots) = Forte mit-ON minimum $168.195
    # (the job ran under the channel DEFAULT, mitigation on -- see run() note);
    # phase-6 (50 shots, mit off) = Forte mit-OFF minimum $25.7899.
    "azure_ionq_aria": {
        "usd_per_1q_gate_shot": 0.000220,
        "usd_per_2q_gate_shot": 0.000975,
        "min_mitigation_on": 97.50,
        "min_mitigation_off": 12.4166,
    },
    "azure_ionq_forte": {
        "usd_per_1q_gate_shot": 0.0001645,
        "usd_per_2q_gate_shot": 0.001121,
        "min_mitigation_on": 168.195,
        "min_mitigation_off": 25.7899,
    },
    "azure_ionq": {  # legacy alias (Aria constants) -- do not use for Forte
        "usd_per_1q_gate_shot": 0.000220,
        "usd_per_2q_gate_shot": 0.000975,
        "min_mitigation_on": 97.50,
        "min_mitigation_off": 12.4166,
    },
    "azure_rigetti": {
        # Time-metered. VERIFY current per-10ms rate on the Azure pricing page
        # on the day of the corpus wave; this default encodes $0.02 per 10 ms.
        "usd_per_10ms": 0.02,
    },
}


def cost_azure_ionq(n_1q: int, n_2q: int, shots: int, mitigation: bool, device: str = "forte") -> float:
    """Azure IonQ gate-token meter (AQT). Depth-sensitive, time-blind.
    DEVICE-SPECIFIC: pass device='aria' or 'forte'; constants differ materially.
    NOTE: mitigation defaults ON at the platform; the on/off minima differ 6.5x
    on Forte, so the flag is a first-order pricing input, not a detail."""
    rates = AZURE_RATES[f"azure_ionq_{device}"]
    gate_cost = shots * (n_1q * rates["usd_per_1q_gate_shot"] + n_2q * rates["usd_per_2q_gate_shot"])
    floor = rates["min_mitigation_on"] if mitigation else rates["min_mitigation_off"]
    return max(gate_cost, floor)


def cost_azure_rigetti(exec_seconds: float, rates=AZURE_RATES["azure_rigetti"]) -> float:
    """Azure Rigetti time meter. Depth/shot-blind except through wall time."""
    import math
    increments = math.ceil(max(exec_seconds, 0) * 100)  # 10 ms increments
    return increments * rates["usd_per_10ms"]


# Transpile to this explicit basis, never to the Azure backend Target:
# azure-quantum's Target gate definitions mistranslate under qiskit 2.x
# (produces a non-identity circuit from a mirror circuit), and
# optimization_level=0 keeps the identity un-cancelled so gate counts
# reflect the submitted circuit (same convention as measure_all.py).
SAFE_BASIS = ["rz", "rx", "ry", "cx"]


# ---------------- runner ----------------
class AzureRunner:
    def __init__(self, target: str):
        from azure.quantum.qiskit import AzureQuantumProvider

        resource_id = os.environ["AZURE_QUANTUM_RESOURCE_ID"]
        location = os.environ.get("AZURE_QUANTUM_LOCATION", "eastus")
        self.provider = AzureQuantumProvider(resource_id=resource_id, location=location)
        self.target = target
        self.backend = self.provider.get_backend(target)

    def estimate(self, qc, shots: int):
        """Pre-submission quote -- knowability-gradient data. Returns
        (estimated_cost_usd_or_None, note)."""
        from qiskit import transpile
        from qiskit.transpiler.passes import RemoveBarriers
        tqc = RemoveBarriers()(transpile(qc, basis_gates=SAFE_BASIS, optimization_level=0))
        try:
            est = self.backend.estimate_cost(tqc, shots=shots)
            return float(est.estimated_total), f"pre-quote in {est.currency_code}"
        except Exception as e:
            return None, f"no pre-quote available: {e}"

    def run(self, qc, shots: int, mitigation: bool = False) -> dict:
        from qiskit import transpile

        rec = {
            "provider": f"azure_{self.target.split('.')[0]}",
            "azure_target": self.target,
            "circuit": qc.name,
            "width": qc.num_qubits,
            "logical_depth": qc.depth(),
            "shots": shots,
            "error_mitigation": mitigation,
        }
        from qiskit.transpiler.passes import RemoveBarriers
        # Azure's QIR validator rejects barrier for the IonQ adaptor
        # (QATTransformationFailed, 2026-08-17). Stripping it means the
        # provider compiler MAY cancel the mirror: fidelity from this path is
        # NOT decay-certified. Billing (the point of the Azure channel) is
        # unaffected.
        tqc = RemoveBarriers()(transpile(qc, basis_gates=SAFE_BASIS, optimization_level=0))
        est, est_note = None, "estimate skipped"
        try:
            e = self.backend.estimate_cost(tqc, shots=shots)
            est, est_note = float(e.estimated_total), e.currency_code
        except Exception as ex:
            est_note = f"unavailable: {ex}"
        rec["pre_quote_usd"], rec["pre_quote_note"] = est, est_note

        t0 = time.time()
        job = self.backend.run(tqc, shots=shots)
        result = job.result()
        wall = time.time() - t0
        counts = dict(result.get_counts())

        g = gate_counts(tqc)
        rec["transpiled_depth"] = tqc.depth()
        rec["depth_inflation"] = round(tqc.depth() / max(qc.depth(), 1), 3)
        rec["transpiled_gates"] = g
        rec["wall_seconds"] = round(wall, 3)  # queue-inclusive; NOT the billed quantity
        rec["success_prob"] = success_probability(counts, qc.name, qc.num_qubits)
        rec["counts"] = counts

        # Platform-reported billing, where the SDK exposes it (the invoice truth)
        try:
            details = job._azure_job.details
            rec["azure_cost_estimate"] = getattr(details, "cost_estimate", None) and str(details.cost_estimate)
            rec["azure_execution_time"] = str(getattr(details, "end_execution_time", "")) or None
        except Exception:
            rec["azure_cost_estimate"] = None

        # Formula-computed price under the target's Azure meter
        fam = self.target.split(".")[0]
        if fam == "ionq":
            rec["cost_formula_usd"] = round(cost_azure_ionq(g["1q"], g["2q"], shots, mitigation), 4)
        elif fam == "rigetti":
            rec["cost_formula_usd"] = None  # time-metered: computable only from billed duration, post hoc
        rec["qpu_seconds"] = None  # filled from invoice/portal for time-metered targets
        return rec


def main():
    import argparse, json, pathlib
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list available targets in the workspace")
    ap.add_argument("--target", help="e.g. ionq.simulator, rigetti.sim.qvm, ionq.qpu.forte-1")
    ap.add_argument("--estimate-only", action="store_true")
    ap.add_argument("--widths", default="3,8,12")
    ap.add_argument("--depths", default="8,16,32")
    ap.add_argument("--shots", type=int, default=1000)
    ap.add_argument("--mitigation", action="store_true")
    args = ap.parse_args()

    if args.list:
        from azure.quantum.qiskit import AzureQuantumProvider
        provider = AzureQuantumProvider(
            resource_id=os.environ["AZURE_QUANTUM_RESOURCE_ID"],
            location=os.environ.get("AZURE_QUANTUM_LOCATION", "eastus"))
        for b in provider.backends():
            name = b.name() if callable(b.name) else b.name
            print(" ", name)
        return

    from measure_all import fair_mirror
    runner = AzureRunner(args.target)
    widths = [int(x) for x in args.widths.split(",")]
    depths = [int(x) for x in args.depths.split(",")]
    outdir = pathlib.Path(__file__).parent / "results"
    outdir.mkdir(exist_ok=True)
    records = []
    for w in widths:
        for d in depths:
            qc = fair_mirror(w, d)
            if args.estimate_only:
                est, note = runner.estimate(qc, args.shots)
                print(f"{qc.name:14s} pre-quote: {est} ({note})")
                continue
            print(f"running {qc.name} on {args.target} ...", flush=True)
            rec = runner.run(qc, args.shots, mitigation=args.mitigation)
            records.append(rec)
            print(f"  fid={rec['success_prob']:.3f} pre_quote={rec['pre_quote_usd']} "
                  f"formula=${rec['cost_formula_usd']}")
            out = outdir / f"measure_all_{rec['provider']}.json"
            out.write_text(json.dumps(records, indent=2))
    if records:
        print(f"saved {len(records)} records")


if __name__ == "__main__":
    main()
