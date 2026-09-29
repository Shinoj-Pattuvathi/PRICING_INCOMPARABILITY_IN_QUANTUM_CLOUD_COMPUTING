#!/usr/bin/env python3
"""thesis_analysis.py -- the S1-S8 statistical pipeline mapped to H1-H4.

Input : results/thesis_*.json (from thesis_run.py) or measure_all_*.json
Output: results/thesis_stats.json + figures figs/S*.png + console report

Analyses (pre-registered order, see Methodology A6):
  S1 descriptives + Wilson CIs          -> data quality
  S2 decay fit F = A exp(-(aw+bd+g wd)) -> H3 (gamma != 0, LRT vs additive null)
  S3 between-rep variance               -> reproducibility bound
  S4 exchange-rate drift (all meters)   -> H1
  S5 channel-flip (Rigetti, IonQ)       -> H2
  S6 QFC + Kendall tau                  -> H4
  S7 mitigation pricing (if data)       -> supplementary
  S8 heuristic-regret simulation        -> H4

Usage: python3 thesis_analysis.py [results/thesis_*.json ...]
"""
from __future__ import annotations

import glob
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
FIGS = HERE / "figs"

# ---------------- meters (rates duplicated from pricing/azure modules) ----
RATES = {
    "time_usd_per_s": 1.60,
    "vol_task": 0.30, "vol_shot": 0.08,            # Braket premium (IonQ)
    "vol_shot_rigetti": 0.000425,                  # verified 2026-08-16
    "vol_shot_iqm": 0.00145,                       # verified 2026-08-16
    "gate_1q": 0.00022, "gate_2q": 0.000975, "gate_min": 1.00,
    # Azure IonQ FORTE constants -- DEVICE-SPECIFIC (verified vs Microsoft
    # pricing.md 2026-09-01). The earlier build applied ARIA constants
    # (0.000220/0.000975, minima 12.4166/97.50) to Forte executions; that
    # error produced the spurious "floor billed at 2x documented" and "3.56x
    # compilation factor" readings. Under Forte constants BOTH invoices are
    # exact tariff floors: phase-4 $168.36 ~= min_mit_on $168.195 (job ran
    # under the platform DEFAULT, mitigation ON -- runner flag not
    # transmitted); phase-6 $25.81 ~= min_mit_off $25.7899.
    "aqt_1q": 0.0001645, "aqt_2q": 0.001121,
    "aqt_min_off": 25.7899, "aqt_min_on": 168.195,
    "azure_time_per_10ms": 0.02,
}

def price_all_meters(rec):
    """Price one record under every meter (counterfactuals flagged by caller)."""
    g = rec["transpiled_gates"]; S = rec["shots"]
    out = {
        "volume": RATES["vol_task"] + S * RATES["vol_shot"],
        "gate": max(S * (g["1q"] * RATES["gate_1q"] + g["2q"] * RATES["gate_2q"]), RATES["gate_min"]),
        "aqt_off": max(S * (g["1q"] * RATES["aqt_1q"] + g["2q"] * RATES["aqt_2q"]), RATES["aqt_min_off"]),
    }
    # time meter: ONLY billed QPU seconds qualify (IBM job.usage()). Braket
    # records carry queue-inclusive wall-clock in qpu_seconds -- informational,
    # never a billed quantity; pricing it as time fabricates a meter.
    if rec.get("qpu_seconds") and rec.get("provider") == "ibm":
        out["time"] = rec["qpu_seconds"] * RATES["time_usd_per_s"]
    return out

# ---------------- io ----------------
def load(paths):
    rows = []
    for pth in paths:
        stem = pathlib.Path(pth).stem
        inferred = stem.replace("thesis_", "").replace("measure_all_", "")
        for r in json.loads(pathlib.Path(pth).read_text()):
            if "success_prob" in r and "transpiled_gates" in r:
                r.setdefault("rep", 1)
                r.setdefault("provider", inferred)
                rows.append(r)
    return rows

def wd(rec):
    """(w, d) from circuit name fair_w{w}_d{d} / mirror... / ghz_w{w}."""
    name = rec["circuit"]
    w = rec["width"]
    d = 0
    if "_d" in name:
        d = int(name.split("_d")[-1].split("_")[0])
    return w, d

def above_floor(rec) -> bool:
    """Drop rule (pre-registered addendum 2026-08-17): a cell enters decay fits
    only if its fidelity is distinguishable from random guessing -- more than
    3 sigma above the 1/2^w floor at its shot count. Floor cells stay in the
    pricing analyses (a dead cell is a real buyer outcome), out of the fits."""
    w = rec["width"]
    floor = 1 / 2**w
    n = max(rec["shots"], 1)
    return rec["success_prob"] > floor + 3 * math.sqrt(floor * (1 - floor) / n)


# ---------------- S1 ----------------
def wilson(p, n, z=1.96):
    if n == 0: return (0, 1)
    den = 1 + z*z/n
    ctr = (p + z*z/(2*n)) / den
    hw = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / den
    return (max(ctr-hw, 0), min(ctr+hw, 1))

