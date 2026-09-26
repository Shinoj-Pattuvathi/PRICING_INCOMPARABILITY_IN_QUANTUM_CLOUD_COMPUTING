"""Hardware runners for IBM Quantum, AWS Braket (IonQ Forte / Rigetti), and
IonQ direct. SDKs are imported lazily so you only need the ones you use.

Credentials:
  IBM    : `pip install qiskit-ibm-runtime`; save token once with
           QiskitRuntimeService.save_account(channel="ibm_quantum_platform", token=...)
  Braket : `pip install amazon-braket-sdk`; standard AWS credentials (aws configure)
  IonQ   : `pip install qiskit-ionq`; export IONQ_API_KEY=...

Every runner returns the same result dict so pricing/analysis are uniform:
  provider, circuit, width, logical_depth, transpiled_depth, depth_inflation,
  transpiled_gates {1q, 2q}, shots, qpu_seconds, success_prob, counts
"""
from __future__ import annotations

import time

from circuits import gate_counts, success_probability


def _base_record(provider, qc, shots):
    return {
        "provider": provider,
        "circuit": qc.name,
        "width": qc.num_qubits,
        "logical_depth": qc.depth(),
        "shots": shots,
    }


def _finish(rec, tqc, counts, qpu_seconds):
    rec["transpiled_depth"] = tqc.depth()
    rec["depth_inflation"] = round(rec["transpiled_depth"] / max(rec["logical_depth"], 1), 3)
    rec["transpiled_gates"] = gate_counts(tqc)
    rec["qpu_seconds"] = qpu_seconds
    rec["success_prob"] = success_probability(counts, rec["circuit"], rec["width"])
    rec["counts"] = dict(counts)
    return rec


class IBMRunner:
    """Superconducting, heavy-hex connectivity, billed by QPU time."""

    def __init__(self, backend_name: str | None = None):
        from qiskit_ibm_runtime import QiskitRuntimeService

        self.service = QiskitRuntimeService()
        self.backend = (
            self.service.backend(backend_name)
            if backend_name
            else self.service.least_busy(operational=True, simulator=False)
        )

    def run(self, qc, shots: int) -> dict:
        from qiskit import transpile
        from qiskit_ibm_runtime import SamplerV2

        rec = _base_record("ibm", qc, shots)
        tqc = transpile(qc, self.backend, optimization_level=3)
        sampler = SamplerV2(mode=self.backend)
        job = sampler.run([tqc], shots=shots)
        res = job.result()
        counts = res[0].data.c.get_counts() if hasattr(res[0].data, "c") else res[0].data.meas.get_counts()
        # Billed usage in seconds, from job metrics (this is what IBM meters).
        try:
            qpu_seconds = float(job.usage())
        except Exception:
            qpu_seconds = float(job.metrics().get("usage", {}).get("seconds", 0.0))
        return _finish(rec, tqc, counts, qpu_seconds)


