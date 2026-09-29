# Changelog

All notable changes to this replication package. Format follows
Keep a Changelog; newest first. Dates are the commit dates in git.

## [v1.2-cash-correction] – 2026-09-30
### Corrected
- Cash outlay. Previous versions recorded cash as $0.00 on every ledger row and
  in ledger_reconciliation.{csv,json}, on the expectation, entered in ledger.csv
  on 2026-08-17 as two azure_ionq credits_covered_adjustment rows (-168.36,
  -25.81), that Azure promotional credit would absorb the two gate-token bills.
  Microsoft invoice G182569155 (dated 2026-09-09, billing period 2026-08-16 to
  2026-08-31) shows "Azure Credit 0": subtotal GBP 151.70 (= (168.2 + 25.79) AQT
  x GBP 0.78201), VAT GBP 30.34, total GBP 182.04, charged to a personal card.
  reconcile_ledger.py now derives cash per row (Azure: invoiced amount; Braket:
  USD 120.00 AWS pool then card; IBM: free tier) instead of hard-coding zero.
  ledger.csv is unchanged; its two Azure credit rows are retained verbatim and
  annotated as not honoured.
### Added
- rate_archive/invoices_redacted/G182569155_redacted.pdf and README.txt.
- CHANGELOG.md (this file).
### Changed
- README.md: evidential-flags paragraph, repository map, citation block.
- ledger/ledger_reconciliation.csv and .json regenerated (cash fields only;
  metered totals unchanged: 327.08 rate basis).
- CHECKSUMS.sha256 refreshed.
### Not changed
- data/raw, data/quarantined, data/certified, figures, ledger/ledger.csv,
  src/thesis_analysis*.py, src/make_figures.py. No statistic, figure or
  hypothesis verdict in the thesis depends on the cash fields.

## [v1.1-deposit] – 2026-09-27 (commit d0937fb)
- Archival deposit: .zenodo.json added, CITATION.cff rewritten as valid CFF
  1.2.0 and versioned v1.1-deposit. Includes the dated rate-page captures
  (rate_archive/captures/, added 2026-09-26) and the Zenodo DOI
  10.5281/zenodo.23003046 backfilled into CITATION.cff and README on
  2026-09-27 (commit 92720ca, after the tag).

## [v1.0-thesis] – 2026-09-26 (commit 674629a)
- Examined snapshot: instrument, raw records, certified statistics, ledger,
  rate archive, figures.