def s1_descriptives(rows):
    out = []
    for r in rows:
        w, d = wd(r)
        lo, hi = wilson(r["success_prob"], r["shots"])
        floor = 1 / 2**w
        out.append(dict(provider=r["provider"], circuit=r["circuit"], w=w, d=d, rep=r["rep"],
                        F=r["success_prob"], ci=(round(lo,4), round(hi,4)), shots=r["shots"],
                        above_floor=above_floor(r)))
    return out

# ---------------- S2: decay fit + LRT (H3) ----------------
FIT_ROLES = {"ibm": "primary H3 test", "braket_rigetti": "replication support",
             "braket_iqm_garnet": "replication support"}

def _decay_fit(W, D, F, bounded):
    """One fit variant. Bounded: A in (0,1.05], alpha>=0, beta>=0 (physical:
    more width/depth cannot help), gamma unconstrained. Returns (report, gamma, p)."""
    from scipy.optimize import curve_fit
    from scipy.stats import chi2
    full = lambda X, A, a, b, g: A * np.exp(-(a*X[0] + b*X[1] + g*X[0]*X[1]))
    null = lambda X, A, a, b:    A * np.exp(-(a*X[0] + b*X[1]))
    n = len(F)
    bf = ([1e-9, 0, 0, -np.inf], [1.05, np.inf, np.inf, np.inf]) if bounded else (-np.inf, np.inf)
    bn = ([1e-9, 0, 0], [1.05, np.inf, np.inf]) if bounded else (-np.inf, np.inf)
    pf, cf = curve_fit(full, (W, D), F, p0=[1, .01, .01, .001], bounds=bf, maxfev=40000)
    pn, _ = curve_fit(null, (W, D), F, p0=[1, .01, .01], bounds=bn, maxfev=40000)
    ssef = float(np.sum((F - full((W, D), *pf))**2))
    ssen = float(np.sum((F - null((W, D), *pn))**2))
    lrt = n * math.log(max(ssen, 1e-12) / max(ssef, 1e-12))
    pval = float(chi2.sf(lrt, df=1))
    se = np.sqrt(np.maximum(np.diag(cf), 0))
    ci = {k: (round(float(pf[i]-1.96*se[i]), 6), round(float(pf[i]+1.96*se[i]), 6))
          for i, k in enumerate(["A", "alpha", "beta", "gamma"])}
    rep = dict(A=round(pf[0],4), alpha=round(pf[1],5), beta=round(pf[2],5), gamma=round(pf[3],6),
               ci=ci, sse_full=round(ssef,5), sse_null=round(ssen,5),
               LRT=round(lrt,3), p_gamma=round(pval,6))
    return rep, float(pf[3]), pval

def s2_decay(rows):
    res = {}
    # Decay-certified records ONLY: post-barrier thesis runs (phases 1-3).
    # measure_all_* mirrors and the phase-4 Azure point are PRE-BARRIER
    # (compiler-cancelled, fidelity fake-high) -- they stay in the pricing
    # analyses, where the bill is real, but must never enter a decay fit.
    fit_rows = [r for r in rows if fidelity_certified(r)]
    for prov in sorted({r["provider"] for r in fit_rows}):
        sub = [r for r in fit_rows if r["provider"] == prov and
               ("mirror" in r["circuit"] or r["circuit"].startswith("fair"))]
        sub = [r for r in sub if wd(r)[1] > 0]
        # drop rule: floor cells carry no decay information, only readout noise
        kept = [r for r in sub if above_floor(r)]
        dropped = [f"{r['circuit']}#r{r['rep']}" for r in sub if not above_floor(r)]
        sub = kept
        role = FIT_ROLES.get(prov, "anchor data" if prov == "braket_ionq_forte" else None)
        single_width = len({wd(r)[0] for r in sub}) < 2
        if len(sub) < 4 and not (single_width and len(sub) >= 2):
            if dropped or sub:
                res[prov] = {"n": len(sub), "n_dropped_floor": len(dropped), "role": role,
                             "dropped": dropped, "verdict": "insufficient above-floor cells"}
            continue
        W = np.array([wd(r)[0] for r in sub], float)
        D = np.array([wd(r)[1] for r in sub], float)
        F = np.clip(np.array([r["success_prob"] for r in sub], float), 1e-4, 1)
        out = {"n": len(F), "n_dropped_floor": len(dropped), "dropped": dropped, "role": role}
        if single_width:
            # single width column: alpha/gamma are collinear -- gamma is
            # unidentifiable. Report the effective per-depth decay instead.
            from scipy.optimize import curve_fit as _cf
            k = None
            try:
                pk, _ = _cf(lambda d, A, k: A*np.exp(-k*d), D, F, p0=[1, .05], maxfev=20000)
                k = round(float(pk[1]), 5)
            except Exception:
                pass
            out["effective_depth_decay_k"] = k
            out["verdict"] = ("decay replication only (single width column: gamma "
                              "unidentifiable); depth decay confirmed" if k and k > 0
                              else "single width column; no decay signal")
            res[prov] = out
            continue
        gs, ps = {}, {}
        for variant, bounded in [("unconstrained", False), ("bounded", True)]:
            try:
                out[variant], gs[variant], ps[variant] = _decay_fit(W, D, F, bounded)
            except Exception as e:
                out[variant] = {"error": str(e)}
        if len(gs) == 2:
            stable = (np.sign(gs["unconstrained"]) == np.sign(gs["bounded"])
                      and (ps["unconstrained"] < 0.05) == (ps["bounded"] < 0.05))
            out["variants_stable"] = bool(stable)
            if not stable:
                out["verdict"] = ("WARNING: gamma sign/significance UNSTABLE across fit "
                                  "variants -- no verdict; do not cite this fit")
                print(f"\n*** S2 WARNING [{prov}]: gamma unstable across fit variants "
                      f"(unconstrained g={gs['unconstrained']:.5f} p={ps['unconstrained']:.4f}; "
                      f"bounded g={gs['bounded']:.5f} p={ps['bounded']:.4f}) ***\n")
            else:
                sig = ps["bounded"] < 0.05
                out["verdict"] = ("H3 SUPPORTED (gamma != 0, stable across variants)"
                                  if sig else "H3 not supported at this N")
        else:
            out["verdict"] = "fit failure in a variant -- see errors"
        # leave-one-cell-out sensitivity on the primary machine
        if prov == "ibm":
            loo = {}
            for circ in sorted({r["circuit"] for r in sub}):
                keep = [r for r in sub if r["circuit"] != circ]
                if len(keep) < 4: continue
                Wk = np.array([wd(r)[0] for r in keep], float)
                Dk = np.array([wd(r)[1] for r in keep], float)
                Fk = np.clip(np.array([r["success_prob"] for r in keep], float), 1e-4, 1)
                try:
                    _, g_lo, _ = _decay_fit(Wk, Dk, Fk, bounded=True)
                    loo[circ] = round(g_lo, 6)
                except Exception:
                    loo[circ] = None
            vals = [v for v in loo.values() if v is not None]
            if vals:
                out["loo_gamma"] = dict(per_dropped_circuit=loo,
                                        range=(round(min(vals),6), round(max(vals),6)),
                                        sign_stable=bool(min(vals) > 0 or max(vals) < 0))
        res[prov] = out
    return res

