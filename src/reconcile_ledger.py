#!/usr/bin/env python3
"""reconcile_ledger.py -- DERIVED convenience view over results/ledger.csv.

The primary record is results/ledger.csv. It is an ex-ante COMMITMENT log:
thesis_run.py appends one row per job BEFORE submission, so its est_usd
column can only ever hold the estimate that existed at submission time. That
design is deliberate and is described in the thesis (Appendix E); the gap
between the estimate and the invoice that arrived later is itself the
settlement-layer finding (Chapter 5.3, 5.6). This script DOES NOT MODIFY IT.

What this script adds is a read-only reconciliation table pairing each phase's
committed estimate with its realised metered cost, and naming the provenance
of every actual, of which there are exactly three kinds:

  invoice_payload   the platform's own billing object, stored on the execution
                    record (Azure returns amountBilled x unitPrice in GBP);
                    parsed by thesis_analysis.azure_billed_usd, converted at
                    the pinned GBP_USD rate.
  ledger_status     an actual recorded only inside a ledger status string of
                    the form billing_correction_actual_<amount>, where the
                    row's est_usd carries the DELTA, not the bill.
  recomputed        no stored actual exists; the cost is reconstructed from the
                    execution records under the frozen rate card. Exact for
                    volume meters, whose tariff (task + shots) is fully
                    determined ex ante -- this is a derivation, not a receipt.
  free_tier         no meter ran (IBM Open Plan). Time-meter dollar figures for
                    IBM elsewhere in the analysis are COUNTERFACTUAL prices,
                    not bills, and are deliberately excluded here.

Usage:  python3 reconcile_ledger.py
Writes: results/ledger_reconciliation.csv
        results/ledger_reconciliation.json
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sys
from collections import defaultdict

from thesis_analysis import RATES, GBP_USD, azure_billed_usd

HERE = pathlib.Path(__file__).parent
RESULTS = HERE / "results"
LEDGER = RESULTS / "ledger.csv"
OUT_CSV = RESULTS / "ledger_reconciliation.csv"
OUT_JSON = RESULTS / "ledger_reconciliation.json"

# provider -> (channel, meter, per-shot rate key or None)
PROVIDER_META = {
    "ibm":                ("ibm",    "time (QPU seconds)",   None),
    "braket_rigetti":     ("braket", "volume (task+shots)",  "vol_shot_rigetti"),
    "braket_iqm_garnet":  ("braket", "volume (task+shots)",  "vol_shot_iqm"),
    "braket_ionq_forte":  ("braket", "volume (task+shots)",  "vol_shot"),
    "azure_ionq":         ("azure",  "gate-token (AQT)",     None),
}

RECORD_FILES = [
    (RESULTS / "thesis_ibm.json",               "post-barrier"),
    (RESULTS / "thesis_braket_rigetti.json",    "post-barrier"),
    (RESULTS / "thesis_braket_iqm_garnet.json", "post-barrier"),
    (RESULTS / "thesis_braket_ionq_forte.json", "post-barrier"),
    (RESULTS / "thesis_azure_ionq.json",        "mixed"),
    (RESULTS / "pre_barrier/thesis_ibm.json",            "pre-barrier (quarantined)"),
    (RESULTS / "pre_barrier/thesis_braket_rigetti.json", "pre-barrier (quarantined)"),
]


def load_records():
    """Every execution record, tagged with its barrier provenance."""
    groups = defaultdict(list)
    for path, barrier in RECORD_FILES:
        if not path.exists():
            print(f"  ! missing record file: {path}", file=sys.stderr)
            continue
        for rec in json.loads(path.read_text()):
            key = (str(rec.get("phase", "?")), rec.get("provider", "?"), barrier)
            groups[key].append(rec)
    return groups


def load_ledger():
    """Ledger rows split into submissions, adjustments, and status-string actuals."""
    rows = list(csv.DictReader(LEDGER.open()))
    est = defaultdict(float)          # (phase, provider) -> committed estimate
    adj = defaultdict(float)          # (phase, provider) -> adjustment total
    jobs = defaultdict(int)
    status_actual = {}                # (phase, provider) -> actual from status string
    for r in rows:
        k = (r["phase"], r["provider"])
        amt = float(r["est_usd"])
        m = re.match(r"billing_correction_actual_([\d.]+)$", r["status"])
        if m:
            status_actual[k] = float(m.group(1))
            adj[k] += amt            # the row carries the delta, not the bill
        elif r["status"] == "submitted":
            est[k] += amt
            jobs[k] += 1
        else:
            adj[k] += amt            # credits / failed / cancelled adjustments
    return rows, est, adj, jobs, status_actual


def volume_cost(recs, shot_rate_key):
    tasks = len(recs)
    shots = sum(int(r.get("shots", 0)) for r in recs)
    usd = tasks * RATES["vol_task"] + shots * RATES[shot_rate_key]
    return tasks, shots, round(usd, 4)


def azure_actual(recs, key, status_actual):
    """Prefer the platform's own invoice payload; fall back to the ledger string."""
    for rec in recs:
        usd, detail = azure_billed_usd(rec)
        if usd is not None:
            return round(usd, 4), "invoice_payload", detail
    if key in status_actual:
        return status_actual[key], "ledger_status", {"note": "parsed from status string"}
    return None, "unknown", {}


