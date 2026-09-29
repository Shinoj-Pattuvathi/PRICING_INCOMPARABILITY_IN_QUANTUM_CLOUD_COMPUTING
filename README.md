# qcost-bench — replication package (v1.0-thesis)

## What this is

The measurement instrument and replication data for the MSc-by-Research thesis
**"Pricing Incomparability in Quantum Cloud Computing"** (Shinoj Pattuvathi,
University of Huddersfield). The instrument runs identical self-grading
circuits on quantum hardware reached through several cloud channels, records a
common measurement vector for every execution (width, depth, gate counts,
transpiled depth, success probability, QPU seconds), and prices each record
under every provider's native billing meter: IBM's time meter (QPU seconds),
AWS Braket's volume meter (tasks + shots), IonQ's gate-shot meter, and the
Azure Quantum Token (AQT) gate-token meter. The S1–S8 statistical pipeline
turns those records into the thesis hypothesis scoreboard (H1–H4). Everything
needed to recompute the certified statistics and figures from the raw records
is in this repository; nothing here requires cloud access.

## The three evidential flags

**Invoiced** prices are amounts a platform actually charged and returned in an
invoice payload, such as the two Azure IonQ bills recorded in the raw records
(168.2 and 25.79 AQT). Both bills were invoiced in full and paid personally
(invoice G182569155, `rate_archive/invoices_redacted/`); no Azure credit was
applied, contrary to the expectation recorded in the ledger's two Azure
`credits_covered_adjustment` rows, which are retained verbatim. Braket
consumption drew down a $120.00 AWS promotional pool with the remainder
charged personally. See `CHANGELOG.md` v1.2. **Formula** prices are reconstructed from the execution
records under the archived rate card: exact for volume meters, whose tariff
(task + shots) is fully determined ex ante, but a derivation rather than a
receipt, and likewise for the published-formula Azure surface. **Counterfactual**
prices are what a tariff would have charged where no meter ran: all IBM
time-meter dollar figures fall in this class because the runs used the free
Open Plan, and `ledger/ledger_reconciliation.csv` deliberately excludes them
from actual spend.

## Repository map

| Path | Contents |
|---|---|
| `src/` | The instrument: circuit generators (`circuits.py`), the three billing models (`pricing.py`), hardware runners for IBM, Braket and IonQ (`runners.py`), the Azure channel (`azure_runner.py`), the unified single-program measurement (`measure_all.py`), the CLI orchestrator (`run_benchmark.py`), the budget-guarded execution programme (`thesis_run.py`), the cross-provider analysis (`analyze.py`), the ledger reconciliation (`reconcile_ledger.py`), the S1–S8 pipeline in both versions (`thesis_analysis.py`, `thesis_analysis_v2_revised.py`), the figure script (`make_figures.py`) and the rate/workload configuration (`config.yaml`). |
| `src/README_instrument.md` | src/README_instrument.md is the original v1 instrument documentation; its rates are the August 2026 snapshot and its DEFAULT_RATES fallback for Rigetti ($0.0009/shot) predates the verified $0.000425 in config.yaml. Read it as history, not as current tariff guidance. |
| `data/raw/` | Hardware execution records from the thesis phases (`thesis_ibm.json`, `thesis_braket_rigetti.json`, `thesis_braket_iqm_garnet.json`, `thesis_braket_ionq_forte.json`, `thesis_azure_ionq.json`) and the seven `measure_all_*.json` single-program runs (including the free local-simulator and SV1-simulator runs, which the pipeline recognises as simulators and excludes from hardware-only statistics). |
| `data/quarantined/` | Pre-barrier IBM and Rigetti records. Optimising compilers cancelled the mirror halves of these circuits, so their fidelities are not decay evidence; their bills remain valid and they are kept for the pricing analysis only. |
| `data/certified/` | The frozen certification outputs. `thesis_stats_CERTIFIED_2026-09-26_v2.json` is the current freeze (v2 pipeline, S6 corrected). `thesis_stats_CERTIFIED_2026-09-01.json` is the v1 pipeline's freeze with the Azure device-constant correction. The two `2026-08-17` freezes are the earliest certifications, kept for provenance. |
| `rate_archive/` | `index.csv`: one row per rate constant found in `config.yaml`, the `RATES` dict in `thesis_analysis.py` and the `AZURE_RATES` dict in `azure_runner.py`, with the verification date and source each file records. `azure_targets_2026-08-16.txt` and `azure_prequotes_2026-08-16.txt` are the Azure target listing and pre-submission quote attempts. `captures/` holds the dated pricing-page captures (see its INDEX.md); `invoices_redacted/` holds the redacted Azure invoice G182569155 and an index of what was redacted. |
| `ledger/` | `ledger.csv`, the ex-ante commitment ledger written by `thesis_run.py`, and its derived reconciliation (`ledger_reconciliation.csv`, `.json`) produced by `reconcile_ledger.py`. The reconciliation's cash fields were corrected in v1.2 after the Azure invoice arrived; `ledger.csv` itself is unchanged. |
| `figures/` | The thesis figures: `fig_exchange_H1.png`, `fig_flip_H2.png`, `fig_decay_H3.png`, `fig_regret_H4.png`, and `fig_qfc_H4_v2_revised.png` (S6 after the correction). |
| `figures/superseded/` | `fig_qfc_H4.png`, the pre-correction S6 render, kept under its original name so the earlier reading can be inspected. Also `measure_all_local.png`, the chart from the free local-simulator run whose source data is `data/raw/measure_all_local.json`; it is illustrative rather than superseded and lives here only for tidiness. |
| `environment/requirements.txt` | Pinned package versions of the environment the runs used. |
| `reproduce.sh` | Copies the shipped data back into the layout the scripts expect and runs the offline reproduction (see below). |
| `CHECKSUMS.sha256` | SHA-256 of every file in the repository at tag time. |

