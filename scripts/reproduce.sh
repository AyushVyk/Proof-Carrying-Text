#!/usr/bin/env bash
# Rebuild every reported number, table and figure from the stored outputs in
# results/. No model is called. Run from anywhere; PYTHON defaults to `python`.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python}"
export PYTHONPATH=. PYTHONIOENCODING=utf-8
export SOURCE_DATE_EPOCH=1795478400     # fixed PDF timestamp, so the figure is byte-stable

echo "== scores: locked scorer outcomes + manual verdicts -> results/scores.json"
"$PY" tools/analysis/final_scoring.py
echo "== underspecified-query layer -> results/underspecified*.{json,md}"
"$PY" tools/analysis/underspecified.py
echo "== main table + paper numbers -> results/three_domain_table.*, paper_numbers.json"
"$PY" tools/analysis/make_domain_table.py
echo "== figure -> results/figures/tractability.{png,pdf}"
"$PY" tools/analysis/figure_tractability.py
echo "== supplement -> supplement/ (checked against paper_numbers.json)"
"$PY" tools/analysis/export_supplement.py
echo "== balancing probe -> experiments/balancing/output.txt"
"$PY" experiments/balancing/run_balancing.py > experiments/balancing/output.txt
echo "== balancing analysis -> experiments/balancing/analysis.txt"
"$PY" experiments/balancing/analyse_sequential.py > experiments/balancing/analysis.txt
echo "done"
