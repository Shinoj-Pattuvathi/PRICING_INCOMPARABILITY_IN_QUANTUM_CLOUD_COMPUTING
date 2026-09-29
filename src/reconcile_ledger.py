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

Each row also carries cash_usd and cash_source: the part of the metered actual
that was paid in cash rather than absorbed by promotional credit, and where
that figure comes from:

  invoice_G182569155  Azure: invoiced in full, no credit applied (invoice
                      G182569155, rate_archive/invoices_redacted/).
  aws_pool_then_card  Braket: the USD 120.00 AWS promotional pool is allocated
                      across the Braket phase rows in ledger order (phases 2,
                      3, 5) until exhausted; the excess was charged to a card.
                      Per billing console, statement not deposited.
  free_tier           IBM Open Plan: no meter ran, no cash.
  n/a                 pooled ledger rows with no execution records.

Usage:  python3 reconcile_ledger.py
        python3 reconcile_ledger.py --reconcile-invoice   (self-check only)
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

# ---- cash provenance constants (each with its source) ----
AWS_CREDIT_POOL_USD = 120.00        # AWS billing console, thesis Appendix F.4; statement not deposited
AZURE_CREDIT_APPLIED_USD = 0.00     # invoice G182569155 line "Azure Credit 0", rate_archive/invoices_redacted/G182569155_redacted.pdf
AZURE_INVOICE = dict(number="G182569155", date="2026-09-09", period="2026-08-16..2026-08-31",
                     gbp_net=151.70, gbp_vat=30.34, gbp_gross=182.04,
                     credit_applied_gbp=0.00, payment="personal card")
AQT_BILLED = (168.2, 25.79)         # AQT counts in the two platform billing payloads (phases 4 and 6)
AQT_GBP_UNIT_PRICE = 0.78201        # GBP per AQT on this account (same payloads)

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


def reconcile_invoice():
    """Self-check: the two platform billing payloads must reproduce the invoice
    subtotal to the penny: (168.2 + 25.79) AQT x GBP 0.78201 = GBP 151.70."""
    derived = sum(AQT_BILLED) * AQT_GBP_UNIT_PRICE
    ok = abs(derived - AZURE_INVOICE["gbp_net"]) < 0.01
    line = (f"invoice identity: ({AQT_BILLED[0]} + {AQT_BILLED[1]}) AQT x GBP {AQT_GBP_UNIT_PRICE} "
            f"= GBP {derived:.3f}  vs  invoice {AZURE_INVOICE['number']} subtotal GBP "
            f"{AZURE_INVOICE['gbp_net']:.2f}  -> {'OK' if ok else 'MISMATCH'}")
    print(line)
    if not ok:
        sys.exit("invoice reconciliation FAILED: " + line)
    return derived


