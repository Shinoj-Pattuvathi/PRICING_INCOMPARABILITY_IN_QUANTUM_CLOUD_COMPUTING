"""make_figures.py -- regenerate the five thesis figures from the frozen
certification artefact.

Single source of truth: results/thesis_stats_CERTIFIED_2026-09-01.json
(S4, S5, S6, S8 read directly from the freeze). The decay figure (H3)
additionally reads the raw certified execution records (thesis_ibm.json,
thesis_braket_ionq_forte.json) because the freeze stores fit parameters,
not the per-cell fidelity means it was fit to; the same certification
filters are applied here (fair-mirror circuits only, hardware only --
these files contain no simulator records).

Usage:  python3 make_figures.py            # writes figures/fig_*.png
        python3 make_figures.py --stats results/thesis_stats_CERTIFIED_2026-09-01.json

Provenance discipline: every number drawn is read from the artefact at run
time; nothing is hand-typed. Output filenames match the ones embedded in
thesis/Thesis_Full_Draft.docx.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).parent
FIGDIR = HERE / "figures"
STATS_DEFAULT = HERE / "results" / "thesis_stats_CERTIFIED_2026-09-01.json"

# One palette, colourblind-safe (Okabe-Ito subset)
C = {"blue": "#0072B2", "orange": "#E69F00", "green": "#009E73",
     "red": "#D55E00", "purple": "#CC79A7", "grey": "#666666", "sky": "#56B4E9"}

PROVIDER_LABEL = {
    "ibm": "IBM (time meter)",
    "braket_rigetti": "Rigetti via Braket (volume)",
    "braket_iqm_garnet": "IQM Garnet via Braket (volume)",
    "braket_ionq_forte": "IonQ Forte via Braket (volume)",
    "azure_ionq": "IonQ Forte via Azure (gate-token)",
}


def savefig(fig, name):
    FIGDIR.mkdir(exist_ok=True)
    out = FIGDIR / name
    fig.savefig(out, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {out}")


# ---------------- H1: exchange-rate drift (S4) ----------------
def fig_exchange_H1(st):
    s4 = st["S4_exchange_H1"]
    pairs = list(s4.keys())
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    y = np.arange(len(pairs))[::-1]
    for yi, pair in zip(y, pairs):
        lo, hi, drift = s4[pair]["min"], s4[pair]["max"], s4[pair]["drift"]
        ax.plot([lo, hi], [yi, yi], lw=7, color=C["blue"], alpha=0.75,
                solid_capstyle="round")
        ax.scatter([lo, hi], [yi, yi], s=28, color=C["blue"], zorder=3)
        ax.annotate(f"drift {drift:g}x", ((lo * hi) ** 0.5, yi + 0.22),
                    ha="center", fontsize=9, color=C["grey"])
    ax.set_ylim(-0.55, len(pairs) - 0.35)
    ax.axvline(1.0, color=C["red"], lw=1.2, ls="--")
    ax.annotate("parity (rate = 1)", (1.0, -0.5), fontsize=8,
                color=C["red"], ha="center", va="bottom")
    ax.set_xscale("log")
    ax.set_yticks(y)
    ax.set_yticklabels(pairs)
    ax.set_xlabel("implied exchange rate between meters (log scale)")
    ax.set_title("H1: no meter pair sustains a constant exchange rate\n"
                 "(min-max span of implied rates across certified hardware records)",
                 fontsize=10)
    ax.grid(axis="x", which="both", alpha=0.25)
    savefig(fig, "fig_exchange_H1.png")


# ---------------- H2: same-machine channel contrast (S5) ----------------
def fig_flip_H2(st):
    s5 = st["S5_channel_flip_H2"]["ionq_azure/braket"]
    pts = sorted(s5["measured_points"], key=lambda p: -p["shots"])
    labels = [f"{p['shots']} shots\n({p['inferred_state'].split('(')[0].replace('_',' ')})"
              for p in pts]
    az = [p["azure_billed_usd"] for p in pts]
    bk = [p["braket_volume_usd"] for p in pts]
    ratios = [p["ratio_azure_over_braket"] for p in pts]
    x = np.arange(len(pts))
    w = 0.36
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    b1 = ax.bar(x - w / 2, az, w, color=C["orange"],
                label="Azure gate-token channel (invoiced)")
    b2 = ax.bar(x + w / 2, bk, w, color=C["blue"],
                label="Braket volume channel (exact ex ante)")
    for bars in (b1, b2):
        for r in bars:
            ax.annotate(f"${r.get_height():,.2f}", (r.get_x() + r.get_width() / 2,
                        r.get_height()), ha="center", va="bottom", fontsize=9)
    for xi, ratio, a, b in zip(x, ratios, az, bk):
        ax.annotate(f"ratio {ratio:g}x", (xi, max(a, b) + max(az + bk) * 0.09),
                    ha="center", fontsize=11, fontweight="bold", color=C["red"])
    ax.set_ylim(0, max(az + bk) * 1.30)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("price of the identical execution (USD)")
    ax.set_title("H2: one processor (IonQ Forte-1), one circuit, two channels\n"
                 "the cross-channel ratio moves on the shots dial alone",
                 fontsize=10)
    ax.legend(fontsize=8, loc="upper left")
    savefig(fig, "fig_flip_H2.png")


# ---------------- H3: fidelity decay (raw certified records) ----------------
def _wd(circ):
    # fair_w{w}_d{d}
    parts = circ.split("_")
    return int(parts[1][1:]), int(parts[2][1:])


def fig_decay_H3():
    def load_means(path):
        rows = json.loads((HERE / "results" / path).read_text())
        acc = defaultdict(list)
        for r in rows:
            if not r["circuit"].startswith("fair"):
                continue
            acc[_wd(r["circuit"])].append(r["success_prob"])
        return {k: float(np.mean(v)) for k, v in acc.items()}

    ibm = load_means("thesis_ibm.json")
    ion = load_means("thesis_braket_ionq_forte.json")
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    colors = {3: C["blue"], 8: C["green"], 12: C["purple"]}
    for wdt in sorted({w for w, _ in ibm}):
        cells = sorted((d, f) for (w, d), f in ibm.items() if w == wdt)
        ax.plot([d for d, _ in cells], [f for _, f in cells], "o-",
                color=colors.get(wdt, C["grey"]), label=f"IBM, width {wdt}")
    for wdt in sorted({w for w, _ in ion}):
        cells = sorted((d, f) for (w, d), f in ion.items() if w == wdt)
        ax.plot([d for d, _ in cells], [f for _, f in cells], "s--",
                color=C["orange"], label=f"IonQ Forte (verbatim), width {wdt}")
    ax.set_yscale("log")
    ax.set_xlabel("logical depth d")
    ax.set_ylabel("success probability (log scale)")
    ax.set_title("H3: fidelity decay across width and depth\n"
                 "primary machine (solid) with trapped-ion replication (dashed); "
                 "widths decay non-additively (gamma > 0, S2)", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25, which="both")
    savefig(fig, "fig_decay_H3.png")


# ---------------- H4a: delivered value QFC (S6) ----------------
def fig_qfc_H4(st):
    table = st["S6_qfc_H4"]["table"]
    cell = [r for r in table if r["circuit"] == "fair_w8_d16"]
    cell.sort(key=lambda r: r["QFC"])
    labels = [PROVIDER_LABEL.get(r["provider"], r["provider"]) for r in cell]
    vals = [r["QFC"] for r in cell]
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    bars = ax.barh(labels, vals, color=C["green"], alpha=0.85)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:g}", (v, b.get_y() + b.get_height() / 2),
                    va="center", ha="left", fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("QFC: correct answers per US dollar (log scale)")
    ax.set_title("H4: delivered value for the identical circuit (fair_w8_d16)\n"
                 "under each machine's native meter -- a spread with no going rate",
                 fontsize=10)
    ax.grid(axis="x", which="both", alpha=0.25)
    savefig(fig, "fig_qfc_H4.png")


# ---------------- H4b: heuristic regret (S8) ----------------
def fig_regret_H4(st):
    s8 = st["S8_regret_H4"]
    rules = ["always_cheapest_pershot", "always_time_meter", "always_ionq_braket"]
    names = ["cheapest per-shot rule", "always time meter", "always premium hardware"]
    opt = [s8[r]["pct_optimal"] for r in rules]
    reg = [s8[r]["mean_regret"] for r in rules]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.2, 4.2))
    a1.bar(names, opt, color=[C["blue"], C["sky"], C["red"]])
    for i, v in enumerate(opt):
        a1.annotate(f"{v:g}%", (i, v), ha="center", va="bottom", fontsize=9)
    a1.set_ylabel("share of workloads at the optimum (%)")
    a1.set_ylim(0, 100)
    a1.set_title("how often each fixed rule\nmatches the oracle router", fontsize=9)
    a1.tick_params(axis="x", labelsize=7, rotation=12)
    a2.bar(names, reg, color=[C["blue"], C["sky"], C["red"]])
    for i, v in enumerate(reg):
        a2.annotate(f"{v:g}x", (i, v), ha="center", va="bottom", fontsize=9)
    a2.set_yscale("log")
    a2.set_ylabel("mean regret vs oracle (x, log scale)")
    a2.set_title("mean overpayment factor\nof each fixed rule", fontsize=9)
    a2.tick_params(axis="x", labelsize=7, rotation=12)
    fig.suptitle("H4: no tested procurement heuristic is near-optimal across the workload space",
                 fontsize=10)
    fig.tight_layout()
    savefig(fig, "fig_regret_H4.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", default=str(STATS_DEFAULT),
                    help="frozen certification artefact (default: 2026-09-01 freeze)")
    args = ap.parse_args()
    blob = json.loads(pathlib.Path(args.stats).read_text())
    st = blob["stats"] if "stats" in blob else blob
    print(f"figures from {args.stats}")
    fig_exchange_H1(st)
    fig_flip_H2(st)
    fig_decay_H3()
    fig_qfc_H4(st)
    fig_regret_H4(st)
    print("done: 5 figures")


if __name__ == "__main__":
    main()
