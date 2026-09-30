#!/usr/bin/env bash
# Full rebuild: sample workbook -> pl.db -> views -> DSCR report
# Requires: pip install -r requirements.txt
set -euo pipefail
cd "$(dirname "$0")"
[ -f data/sample_workbook.xlsx ] || python make_sample_workbook.py
python load_pl.py "$@"
python run_sql.py sql/views.sql
python run_sql.py sql/dscr_report.sql