## Reproduce offline in three commands

```bash
pip install -r environment/requirements.txt
python src/thesis_analysis_v2_revised.py --certify
python src/make_figures.py
```

Everything reproduces from `data/raw/` with no cloud access and no
credentials. Analysis-only reproduction needs just numpy, scipy and
matplotlib from the requirements file.

Note: the scripts locate their inputs relative to their own directory and
expect the files under a `results/` folder (`src/results/`), so run
`./reproduce.sh` first: it copies `data/*`, `ledger/*` and the rate-archive
text files into `src/results/` and then runs the two commands above. Running
the second command directly in this layout exits with "no result files
found". Outputs land in `src/results/` (a new `thesis_stats_CERTIFIED_<date>_*.json`
freeze and `thesis_stats_v2.json`) and `src/figures/`; both are ignored by git.
`make_figures.py` defaults to the 2026-09-01 freeze and therefore regenerates
the pre-correction `fig_qfc_H4.png`; pass
`--stats src/results/thesis_stats_CERTIFIED_2026-09-26_v2.json` for the
corrected S6 figure.

## The S6 correction (why there are two pipelines)

`src/thesis_analysis.py` is the pipeline of record. Its S6 stage (the
quality-for-cost ranking behind H4) priced volume-metered devices at the wrong
device's rate: every provider's native price fell through to the Braket IonQ
Forte shot rate used for the counterfactual grid, so Rigetti and IQM Garnet
records were priced at $0.08/shot instead of their own archived rates.
`src/thesis_analysis_v2_revised.py` corrects this (correction dated
2026-09-26): each provider's volume meter is priced at its own archived shot
rate, and a record with no native price is excluded rather than silently
re-metered. Nothing outside S6 changed. Both freezes are in `data/certified/`,
so both readings are checkable: `thesis_stats_CERTIFIED_2026-09-01.json` (v1)
and `thesis_stats_CERTIFIED_2026-09-26_v2.json` (v2).

## Warning: re-executing on hardware costs real money

`thesis_run.py`, `run_benchmark.py`, `measure_all.py` and `azure_runner.py`
submit paid jobs to IBM, AWS Braket and Azure Quantum when given credentials.
The rates in `config.yaml` and the `RATES` dicts are August–September 2026
snapshots and have drifted since; re-verify every rate against the provider's
current pricing page before drawing any cost conclusion, and use the
`--dry-run` / `--estimate-only` modes and the budget guard in `thesis_run.py`
before any paid submission. Nothing in the offline reproduction spends money.

## Licence and citation

Code (`src/`, `reproduce.sh`) is released under the MIT licence
(`LICENSE-CODE.txt`); data, figures, ledger and rate archive under CC BY 4.0
(`LICENSE-DATA.txt`). Cite using `CITATION.cff`. Archived at Zenodo: this deposit https://doi.org/10.5281/zenodo.23003046 · all versions https://doi.org/10.5281/zenodo.23003045 (the examined snapshot is tag v1.0-thesis, commit 674629a).
Archival version v1.2-cash-correction: TODO-DOI (to be minted as a new version
on the same Zenodo record, concept DOI 10.5281/zenodo.23003045, once the tag is
pushed; see `CHANGELOG.md`).

## Not included

The thesis text and its drafts, the document-generation tooling, and the
operational notes that carry account identifiers and credentials are not part
of this repository.