class BraketRunner:
    """AWS Braket marketplace: same billing meter (task+shot) over different
    hardware. Pass an IonQ Forte ARN or a Rigetti ARN."""

    ARNS = {
        "braket_ionq_forte": "arn:aws:braket:us-east-1::device/qpu/ionq/Forte-1",
        "braket_rigetti": "arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q",
        "braket_iqm_garnet": "arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet",
        "braket_iqm_emerald": "arn:aws:braket:eu-north-1::device/qpu/iqm/Emerald",
    }

    # braket gate names -> qiskit names, for gates whose names differ
    _BRAKET_TO_QISKIT = {"cnot": "cx", "ccnot": "ccx", "phaseshift": "p",
                         "si": "sdg", "ti": "tdg", "v": "sx", "vi": "sxdg", "i": "id"}
    _QISKIT_STD = {"rx", "ry", "rz", "h", "x", "y", "z", "s", "t", "swap", "cz",
                   "iswap", "cx", "ccx", "p", "sdg", "tdg", "sx", "sxdg", "id"}

    def __init__(self, provider_key: str, device_arn: str | None = None):
        from braket.aws import AwsDevice

        self.provider_key = provider_key
        self.device = AwsDevice(device_arn or self.ARNS[provider_key])

    def _device_basis(self) -> list | None:
        """Qiskit-standard basis restricted to the device's supported gate set,
        so to_braket never emits a gate the QPU rejects (e.g. `v` on Rigetti)."""
        try:
            ops = self.device.properties.action["braket.ir.openqasm.program"].supportedOperations
        except Exception:
            return None
        basis = {self._BRAKET_TO_QISKIT.get(g.lower(), g.lower()) for g in ops}
        return sorted(basis & self._QISKIT_STD) or None

    # Families whose verbatim path is implemented+validated. Others fall back
    # to provider-side compilation (NOT safe for mirror-decay data: the
    # server compiler can cancel U·U† across the seam -- Quilc ignores
    # barriers -- leaving fidelity readout-dominated and flat in depth).
    # Per-family client-side transpile basis; every gate in it must map 1:1
    # onto the device's native gate set in _to_verbatim.
    _VERBATIM_FAMILIES = {
        "rigetti": ["rz", "sx", "x", "cz"],   # native: rx(k*pi/2) via sx/x, rz virtual, cz
        "iqm": ["r", "cz"],                    # native: prx(theta,phi) == qiskit r, cz
        "ionq": ["rz", "sx", "x", "rzz"],      # native: GPI/GPI2/ZZ; rz folded into phases
    }

    def _family(self) -> str:
        return self.device.arn.split("/")[-2]

    def _coupling_map(self):
        from qiskit.transpiler import CouplingMap

        conn = self.device.properties.paradigm.connectivity
        if getattr(conn, "fullyConnected", False):
            return None  # all-to-all (trapped ion): no routing needed
        g = conn.connectivityGraph
        return CouplingMap([(int(a), int(b)) for a, nbrs in g.items() for b in nbrs])

    def _to_verbatim(self, qc):
        """Compile client-side to physical qubits + native gates (rz, rx(π/2),
        rx(π), cz) and wrap in a verbatim box so the device compiler executes
        it exactly as written. Returns (physical qiskit circuit, braket circuit)."""
        import math

        from braket.circuits import Circuit
        from qiskit import transpile

        tqc = transpile(qc, basis_gates=self._VERBATIM_FAMILIES[self._family()],
                        coupling_map=self._coupling_map(),
                        optimization_level=1, seed_transpiler=7)
        ionq = self._family() == "ionq"
        # IonQ has no physical rz: fold each rz into the phase of subsequent
        # GPI/GPI2 pulses (verified numerically: SX.RZ(a)=RZ(a).GPI2(-a),
        # X.RZ(a)=RZ(a).GPI(-a); ZZ commutes; trailing rz drops at Z-measure).
        acc = {}
        inner = Circuit()
        for inst in tqc.data:
            name = inst.operation.name
            qs = [tqc.find_bit(q).index for q in inst.qubits]
            params = [float(p) for p in inst.operation.params]
            if name in ("measure", "barrier"):
                continue
            if name == "rz":
                if ionq:
                    acc[qs[0]] = acc.get(qs[0], 0.0) + params[0]
                else:
                    inner.rz(qs[0], params[0])
            elif name == "sx":
                if ionq:
                    inner.gpi2(qs[0], -acc.get(qs[0], 0.0))
                else:
                    inner.rx(qs[0], math.pi / 2)
            elif name == "x":
                if ionq:
                    inner.gpi(qs[0], -acc.get(qs[0], 0.0))
                else:
                    inner.rx(qs[0], math.pi)
            elif name == "r":
                inner.prx(qs[0], params[0], params[1])
            elif name == "rzz":
                inner.zz(qs[0], qs[1], params[0])
            elif name == "cz":
                inner.cz(qs[0], qs[1])
            else:
                raise ValueError(f"gate '{name}' not native; cannot go verbatim")
        return tqc, Circuit().add_verbatim_box(inner)

    @staticmethod
    def _logical_counts(counts: dict, tqc, width: int) -> dict:
        """Map physical-qubit counts back to logical order for grading.
        Braket keys are ascending-physical-qubit order; qiskit grading keys
        are c[w-1]..c[0]. Routing ancillas (ideal 0) are dropped."""
        phys_of_logical = (list(tqc.layout.final_index_layout())[:width]
                           if tqc.layout is not None else list(range(width)))
        active = sorted({tqc.find_bit(q).index for inst in tqc.data
                         if inst.operation.name not in ("measure", "barrier")
                         for q in inst.qubits})
        pos = {p: i for i, p in enumerate(active)}
        out = {}
        for key, n in counts.items():
            logical = [key[pos[p]] for p in phys_of_logical]
            out_key = "".join(reversed(logical))
            out[out_key] = out.get(out_key, 0) + n
        return out

    def run(self, qc, shots: int) -> dict:
        rec = _base_record(self.provider_key, qc, shots)
        if self._family() in self._VERBATIM_FAMILIES:
            tqc, braket_circuit = self._to_verbatim(qc)
            run_kwargs = {"disable_qubit_rewiring": True}
            rec["verbatim"] = True
        else:
            from qiskit_braket_provider.providers.adapter import to_braket

            tqc, braket_circuit = qc, to_braket(qc, basis_gates=self._device_basis())
            run_kwargs = {}
        t0 = time.time()
        task = self.device.run(braket_circuit, shots=shots, **run_kwargs)
        result = task.result()
        wall = time.time() - t0
        counts = dict(result.measurement_counts)
        if rec.get("verbatim"):
            counts = self._logical_counts(counts, tqc, qc.num_qubits)
        # Braket does not bill time; record wall time for information only.
        # Verbatim: gate/depth accounting from the physical circuit actually
        # executed; otherwise from the submitted logical circuit.
        return _finish(rec, tqc, counts, wall)


class IonQDirectRunner:
    """IonQ via native API (qiskit-ionq), billed per gate-shot (depth-adjusted)."""

    def __init__(self, backend_name: str = "qpu.forte-1"):
        from qiskit_ionq import IonQProvider

        provider = IonQProvider()  # reads IONQ_API_KEY
        self.backend = provider.get_backend(backend_name)

    def run(self, qc, shots: int) -> dict:
        from qiskit import transpile

        rec = _base_record("ionq_direct", qc, shots)
        tqc = transpile(qc, self.backend)
        t0 = time.time()
        job = self.backend.run(tqc, shots=shots)
        counts = job.get_counts()
        wall = time.time() - t0
        return _finish(rec, tqc, counts, wall)


def get_runner(provider: str, backend: str | None = None):
    if provider == "ibm":
        return IBMRunner(backend)
    if provider in BraketRunner.ARNS:
        return BraketRunner(provider, backend)
    if provider == "ionq_direct":
        return IonQDirectRunner(backend or "qpu.forte-1")
    raise ValueError(f"unknown provider: {provider}")