# ---------------- S3 ----------------
def s3_repvar(rows):
    from collections import defaultdict
    cells = defaultdict(list)
    for r in rows:
        cells[(r["provider"], r["circuit"])].append(r["success_prob"])
    out = {}
    for k, v in cells.items():
        if len(v) >= 2:
            out[f"{k[0]}|{k[1]}"] = dict(reps=len(v), mean=round(float(np.mean(v)),4),
                                          between_rep_sd=round(float(np.std(v, ddof=1)),4),
                                          shot_sd=round(math.sqrt(np.mean(v)*(1-np.mean(v))/1000),4))
    return out

# ---------------- record eligibility (certification rules) ----------------
SIM_PROVIDERS = {"local", "braket_SV1_SIMULATOR_test"}

def is_sim(rec) -> bool:
    """Simulator records: pipeline plumbing, never citable as hardware evidence.
    azure_rigetti has sim smoke-test data only (no QPU target exists on Azure)."""
    return (rec["provider"] in SIM_PROVIDERS
            or "simulator" in str(rec.get("azure_target", ""))
            or rec["provider"] == "azure_rigetti")

def fidelity_certified(rec) -> bool:
    """Fidelity is certified only for barrier/verbatim thesis runs: phases 1-3
    and phase 5 (IonQ Braket VERBATIM -- compiler cannot touch the mirror).
    Pre-barrier records (measure_all_*, phase-4 Azure) and phase 6 (barrier
    stripped for Azure QIR) keep valid BILLS but uncertified fidelities."""
    return rec.get("phase") in (1, 2, 3, 5)

# ---------------- S4: exchange-rate drift (H1) ----------------
def s4_exchange(rows):
    """Per-record cross-meter ratios over HARDWARE records only. The time
    meter enters only where time was actually billed (qpu_seconds > 0)."""
    hw = [r for r in rows if not is_sim(r)]
    pairs = [("gate", "volume"), ("aqt_off", "volume"), ("gate", "time"), ("time", "volume")]
    per = {f"{a}/{b}": {} for a, b in pairs}
    for r in hw:
        prices = price_all_meters(r)
        key = f"{r['provider']}|{r['circuit']}#r{r.get('rep', 1)}"
        for a, b in pairs:
            if a in prices and b in prices and prices[b] > 0:
                per[f"{a}/{b}"][key] = prices[a] / prices[b]
    out = {}
    for name, ratios in per.items():
        if len(ratios) >= 3:
            vals = list(ratios.values())
            drift = round(max(vals)/min(vals), 1) if min(vals) > 0 else None
            out[name] = dict(n=len(vals), min=round(min(vals),4), max=round(max(vals),4),
                             drift=drift,
                             rank_reversal=bool(min(vals) < 1 < max(vals)),
                             verdict="H1 SUPPORTED" if drift and drift > 10 else "drift < 10x")
    return out

