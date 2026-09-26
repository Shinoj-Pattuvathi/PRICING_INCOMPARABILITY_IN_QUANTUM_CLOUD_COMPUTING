#!/usr/bin/env sh
# Offline reproduction of the certified statistics and figures.
# The analysis scripts resolve their inputs relative to their own location
# (src/results/, src/figures/), so this script first copies the shipped data
# back into that layout, then runs the two analysis entry points.
# Nothing here needs cloud credentials or spends money.
set -eu
cd "$(dirname "$0")"
R=src/results
mkdir -p "$R/pre_barrier" src/figures
cp data/raw/*.json "$R/"
cp data/quarantined/*.json "$R/pre_barrier/"
cp data/certified/*.json "$R/"
cp ledger/ledger.csv ledger/ledger_reconciliation.csv ledger/ledger_reconciliation.json "$R/"
cp rate_archive/azure_targets_2026-08-16.txt rate_archive/azure_prequotes_2026-08-16.txt "$R/"
python src/thesis_analysis_v2_revised.py --certify
python src/make_figures.py
echo "reproduce.sh: done -- outputs in src/results/ and src/figures/"
