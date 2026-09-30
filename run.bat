@echo off
REM Full rebuild: sample workbook -> pl.db -> views -> DSCR report
REM Requires: pip install -r requirements.txt

if not exist data\sample_workbook.xlsx python make_sample_workbook.py || exit /b 1
python load_pl.py || exit /b 1
python run_sql.py sql\views.sql || exit /b 1
python run_sql.py sql\dscr_report.sql