# ---------------- S5: channel-flip (H2) ----------------
# GBP -> USD for Azure bills (AQT token is GBP-denominated on this account).
# 1.28 = rate used across the thesis budget (thesis_run.USD_PER_GBP), set
# 2026-08-16; re-verify against a dated FX source before final submission.
GBP_USD = 1.28

def azure_billed_usd(rec):
    """Real platform bill from a record's azure_cost_estimate, in USD.
    Returns (usd, detail) or (None, reason)."""
    ce = rec.get("azure_cost_estimate")
    if not ce:
        return None, "no azure_cost_estimate on record"
    if isinstance(ce, str):
        import ast
        try:
            ce = ast.literal_eval(ce)
        except Exception:
            return None, "unparseable azure_cost_estimate string"
    try:
        ev = ce["events"][0]
        native = float(ev["amountBilled"]) * float(ev["unitPrice"])
        cur = ce.get("currencyCode", "USD")
        usd = native * (GBP_USD if cur == "GBP" else 1.0)
        return usd, dict(amount_billed_tokens=float(ev["amountBilled"]),
                         unit_price=float(ev["unitPrice"]), currency=cur,
                         native_total=round(native, 4), gbp_usd_rate=GBP_USD,
                         usd=round(usd, 4))
    except Exception as e:
        return None, f"parse error: {e}"