def main():
    if "--reconcile-invoice" in sys.argv[1:]:
        reconcile_invoice()
        return
    if not LEDGER.exists():
        sys.exit(f"no ledger at {LEDGER}")
    reconcile_invoice()
    groups = load_records()
    ledger_rows, est, adj, jobs, status_actual = load_ledger()

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
            cash_usd=None, cash_source=None,   # derived below
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
            cross_check_usd=None, cross_check_delta_usd=None,
            cash_usd=0.0, cash_source="n/a",
            est_note="pooled ledger row; metered cost attributed to the phase rows above",
            detail={"note": "adjustment-only ledger key"},
        ))

    # azure rows are per-phase but share one record file; split their estimates
    # correctly by re-keying phase-wise (already done via rec['phase']).

    # cash is DERIVED per row, not hard-coded: Azure was invoiced in full
    # (no credit applied); Braket drew down the AWS pool in ledger order
    # (phases 2, 3, 5) with the excess charged to a card; IBM ran free.
    pool = AWS_CREDIT_POOL_USD
    for r in out:
        if r["cash_source"] is not None:      # pooled rows already set
            continue
        a = r["actual_metered_usd"] or 0.0
        if r["channel"] == "azure":
            r["cash_usd"] = round(a - AZURE_CREDIT_APPLIED_USD, 4)
            r["cash_source"] = f"invoice_{AZURE_INVOICE['number']}"
            r["detail"]["credit_applied_usd"] = AZURE_CREDIT_APPLIED_USD
        elif r["channel"] == "braket":
            covered = min(pool, a)
            pool -= covered
            r["cash_usd"] = round(a - covered, 4)
            r["cash_source"] = "aws_pool_then_card"
            r["detail"]["credit_applied_usd"] = round(covered, 4)
        elif r["channel"] == "ibm":
            r["cash_usd"], r["cash_source"] = 0.0, "free_tier"
        else:
            r["cash_usd"], r["cash_source"] = 0.0, "n/a"
    aws_credit_applied = round(AWS_CREDIT_POOL_USD - pool, 4)

    cols = ["phase", "provider", "channel", "meter", "barrier", "records",
            "tasks_billed", "shots", "est_usd", "adjustments_usd",
            "net_committed_usd", "actual_metered_usd", "actual_source",
            "cross_check_usd", "cross_check_delta_usd", "cash_usd", "cash_source",
            "est_note"]
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

    # credits as RECORDED in ledger.csv (credits_covered_adjustment rows) vs
    # credit ACTUALLY applied by the platforms
    recorded = defaultdict(float)
    for lr in ledger_rows:
        if lr["status"] == "credits_covered_adjustment":
            prov = lr["provider"]
            ch = "braket" if prov.startswith("braket") else ("azure" if prov.startswith("azure") else prov)
            recorded[ch] += float(lr["est_usd"])
    recorded = {k: round(v, 2) for k, v in sorted(recorded.items())}
    applied = {"azure": AZURE_CREDIT_APPLIED_USD, "braket": aws_credit_applied}
    credits_recorded_vs_applied = dict(
        recorded_in_ledger_usd=recorded,
        credit_actually_applied_usd=applied,
        note="The two azure_ionq credits_covered_adjustment rows in ledger.csv recorded an "
             "expectation that promotional credit would absorb the gate-token bills; invoice "
             f"{AZURE_INVOICE['number']} contradicts it (Azure Credit 0), so they are not honoured "
             "here and are retained verbatim in ledger.csv.",
        aws_basis="per billing console, statement not deposited",
    )

    cash_total = sum(r["cash_usd"] for r in out)
    # estimate basis (thesis Appendix F.2): what the ledger's credit rows assumed
    metered_est_basis = round(-sum(recorded.values()), 2)
    cash_est_basis = round(metered_est_basis - AWS_CREDIT_POOL_USD - AZURE_CREDIT_APPLIED_USD, 2)

    summary = dict(
        generated_from=str(LEDGER.name),
        primary_record_unmodified=True,
        gbp_usd_rate=GBP_USD,
        metered_actual_total_usd=round(total, 2),
        metered_actual_by_meter={k: round(v, 2) for k, v in sorted(by_meter.items())},
        committed_estimate_total_usd=round(total_est, 2),
        net_committed_after_adjustments_usd=round(total_net, 2),
        invoice_vs_ledger_discrepancies=discrepancies,
        credits_recorded_vs_applied=credits_recorded_vs_applied,
        cash_total_usd=round(cash_total, 2),
        cash_basis_rate=dict(metered_usd=round(total, 2),
                             credits_applied_usd=round(sum(applied.values()), 2),
                             cash_usd=round(cash_total, 2),
                             basis="metered actuals: Braket recomputed under the archived rate "
                                   "card, Azure from the invoice payloads"),
        cash_basis_estimate=dict(metered_usd=metered_est_basis,
                                 credits_applied_usd=round(AWS_CREDIT_POOL_USD + AZURE_CREDIT_APPLIED_USD, 2),
                                 cash_usd=cash_est_basis,
                                 basis="ledger.csv credits_covered_adjustment rows (estimate basis, "
                                       "thesis Appendix F.2)"),
        cash_total_gbp_azure_gross=AZURE_INVOICE["gbp_gross"],
        azure_vat_gbp=AZURE_INVOICE["gbp_vat"],
        azure_invoice=AZURE_INVOICE,
        cash_note="Azure consumption was invoiced in full (G182569155, no credit applied) and paid "
                  "personally; Braket consumption drew down the $120.00 AWS pool with the remainder "
                  "charged personally. Cash figures are net of UK VAT; the Azure invoice carried "
                  "\u00a330.34 VAT.",
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
    print(f"  cash outlay            ${cash_total:.2f}  (rate basis, net of VAT; "
          f"estimate basis ${cash_est_basis:.2f})")
    print(f"    Azure invoice {AZURE_INVOICE['number']}: GBP {AZURE_INVOICE['gbp_net']:.2f} net "
          f"+ GBP {AZURE_INVOICE['gbp_vat']:.2f} VAT = GBP {AZURE_INVOICE['gbp_gross']:.2f} gross, "
          f"credit applied GBP {AZURE_INVOICE['credit_applied_gbp']:.2f}")
    print(f"    AWS pool applied       ${aws_credit_applied:.2f} of ${AWS_CREDIT_POOL_USD:.2f} "
          f"(per billing console, statement not deposited)")
    print(f"\nwrote {OUT_CSV}\nwrote {OUT_JSON}")


if __name__ == "__main__":
    main()
