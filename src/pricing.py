"""The three incompatible pricing models, encoded exactly as billed.

Each function maps *what its provider actually meters* to dollars:
  IBM     -> QPU seconds            (fixed-cost architecture -> time-based)
  Braket  -> tasks + shots          (marketplace -> execution-volume)
  IonQ    -> gate-shots by depth    (variable-cost architecture -> depth-adjusted)

The point of the demonstration: these functions take DIFFERENT ARGUMENTS.
There is no common unit. Any cross-provider comparison requires empirical
measurement (running the workload) -- the 'unbuilt conversion layer'.

Rates live in config.yaml because they change; defaults reflect Aug 2026.
"""
from __future__ import annotations

DEFAULT_RATES = {
    "ibm": {
        # Pay-as-you-go plan, USD per second of QPU time ($96/min).
        "usd_per_qpu_second": 1.60,
    },
    "braket_ionq_forte": {
        "usd_per_task": 0.30,
        "usd_per_shot": 0.08,
    },
    "braket_rigetti": {  # superconducting via Braket, for a 3-way contrast
        "usd_per_task": 0.30,
        "usd_per_shot": 0.0009,
    },
    "ionq_direct": {
        # IonQ native gate-based pricing: charged per gate-shot, by gate type.
        "usd_per_1q_gate_shot": 0.00022,
        "usd_per_2q_gate_shot": 0.000975,
        "usd_min_per_job": 1.00,
    },
}


def cost_ibm(qpu_seconds: float, rates: dict) -> float:
    """Time-based billing. Depth, width, shots are invisible to the meter --
    only wall-clock QPU time matters. Rational for fixed-cost hardware."""
    return qpu_seconds * rates["usd_per_qpu_second"]


def cost_braket(n_tasks: int, n_shots: int, rates: dict) -> float:
    """Volume-based billing. Time and depth are invisible to the meter --
    a 2-gate circuit and a 2000-gate circuit cost the same per shot."""
    return n_tasks * rates["usd_per_task"] + n_shots * rates["usd_per_shot"]


def cost_ionq_direct(n_1q_gates: int, n_2q_gates: int, n_shots: int, rates: dict) -> float:
    """Depth-adjusted billing. Cost scales with gate count x shots --
    time and task count are invisible. Rational for variable-cost hardware."""
    raw = n_shots * (
        n_1q_gates * rates["usd_per_1q_gate_shot"]
        + n_2q_gates * rates["usd_per_2q_gate_shot"]
    )
    return max(raw, rates["usd_min_per_job"])


def cost_for_result(result: dict, rates_cfg: dict) -> float:
    """Dispatch on the provider's billing model using whatever that model meters."""
    p = result["provider"]
    if p == "ibm":
        return cost_ibm(result["qpu_seconds"], rates_cfg["ibm"])
    if p.startswith("braket"):
        return cost_braket(1, result["shots"], rates_cfg[p])
    if p == "ionq_direct":
        g = result["transpiled_gates"]
        return cost_ionq_direct(g["1q"], g["2q"], result["shots"], rates_cfg["ionq_direct"])
    raise ValueError(f"unknown provider {p}")


def cost_per_successful_shot(result: dict, rates_cfg: dict) -> float:
    """The buyer's actual question: dollars per correct answer.
    Computable only AFTER running the workload on each machine -- this is the
    empirical conversion data no price sheet contains."""
    cost = cost_for_result(result, rates_cfg)
    successes = result["shots"] * max(result["success_prob"], 1e-12)
    return cost / successes
