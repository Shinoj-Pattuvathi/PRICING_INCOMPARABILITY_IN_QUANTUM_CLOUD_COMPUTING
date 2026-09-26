# qcost-bench: measuring pricing incomparability on real quantum hardware

Companion program to *"Pricing Incomparability in Quantum Computing."* It runs
identical workloads on IBM Quantum, AWS Braket (IonQ Forte / Rigetti), and
IonQ direct, measures the four non-separable performance variables — **width,
depth, fidelity, connectivity** — and prices each run under its provider's
native billing meter. The output is the empirical signature of the paper's
claim: the implied "exchange rate" between billing units is not constant
across workloads, so no price-sheet conversion exists.

## How the demonstration works

Each provider meters something different:

| Provider | Billing meter | Blind to |
|---|---|---|
| IBM (pay-as-you-go) | QPU seconds | shots, depth, width |
| AWS Braket | tasks + shots | time, depth |
| IonQ direct | gate-shots (depth-adjusted) | time, task count |

The suite spans the (width, depth, connectivity) space: mirror circuits
(width×depth interaction, ideal output all-zeros), GHZ states (width +
coherence), and connectivity-stress circuits (long-range CNOTs that are free
on trapped-ion all-to-all connectivity but inflate depth ~2–10× on
superconducting heavy-hex — measured as `depth_inflation`). Fidelity is the
measured success probability against each circuit's known ideal output.

For each workload W and providers A, B the analysis computes
`rate(W) = cost_per_success_A(W) / cost_per_success_B(W)`.
**If billing units were commensurable, rate(W) would be constant. The measured
spread — typically several-fold — is the incomparability, quantified.** Deep
narrow circuits favor per-shot billing; shallow wide circuits favor gate-based
billing; fast-executing circuits favor time-based billing. Which provider is
"cheaper" is a property of the workload, not the price sheet.

## Setup

```bash
pip install qiskit qiskit-ibm-runtime amazon-braket-sdk qiskit-braket-provider qiskit-ionq pyyaml matplotlib
```

Credentials (only for providers you use):

- **IBM**: create an account at quantum.cloud.ibm.com, then once:
  `python -c "from qiskit_ibm_runtime import QiskitRuntimeService; QiskitRuntimeService.save_account(channel='ibm_quantum_platform', token='YOUR_TOKEN')"`
- **Braket**: `aws configure` with an account that has Braket enabled (check
  device availability windows in the Braket console).
- **IonQ direct**: `export IONQ_API_KEY=...` from cloud.ionq.com.

## Cost warning — read before running

Real hardware costs real money. Estimate first:

```bash
python run_benchmark.py --provider braket_ionq_forte --dry-run
python run_benchmark.py --provider ionq_direct --dry-run
python run_benchmark.py --provider ibm --dry-run
```

Note the dry-run asymmetry, which is itself the paper's point: Braket costs
are exact a priori, IonQ costs are computable after transpilation, **IBM costs
cannot be estimated from the price sheet at all** (they depend on execution
speed you learn only by running). At $0.08/shot, the full 9-circuit × 1000-shot
suite on IonQ Forte via Braket is ~$720 — trim with `--shots 100` or
`--max-circuits 3` first. IBM's free Open Plan (10 min QPU time / 28 days) can
run the entire suite at zero cost for calibration.

## Run

```bash
python run_benchmark.py --provider ibm --shots 1000
python run_benchmark.py --provider braket_ionq_forte --shots 1000
python run_benchmark.py --provider ionq_direct --shots 1000
python analyze.py results/*.json
```

Results checkpoint to `results/<provider>.json` after every circuit. The
analysis prints the measurement table, the exchange-rate table with a verdict,
and saves `incomparability_report.png`.

## Interpreting the output

- `depth_inflation` ≈ 1 on trapped-ion, ≫ 1 on superconducting → the
  connectivity term of the non-separability argument.
- `success_prob` falling faster than width+depth would predict additively →
  the axiom violation (Krantz et al.) that blocks an additive scale.
- Exchange-rate spread across workloads → the unbuilt conversion layer. This
  program *is* a fragment of that layer for exactly these workloads — and its
  results do not generalize to other workloads, which is why building the full
  layer is expensive and why no actor has done it.

Update `config.yaml` rates before drawing conclusions; prices change and the
qualitative result (instability) is robust to rate changes anyway.
