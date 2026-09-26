#!/usr/bin/env python3
"""Analyze results from 2+ providers and demonstrate pricing incomparability.

Produces:
 1. A per-workload table: measured width/depth/fidelity/connectivity-overhead,
    native-unit cost, and cost per successful shot.
 2. The demonstration table: implied 'exchange rates' between providers
    per workload. If a single conversion between billing units existed,
    these ratios would be constant. They are not.
 3. A chart: cost-per-successful-shot by workload and provider, plus the
    exchange-rate instability panel.

Usage: python analyze.py results/*.json [-o report]
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
from collections import defaultdict

from pricing import DEFAULT_RATES, cost_for_result, cost_per_successful_shot

try:
    import yaml
    _cfg = pathlib.Path(__file__).parent / "config.yaml"
    RATES = yaml.safe_load(_cfg.read_text()).get("rates", DEFAULT_RATES) if _cfg.exists() else DEFAULT_RATES
except Exception:
    RATES = DEFAULT_RATES


def load_results(paths):
    rows = []
    for p in paths:
        rows.extend(json.loads(pathlib.Path(p).read_text()))
    return rows


def enrich(rows):
    for r in rows:
        r["cost_usd"] = cost_for_result(r, RATES)
        r["cost_per_success"] = cost_per_successful_shot(r, RATES)
    return rows


def print_measurement_table(rows):
    print("\n=== MEASURED PARAMETERS AND COSTS ===\n")
    hdr = (f"{'circuit':20s} {'provider':18s} {'w':>3s} {'depth':>11s} "
           f"{'conn x':>7s} {'fidelity':>8s} {'cost $':>9s} {'$/success':>10s}")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda x: (x["circuit"], x["provider"])):
        depth = f"{r['logical_depth']}->{r['transpiled_depth']}"
        print(f"{r['circuit']:20s} {r['provider']:18s} {r['width']:>3d} {depth:>11s} "
              f"{r['depth_inflation']:>7.2f} {r['success_prob']:>8.3f} "
              f"{r['cost_usd']:>9.2f} {r['cost_per_success']:>10.4f}")


def exchange_rate_table(rows):
    """The core demonstration. For providers A and B, the implied exchange
    rate on workload W is  cps_A(W) / cps_B(W).  A usable price-sheet
    conversion requires this to be (approximately) workload-invariant."""
    by_circuit = defaultdict(dict)
    for r in rows:
        by_circuit[r["circuit"]][r["provider"]] = r["cost_per_success"]

    providers = sorted({r["provider"] for r in rows})
    pairs = list(itertools.combinations(providers, 2))
    print("\n=== IMPLIED EXCHANGE RATES (cost-per-success ratio A/B) ===")
    print("If billing units were convertible, each column would be constant.\n")
    print(f"{'circuit':20s} " + " ".join(f"{a[:8]}/{b[:8]:>10s}" for a, b in pairs))
    spread = defaultdict(list)
    for c, provs in sorted(by_circuit.items()):
        cells = []
        for a, b in pairs:
            if a in provs and b in provs and provs[b] > 0:
                ratio = provs[a] / provs[b]
                spread[(a, b)].append(ratio)
                cells.append(f"{ratio:>19.2f}")
            else:
                cells.append(f"{'--':>19s}")
        print(f"{c:20s} " + " ".join(cells))

    print("\n--- Verdict ---")
    for (a, b), ratios in spread.items():
        if len(ratios) >= 2:
            lo, hi = min(ratios), max(ratios)
            factor = hi / lo if lo > 0 else float("inf")
            print(f"{a} vs {b}: implied rate varies {lo:.2f}x to {hi:.2f}x "
                  f"across workloads -- a {factor:.1f}-fold instability.")
    print("\nA constant would mean the units are commensurable. The variation is")
    print("the measured signature of pricing incomparability: which provider is")
    print("'cheaper' depends on the workload's position in (width, depth,")
    print("fidelity, connectivity) space -- exactly because those variables are")
    print("non-separable and interact differently with each billing meter.")
    return by_circuit, pairs, spread


def make_chart(rows, by_circuit, spread, outstem):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    providers = sorted({r["provider"] for r in rows})
    circuits = sorted(by_circuit.keys())
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

    ax = axes[0]
    x = np.arange(len(circuits))
    w = 0.8 / max(len(providers), 1)
    for i, p in enumerate(providers):
        vals = [by_circuit[c].get(p, np.nan) for c in circuits]
        ax.bar(x + i * w, vals, w, label=p)
    ax.set_yscale("log")
    ax.set_xticks(x + w * (len(providers) - 1) / 2)
    ax.set_xticklabels(circuits, rotation=40, ha="right", fontsize=8)
    ax.set_ylabel("cost per successful shot (USD, log)")
    ax.set_title("Same workloads, incompatible billing meters")
    ax.legend(fontsize=8)

    ax = axes[1]
    for (a, b), ratios in spread.items():
        if len(ratios) >= 2:
            ax.plot(range(len(ratios)), sorted(ratios), marker="o",
                    label=f"{a[:12]}/{b[:12]}")
    ax.set_yscale("log")
    ax.set_xlabel("workloads (sorted by ratio)")
    ax.set_ylabel("implied exchange rate (log)")
    ax.set_title("A convertible unit would be a flat line")
    ax.legend(fontsize=8)
    fig.tight_layout()
    out = f"{outstem}.png"
    fig.savefig(out, dpi=150)
    print(f"\nChart saved to {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", default="incomparability_report")
    args = ap.parse_args()

    rows = enrich(load_results(args.files))
    if not rows:
        raise SystemExit("no results found")
    print_measurement_table(rows)
    by_circuit, pairs, spread = exchange_rate_table(rows)
    make_chart(rows, by_circuit, spread, args.out)


if __name__ == "__main__":
    main()