def main():
    if not LEDGER.exists():
        sys.exit(f"no ledger at {LEDGER}")
    groups = load_records()
    _, est, adj, jobs, status_actual = load_ledger()

    out = []
    est_claimed = set()   # a (phase, provider) estimate is not separable by
                          # barrier state; attribute it to the first row only
    for (phase, provider, barrier), recs in sorted(groups.items()):
        channel, meter, shot_key = PROVIDER_META.get(provider, ("?", "?", None))
        key = (phase, provider)
        detail = {}

        if provider == "ibm":
            tasks, shots = len(recs), sum(int(r.get("shots", 0)) for r in recs)
            actual, source = 0.0, "free_tier"
            detail = {"note": "IBM Open Plan; no invoice. Time-meter prices "
                              "elsewhere in the analysis are counterfactual."}
        elif shot_key:
            tasks, shots, actual = volume_cost(recs, shot_key)
            source = "recomputed"
            detail = {"tariff": f"{RATES['vol_task']}/task + {RATES[shot_key]}/shot",
                      "exact_ex_ante": True}
        elif provider == "azure_ionq":
            tasks, shots = len(recs), sum(int(r.get("shots", 0)) for r in recs)
            actual, source, detail = azure_actual(recs, key, status_actual)
        else:
            tasks, shots, actual, source = len(recs), 0, None, "unknown"

        first = key not in est_claimed
        est_claimed.add(key)
        e = round(est.get(key, 0.0), 4) if first else 0.0
        a = round(adj.get(key, 0.0), 4) if first else 0.0

        # where an actual exists in BOTH the invoice payload and a ledger
        # status string, report the discrepancy rather than silently picking one
        cross = status_actual.get(key) if source == "invoice_payload" else None
        delta = round(actual - cross, 4) if (cross is not None and actual is not None) else None

        out.append(dict(
            phase=phase, provider=provider, channel=channel, meter=meter,
            barrier=barrier, records=len(recs), tasks_billed=tasks, shots=shots,
            est_usd=e,
            adjustments_usd=a,
            net_committed_usd=round(e + a, 4),
            actual_metered_usd=actual,
            actual_source=source,
            cross_check_usd=cross,
            cross_check_delta_usd=delta,
            cash_usd=0.0,
            est_note="" if first else f"estimate carried on the {phase}/{provider} row above",
            detail=detail,
        ))

    # ledger keys with no execution records (pooled credit rows, orphaned
    # adjustments) must still appear, or the reconciliation silently loses money
    seen = {(r["phase"], r["provider"]) for r in out}
    for key in sorted(set(list(est) + list(adj))):
        if key in seen:
            continue
        phase, provider = key
        channel, meter, _ = PROVIDER_META.get(provider, ("pooled", "n/a", None))
        out.append(dict(
            phase=phase, provider=provider, channel=channel, meter=meter,
            barrier="n/a", records=0, tasks_billed=0, shots=0,
            est_usd=round(est.get(key, 0.0), 4),
            adjustments_usd=round(adj.get(key, 0.0), 4),
            net_committed_usd=round(est.get(key, 0.0) + adj.get(key, 0.0), 4),
            actual_metered_usd=None, actual_source="no_execution_records",
            cross_check_usd=None, cross_check_delta_usd=None, cash_usd=0.0,
            est_note="pooled ledger row; metered cost attributed to the phase rows above",
            detail={"note": "adjustment-only ledger key"},
        ))

    # azure rows are per-phase but share one record file; split their estimates
    # correctly by re-keying phase-wise (already done via rec['phase']).

    cols = ["phase", "provider", "channel", "meter", "barrier", "records",
            "tasks_billed", "shots", "est_usd", "adjustments_usd",
            "net_committed_usd", "actual_metered_usd", "actual_source",
            "cross_check_usd", "cross_check_delta_usd", "cash_usd", "est_note"]
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in out:
            w.writerow({c: r[c] for c in cols})

    known = [r for r in out if r["actual_metered_usd"] is not None]
    by_meter = defaultdict(float)
    for r in known:
        by_meter[r["meter"]] += r["actual_metered_usd"]
    total = sum(r["actual_metered_usd"] for r in known)
    total_est = sum(r["est_usd"] for r in out)
    total_net = sum(r["net_committed_usd"] for r in out)
    discrepancies = [dict(phase=r["phase"], provider=r["provider"],
                          invoice=r["actual_metered_usd"],
                          ledger_status=r["cross_check_usd"],
                          delta=r["cross_check_delta_usd"])
                     for r in out if r.get("cross_check_delta_usd")]
    TOL = 0.01   # sub-cent gaps are GBP->USD rounding, not billing disputes
    for d in discrepancies:
        d["material"] = abs(d["delta"]) >= TOL

    summary = dict(
        generated_from=str(LEDGER.name),
        primary_record_unmodified=True,
        gbp_usd_rate=GBP_USD,
        metered_actual_total_usd=round(total, 2),
        metered_actual_by_meter={k: round(v, 2) for k, v in sorted(by_meter.items())},
        committed_estimate_total_usd=round(total_est, 2),
        net_committed_after_adjustments_usd=round(total_net, 2),
        invoice_vs_ledger_discrepancies=discrepancies,
        cash_total_usd=0.0,
        cash_note="all metered consumption absorbed by Azure and AWS promotional "
                  "credit pools; see ledger credits_covered_adjustment rows",
        rows=out,
    )
    OUT_JSON.write_text(json.dumps(summary, indent=2))

    w1, w2 = 5, 20
    print(f"\nDERIVED RECONCILIATION  (primary ledger.csv unmodified)\n")
    print(f"{'ph':>{w1}} {'provider':{w2}} {'meter':22} {'est $':>8} {'adj $':>8} "
          f"{'net $':>8} {'actual $':>9}  source")
    for r in out:
        a = "n/a" if r["actual_metered_usd"] is None else f"{r['actual_metered_usd']:.2f}"
        print(f"{r['phase']:>{w1}} {r['provider']:{w2}} {r['meter']:22} "
              f"{r['est_usd']:>8.2f} {r['adjustments_usd']:>8.2f} "
              f"{r['net_committed_usd']:>8.2f} {a:>9}  {r['actual_source']}")
    print(f"\n  metered actual total   ${total:.2f}")
    for k, v in sorted(by_meter.items()):
        print(f"    {k:22} ${v:.2f}")
    print(f"  committed estimate     ${total_est:.2f}  (gross, incl. failed submissions)")
    print(f"  net after adjustments  ${total_net:.2f}")
    if discrepancies:
        print("\n  invoice payload vs ledger status string:")
        for d in discrepancies:
            tag = "MATERIAL" if d["material"] else "within rounding tolerance"
            print(f"    phase {d['phase']} {d['provider']}: ${d['invoice']:.4f} "
                  f"vs ${d['ledger_status']:.2f}  delta ${d['delta']:+.4f}  [{tag}]")
    print(f"  cash outlay            $0.00  (credit pools)")
    print(f"\nwrote {OUT_CSV}\nwrote {OUT_JSON}")


if __name__ == "__main__":
    main()