def _mirror_native_counts(w, d):
    """Closed-form submitted gate counts of fair_mirror(w, d) in the safe basis
    (matches transpiled_gates measured on every run): 1q = 6wd, 2q from the
    brickwork pattern."""
    ones = 6 * w * d
    twos = 2 * sum((w - (l % 2)) // 2 for l in range(d))
    return ones, twos

def s5_channel_flip(rows):
    out = {}
    # Rigetti: braket volume (measured) vs azure time (measured qpu_seconds needed)
    az = [r for r in rows if r["provider"] == "azure_rigetti"]
    bk = [r for r in rows if r["provider"] == "braket_rigetti"]
    if az and bk:
        flips = {}
        for a in az:
            twin = next((b for b in bk if b["circuit"] == a["circuit"]), None)
            if twin is None: continue
            p_az = a.get("cost_formula_usd") or (a.get("qpu_seconds") or 0) * 100 * RATES["azure_time_per_10ms"]
            p_bk = RATES["vol_task"] + twin["shots"] * RATES["vol_shot_rigetti"]
            if p_az and p_bk:
                flips[a["circuit"]] = round(p_az/p_bk, 3)
        if flips:
            v = list(flips.values())
            out["rigetti_azure/braket"] = dict(ratios=flips, drift=round(max(v)/min(v),1),
                flip=bool(min(v) < 1 < max(v)),
                verdict="H2 SUPPORTED (measured flip)" if max(v)/min(v) > 2 else "ratio near-constant")
    # IonQ same-machine flip: Azure (gate/AQT meter) vs Braket (volume meter).
    # DEVICE-SPECIFIC Forte constants throughout (see RATES note). There is NO
    # calibration factor: under the correct constants every measured bill
    # reconciles with the published tariff (both invoices were floors), so the
    # Azure side of the surface is the published formula itself, for both
    # mitigation states -- a projection from disclosed constants, not a
    # measurement, and labelled as such.
    az_ion = [r for r in rows if r["provider"] == "azure_ionq"]
    ion_res = {}
    points = []
    for r in az_ion:
        S = r["shots"]; g = r["transpiled_gates"]
        gate_cost = S*(g["1q"]*RATES["aqt_1q"] + g["2q"]*RATES["aqt_2q"])
        price_on = max(gate_cost, RATES["aqt_min_on"])
        price_off = max(gate_cost, RATES["aqt_min_off"])
        billed_usd, detail = azure_billed_usd(r)
        if not billed_usd:
            continue
        # infer the ACTIVE mitigation state from which tariff branch the
        # invoice reconciles with; the platform default is ON and the runner's
        # mitigation flag was not transmitted to the service (phase-4 lesson)
        on_err = abs(billed_usd/price_on - 1.0)
        off_err = abs(billed_usd/price_off - 1.0)
        state = "mitigation_on(platform_default)" if on_err < off_err else "mitigation_off"
        analytic = price_on if on_err < off_err else price_off
        p_vol = RATES["vol_task"] + S*RATES["vol_shot"]   # Braket Forte price, exact ex ante
        points.append(dict(
            circuit=r["circuit"], shots=S,
            azure_billed_usd=round(billed_usd, 2), billing_detail=detail,
            inferred_state=state,
            gate_formula_usd=round(gate_cost, 2),
            azure_analytic_usd=round(analytic, 2),
            reconciliation=round(billed_usd/analytic, 4),
            floor_binding=bool(gate_cost < analytic),
            braket_volume_usd=round(p_vol, 2),
            ratio_azure_over_braket=round(billed_usd/p_vol, 3)))
    if points:
        ion_res["measured_points"] = points
        recs = [p["reconciliation"] for p in points]
        ion_res["invoice_reconciliation"] = dict(
            values=recs,
            max_abs_deviation=round(max(abs(x - 1.0) for x in recs), 4),
            note=("billed/analytic under device-specific Forte constants and the "
                  "inferred mitigation state; both certified invoices were "
                  "tariff floors (min charge bound, gate formula did not)"))
        # published-formula Azure surface over the thesis grid, both mitigation
        # states, 1000 shots. PROJECTION from disclosed constants -- exact if
        # the tariff is honoured (which the two invoices confirm), but not
        # itself a measurement.
        S = 1000
        p_vol = RATES["vol_task"] + S*RATES["vol_shot"]
        surface = {}
        for w in (3, 8, 12):
            for d in (8, 16, 32):
                n1, n2 = _mirror_native_counts(w, d)
                gate = S*(n1*RATES["aqt_1q"] + n2*RATES["aqt_2q"])
                off = max(gate, RATES["aqt_min_off"])
                on = max(gate, RATES["aqt_min_on"])
                surface[f"fair_w{w}_d{d}"] = dict(
                    label="published_formula_projection",
                    azure_mit_off_usd=round(off, 2), azure_mit_on_usd=round(on, 2),
                    braket_volume_usd=round(p_vol, 2),
                    ratio_off=round(off/p_vol, 3), ratio_on=round(on/p_vol, 3))
        ion_res["formula_surface"] = surface
        r_off = [v["ratio_off"] for v in surface.values()]
        r_on = [v["ratio_on"] for v in surface.values()]
        ion_res["surface_ratio_range_off"] = (round(min(r_off), 2), round(max(r_off), 2))
        ion_res["surface_ratio_range_on"] = (round(min(r_on), 2), round(max(r_on), 2))
        flip_off = min(r_off) < 1.0 < max(r_off)
        ion_res["cheaper_channel_flips_mit_off"] = flip_off
        meas = sorted(p["ratio_azure_over_braket"] for p in points)
        supported = (max(meas) > 1.5 or min(meas) < 0.67
                     or flip_off or max(r_off)/min(r_off) > 2)
        if supported:
            ion_res["verdict"] = (
                f"H2 SUPPORTED (same machine, channel-dependent billing architecture: "
                f"measured ratios {meas} across shot regimes; published-formula surface "
                f"mit-off {min(r_off):.2f}-{max(r_off):.2f}x"
                + (", cheaper channel FLIPS inside the grid" if flip_off else "")
                + f", mit-on {min(r_on):.2f}-{max(r_on):.2f}x; invoices reconcile with "
                f"the device-specific tariff to <={max(abs(x-1) for x in recs):.2%})")
        else:
            ion_res["verdict"] = "ratio near-constant across grid -- H2 not supported"
    elif az_ion:
        ion_res["verdict"] = "azure record present but no billed amount parseable"
    if ion_res:
        out["ionq_azure/braket"] = ion_res
    return out

# ---------------- S6: QFC + Kendall tau (H4) ----------------
def s6_qfc(rows):
    from scipy.stats import kendalltau
    from collections import defaultdict
    cells = defaultdict(list)
    # QFC ranks depend on fidelity -> certified hardware fidelities only
    for r in rows:
        if is_sim(r) or not fidelity_certified(r):
            continue
        cells[(r["provider"], r["circuit"])].append(r)
    table = []
    for (prov, circ), rs in cells.items():
        F = float(np.mean([x["success_prob"] for x in rs]))
        r0 = rs[0]
        # v2 CORRECTION (2026-09-26): price each provider's
        # volume meter at ITS OWN archived shot rate, not the Forte rate that
        # price_all_meters uses for the counterfactual grid. No fallback: a
        # record with no native price is excluded, not silently re-metered.
        S0 = r0["shots"]
        if prov == "ibm":
            pn = (r0.get("qpu_seconds") or 0) * RATES["time_usd_per_s"] or None
        elif prov == "braket_rigetti":
            pn = RATES["vol_task"] + S0 * RATES["vol_shot_rigetti"]
        elif prov == "braket_iqm_garnet":
            pn = RATES["vol_task"] + S0 * RATES["vol_shot_iqm"]
        elif prov == "braket_ionq_forte":
            pn = RATES["vol_task"] + S0 * RATES["vol_shot"]
        elif prov == "azure_ionq":
            pn = price_all_meters(r0).get("aqt_off")
        else:
            pn = None
        if pn and pn > 0:
            table.append(dict(provider=prov, circuit=circ, F=round(F,4),
                              native_price=round(pn,4), QFC=round(F*r0["shots"]/pn,2)))
    # rank concordance per circuit across providers
    taus = {}
    circs = sorted({t["circuit"] for t in table})
    for cx in circs:
        sub = [t for t in table if t["circuit"] == cx]
        if len(sub) >= 3:
            fr = [t["F"] for t in sub]; qr = [t["QFC"] for t in sub]
            tau, pv = kendalltau(fr, qr)
            taus[cx] = dict(n=len(sub), tau=round(float(tau),3), p=round(float(pv),3))
    verdict = "H4 SUPPORTED (rankings discordant)" if any(t["tau"] < 0.99 for t in taus.values()) else "rankings concordant"
    return dict(table=table, kendall=taus, verdict=verdict if taus else "insufficient overlap")

# ---------------- S8: heuristic regret (H4) ----------------
def s8_regret(rows, n_interior=200, seed=7):
    rng = np.random.default_rng(seed)
    usable = [r for r in rows if not is_sim(r) and fidelity_certified(r)]
    provs = sorted({r["provider"] for r in usable})
    # build per-provider interpolators of F over (w,d) from mirror data
    fits = {}
    for prov in provs:
        pts = [(wd(r)[0], wd(r)[1], r["success_prob"]) for r in usable
               if r["provider"] == prov and wd(r)[1] > 0]
        # >=2 admits the 2-point IonQ depth pair (final-session anchor data);
        # inverse-distance interpolation degrades gracefully at low n
        if len(pts) >= 2:
            fits[prov] = pts
    if len(fits) < 2:
        return {"note": "need >=2 providers with mirror data"}
    def interp_F(prov, w, d):
        pts = fits[prov]
        ws = np.array([p[0] for p in pts]); ds = np.array([p[1] for p in pts]); fs = np.array([p[2] for p in pts])
        dist = np.sqrt(((ws-w)/9)**2 + ((ds-d)/24)**2) + 1e-6
        wt = 1/dist**2
        return float(np.sum(wt*fs)/np.sum(wt))
    def price(prov, w, d, S=1000):
        n1, n2 = 6*w*d, int(0.9*w*d)  # structural estimates
        if prov == "braket_rigetti":
            return RATES["vol_task"] + S*RATES["vol_shot_rigetti"]
        if prov.startswith("braket_iqm"):
            return RATES["vol_task"] + S*RATES["vol_shot_iqm"]
        if prov == "braket_ionq_forte":
            return RATES["vol_task"] + S*RATES["vol_shot"]
        if prov == "azure_ionq":
            return max(S*(n1*RATES["aqt_1q"] + n2*RATES["aqt_2q"]), RATES["aqt_min_off"])
        if prov in ("ibm", "azure_rigetti"):
            secs = 2.0 * (1 + d/16) * (S/1000)  # anchored to measured 2.0s point
            return secs * (RATES["time_usd_per_s"] if prov == "ibm" else 100*RATES["azure_time_per_10ms"])
        return None
    # only heuristics whose target provider has data; a fallback pick would
    # silently test a different rule than the one named
    heuristics = {
        "always_cheapest_pershot": lambda cands: min(cands, key=lambda c: {"braket_rigetti":0,"braket_iqm_garnet":1}.get(c[0], 9)),
    }
    skipped = []
    if "braket_ionq_forte" in fits:
        heuristics["always_ionq_braket"] = lambda cands: next((c for c in cands if c[0]=="braket_ionq_forte"), cands[0])
    else:
        skipped.append("always_ionq_braket (no braket_ionq_forte data)")
    if any(p in ("ibm", "azure_rigetti") for p in fits):
        heuristics["always_time_meter"] = lambda cands: next((c for c in cands if c[0] in ("ibm","azure_rigetti")), cands[0])
    else:
        skipped.append("always_time_meter (no time-metered provider data)")
    regrets = {h: [] for h in heuristics}
    for _ in range(n_interior):
        w = float(rng.uniform(3, 12)); d = float(rng.uniform(8, 32))
        cands = []
        for prov in fits:
            P = price(prov, w, d)
            if P:
                F = max(interp_F(prov, w, d), 1e-3)
                cands.append((prov, P / (F*1000)))  # cost per success
        if len(cands) < 2: continue
        oracle = min(c[1] for c in cands)
        for h, rule in heuristics.items():
            pick = rule(cands)
            regrets[h].append(pick[1]/oracle)
    out = {}
    for h, v in regrets.items():
        if v:
            out[h] = dict(mean_regret=round(float(np.mean(v)),2), p95=round(float(np.percentile(v,95)),2),
                          pct_optimal=round(100*float(np.mean(np.array(v) < 1.01)),1))
    if skipped:
        out["heuristics_skipped"] = skipped
    out["providers_in_simulation"] = sorted(fits)
    out["verdict"] = ("H4 SUPPORTED (no heuristic near-optimal everywhere)"
                      if all(o["pct_optimal"] < 90 for o in out.values() if isinstance(o, dict) and "pct_optimal" in o)
                      else "a heuristic dominates")
    return out

# ---------------- certification ----------------
def certify(rows, stats):
    import datetime
    date = datetime.date.today().isoformat()
    warnings = []

    print("\n" + "="*72)
    print(f"CERTIFICATION REPORT -- {date}")
    print("="*72)

    s4 = stats["S4_exchange_H1"]
    print("\nH1 (no convertibility) -- exchange-rate drift, hardware records only:")
    for pair, v in s4.items():
        print(f"  {pair:16s} n={v['n']:3d}  min={v['min']}  max={v['max']}  "
              f"drift={v['drift']}x  rank_reversal={v['rank_reversal']}  -> {v['verdict']}")

    s5 = stats["S5_channel_flip_H2"].get("ionq_azure/braket", {})
    pts = s5.get("measured_points", [])
    print("\nH2 (channel-dependent billing architecture) -- IonQ Forte-1, Azure vs Braket:")
    for p in pts:
        print(f"  MEASURED  {p['circuit']}@{p['shots']} [{p['inferred_state']}"
              f"{', FLOOR binding' if p['floor_binding'] else ''}]: Azure billed "
              f"${p['azure_billed_usd']} (analytic ${p['azure_analytic_usd']}, "
              f"reconciliation {p['reconciliation']}) vs Braket ${p['braket_volume_usd']} "
              f"-> ratio {p['ratio_azure_over_braket']}x")
    if s5.get("invoice_reconciliation"):
        print(f"  INVOICE RECONCILIATION: max deviation "
              f"{s5['invoice_reconciliation']['max_abs_deviation']} "
              f"(device-specific Forte constants; both bills were tariff floors)")
    if s5.get("surface_ratio_range_off"):
        print(f"  PUBLISHED-FORMULA surface (projection, 1000 shots): "
              f"mit-off ratio range {s5['surface_ratio_range_off']}"
              f"{' (cheaper channel FLIPS inside grid)' if s5.get('cheaper_channel_flips_mit_off') else ''}, "
              f"mit-on {s5['surface_ratio_range_on']}")
    if len(pts) >= 2:
        hi, lo = pts[0], pts[-1]
        print(f"  TWO-REGIME PAIR: same machine+circuit, ratio "
              f"{hi['ratio_azure_over_braket']}x ({hi['shots']} shots) vs "
              f"{lo['ratio_azure_over_braket']}x ({lo['shots']} shots) -- the shots "
              f"dial alone moves the cross-channel price ratio")
    print(f"  -> {s5.get('verdict')}")

    s2 = stats["S2_decay_H3"]
    print("\nH3 (non-separability, gamma != 0):")
    for prov in ("ibm", "braket_rigetti", "braket_iqm_garnet"):
        v = s2.get(prov)
        if not v: continue
        print(f"  {prov} [{v.get('role')}]: n={v.get('n')} (floor-dropped {v.get('n_dropped_floor')})")
        for var in ("unconstrained", "bounded"):
            f = v.get(var)
            if f and "gamma" in f:
                print(f"    {var:14s} gamma={f['gamma']} CI={f['ci']['gamma']} p={f['p_gamma']}")
        if "loo_gamma" in v:
            print(f"    leave-one-out gamma range: {v['loo_gamma']['range']} "
                  f"(sign stable: {v['loo_gamma']['sign_stable']})")
        if "effective_depth_decay_k" in v:
            print(f"    effective depth decay k={v['effective_depth_decay_k']}")
        print(f"    -> {v.get('verdict')}")
        if "WARNING" in str(v.get("verdict")):
            warnings.append(f"S2/{prov}: {v['verdict']}")

    s6, s8 = stats["S6_qfc_H4"], stats["S8_regret_H4"]
    print("\nH4 (no stable value ranking):")
    print(f"  QFC Kendall tau per circuit: { {k: v['tau'] for k, v in s6.get('kendall', {}).items()} }"
          f" -> {s6.get('verdict')}")
    for h, v in s8.items():
        if isinstance(v, dict) and "pct_optimal" in v:
            print(f"  regret[{h}]: mean={v['mean_regret']}x p95={v['p95']}x optimal {v['pct_optimal']}%")
    if s8.get("heuristics_skipped"):
        print(f"  heuristics skipped (no data): {s8['heuristics_skipped']}")
        warnings.append(f"S8 skipped: {s8['heuristics_skipped']}")
    print(f"  providers simulated: {s8.get('providers_in_simulation')} -> {s8.get('verdict')}")

    # ---- independent cross-checks ----
    print("\nCROSS-CHECKS (independent recomputation):")
    # (a) gate/volume drift straight from rate constants + gate counts
    vals = []
    for r in rows:
        if is_sim(r): continue
        g, S = r["transpiled_gates"], r["shots"]
        gate = max(S*(g["1q"]*0.00022 + g["2q"]*0.000975), 1.00)
        vol = 0.30 + S*0.08
        vals.append(gate/vol)
    drift_chk = round(max(vals)/min(vals), 1)
    s4_drift = s4.get("gate/volume", {}).get("drift")
    ok_a = abs(drift_chk - (s4_drift or 0)) < 0.05 * drift_chk
    print(f"  (a) gate/volume drift: recomputed {drift_chk}x vs S4 {s4_drift}x -> "
          f"{'MATCH' if ok_a else 'MISMATCH'}")
    print(f"      [expected 193.6x was from the pre-certification snapshot: the dataset has "
          f"since changed -- verbatim physical gate counts + post-barrier reruns]")
    if not ok_a: warnings.append("cross-check (a) mismatch")
    # (b) Azure bill re-derived + floor identification under Forte constants
    bill = 168.2 * 0.78201 * GBP_USD
    p1000 = next((p for p in pts if p["shots"] == 1000), {})
    mp_billed = p1000.get("azure_billed_usd")
    ok_b = mp_billed and abs(bill - mp_billed) < 0.01
    print(f"  (b) Azure bill: 168.2 AQT x 0.78201 GBP x {GBP_USD} = ${bill:.2f} "
          f"vs S5 ${mp_billed} -> {'MATCH' if ok_b else 'MISMATCH'}")
    ok_b2 = mp_billed and abs(mp_billed / RATES["aqt_min_on"] - 1.0) < 0.01
    print(f"      floor identification: ${mp_billed} / Forte min_mit_on "
          f"${RATES['aqt_min_on']} = {mp_billed / RATES['aqt_min_on']:.4f} -> "
          f"{'FLOOR CONFIRMED' if ok_b2 else 'NOT the mit-on floor'}")
    if not ok_b: warnings.append("cross-check (b) mismatch")
    if not ok_b2: warnings.append("cross-check (b2) phase-4 bill is not the Forte mit-on floor")
    # (c) IBM gamma by log-linear OLS
    sub = [r for r in rows if r["provider"] == "ibm" and fidelity_certified(r)
           and r["circuit"].startswith("fair") and wd(r)[1] > 0 and above_floor(r)]
    W = np.array([wd(r)[0] for r in sub], float); D = np.array([wd(r)[1] for r in sub], float)
    y = -np.log(np.clip(np.array([r["success_prob"] for r in sub], float), 1e-4, 1))
    X = np.column_stack([np.ones_like(W), W, D, W*D])
    beta_ols = np.linalg.lstsq(X, y, rcond=None)[0]
    g_ols = float(beta_ols[3])
    g_nls = s2["ibm"]["unconstrained"]["gamma"]
    ok_c = np.sign(g_ols) == np.sign(g_nls) and 0.2 < abs(g_ols/g_nls) < 5
    print(f"  (c) IBM gamma, log-linear OLS: {g_ols:.6f} vs NLS {g_nls} -> "
          f"{'AGREE (sign+magnitude)' if ok_c else 'DISAGREE'}")
    if not ok_c: warnings.append("cross-check (c) OLS/NLS gamma disagreement")

    print(f"\nWARNINGS ({len(warnings)}):")
    for w in warnings or ["none"]:
        print(f"  - {w}")

    frozen = HERE / "results" / f"thesis_stats_CERTIFIED_{date}_v2.json"
    if frozen.exists():  # never overwrite an earlier frozen copy from the same date
        frozen = HERE / "results" / f"thesis_stats_CERTIFIED_{date}_final.json"
    payload = dict(certified_date=date, gbp_usd_rate=GBP_USD,
                   cross_checks=dict(gate_volume_drift=drift_chk, azure_bill_usd=round(bill,2),
                                     ibm_gamma_ols=round(g_ols,6)),
                   warnings=warnings, stats=stats)
    frozen.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nfrozen copy -> {frozen}")
    return warnings


# ---------------- main ----------------
def main():
    argv = [a for a in sys.argv[1:] if a != "--certify"]
    do_certify = "--certify" in sys.argv[1:]
    paths = argv or (glob.glob(str(HERE/"results/thesis_*.json")) +
                     glob.glob(str(HERE/"results/measure_all_*.json")))
    paths = [p for p in paths if "CERTIFIED" not in p and "thesis_stats" not in p]
    rows = load(paths)
    if not rows:
        sys.exit("no result files found")
    print(f"loaded {len(rows)} records from {len(paths)} files; providers: {sorted({r['provider'] for r in rows})}\n")
    stats = {
        "S1_descriptives": s1_descriptives(rows),
        "S2_decay_H3": s2_decay(rows),
        "S3_repvar": s3_repvar(rows),
        "S4_exchange_H1": s4_exchange(rows),
        "S5_channel_flip_H2": s5_channel_flip(rows),
        "S6_qfc_H4": s6_qfc(rows),
        "S8_regret_H4": s8_regret(rows),
    }
    out = HERE / "results" / "thesis_stats_v2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(stats, indent=2, default=str))
    # console verdict summary
    print("=== HYPOTHESIS SCOREBOARD ===")
    for k in ["S4_exchange_H1", "S5_channel_flip_H2", "S2_decay_H3", "S6_qfc_H4", "S8_regret_H4"]:
        v = stats[k]
        if isinstance(v, dict):
            verdicts = [f"{kk}: {vv.get('verdict') or vv.get('drift')}" for kk, vv in v.items()
                        if isinstance(vv, dict) and ("verdict" in vv or "drift" in vv)]
            print(f"{k}: " + ("; ".join(verdicts) if verdicts else v.get("verdict", "see json")))
    print(f"\nfull statistics -> {out}")
    if do_certify:
        certify(rows, stats)

if __name__ == "__main__":
    main()
