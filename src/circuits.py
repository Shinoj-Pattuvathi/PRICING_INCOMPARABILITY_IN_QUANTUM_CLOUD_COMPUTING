"""Benchmark circuits that stress the four non-separable performance variables:
width (n qubits), depth (layers), fidelity (measured as success probability),
and connectivity (transpilation overhead from long-range interactions).

All circuits have a *known ideal outcome*, so fidelity is directly measurable
from counts on any hardware without tomography.

Built on Qiskit QuantumCircuit as the common IR; runners convert as needed.
"""
from __future__ import annotations

import numpy as np
from qiskit import QuantumCircuit


def mirror_circuit(width: int, depth: int, seed: int = 42) -> QuantumCircuit:
    """Random layered circuit U followed by its inverse U-dagger.

    Ideal output is |00...0>, so success probability = P(all zeros).
    Stresses width x depth jointly -- the non-separability the paper describes:
    errors compound multiplicatively, not additively, across the two dimensions.
    """
    rng = np.random.default_rng(seed)
    qc = QuantumCircuit(width, width)
    u = QuantumCircuit(width)
    for _ in range(depth):
        # single-qubit layer
        for q in range(width):
            u.rz(float(rng.uniform(0, 2 * np.pi)), q)
            u.sx(q)
            u.rz(float(rng.uniform(0, 2 * np.pi)), q)
        # entangling layer: nearest-neighbour pairs, alternating offset
        offset = int(rng.integers(0, 2))
        for q in range(offset, width - 1, 2):
            u.cx(q, q + 1)
    qc.compose(u, inplace=True)
    qc.barrier()  # blocks U·U† cross-seam cancellation by optimizing compilers
    qc.compose(u.inverse(), inplace=True)
    qc.measure(range(width), range(width))
    qc.name = f"mirror_w{width}_d{depth}"
    return qc


def ghz_circuit(width: int) -> QuantumCircuit:
    """GHZ state preparation + measurement.

    Ideal output: 50% |0...0>, 50% |1...1>. Success = P(000..) + P(111..).
    Stresses width and coherence; the CNOT ladder is cheap on all-to-all
    (trapped-ion) connectivity and expensive on heavy-hex (superconducting).
    """
    qc = QuantumCircuit(width, width)
    qc.h(0)
    for q in range(width - 1):
        qc.cx(q, q + 1)
    qc.measure(range(width), range(width))
    qc.name = f"ghz_w{width}"
    return qc


def connectivity_stress_circuit(width: int, rounds: int = 2) -> QuantumCircuit:
    """CNOTs between maximally distant qubit pairs, then mirrored.

    Ideal output |0...0>. On all-to-all hardware these are native 2q gates;
    on limited-connectivity hardware each long-range CNOT becomes a SWAP
    chain, inflating transpiled depth. The ratio (transpiled depth / logical
    depth) is the connectivity-overhead measurement.
    """
    qc = QuantumCircuit(width, width)
    u = QuantumCircuit(width)
    for _ in range(rounds):
        for q in range(width // 2):
            partner = width - 1 - q
            if partner != q:
                u.h(q)
                u.cx(q, partner)
    qc.compose(u, inplace=True)
    qc.compose(u.inverse(), inplace=True)
    qc.measure(range(width), range(width))
    qc.name = f"conn_w{width}_r{rounds}"
    return qc


def success_probability(counts: dict, circuit_name: str, width: int) -> float:
    """Fidelity proxy from measured counts, given each circuit's known ideal output."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    zeros = "0" * width
    ones = "1" * width
    if circuit_name.startswith("ghz"):
        return (counts.get(zeros, 0) + counts.get(ones, 0)) / total
    return counts.get(zeros, 0) / total


def gate_counts(qc: QuantumCircuit) -> dict:
    """Count 1q and 2q gates (excluding measurements/barriers) for gate-based pricing."""
    ops = {"1q": 0, "2q": 0}
    for inst in qc.data:
        n = inst.operation.num_qubits
        if inst.operation.name in ("measure", "barrier"):
            continue
        if n == 1:
            ops["1q"] += 1
        elif n >= 2:
            ops["2q"] += 1
    return ops


def workload_suite(config: dict) -> list:
    """Build the full suite from the config grid."""
    suite = []
    for w, d in config.get("mirror", [[4, 4], [4, 16], [8, 4], [8, 16]]):
        suite.append(mirror_circuit(w, d))
    for w in config.get("ghz_widths", [4, 8, 12]):
        suite.append(ghz_circuit(w))
    for w in config.get("conn_widths", [6, 10]):
        suite.append(connectivity_stress_circuit(w))
    return suite
