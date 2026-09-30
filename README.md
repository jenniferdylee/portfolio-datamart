# Portfolio Financial Data Mart

Turns a multi-entity consolidated P&L workbook into a queryable DuckDB database, so a
metric like DSCR can be pulled for any entity and any period with one query instead of
being rebuilt by hand in Excel each month.

Built during a private-equity internship against a real portfolio workbook. That data is
confidential and **not in this repository**. `make_sample_workbook.py` generates a
synthetic workbook with the same layout quirks, so everything below runs as-is.

```bash
pip install -r requirements.txt

python make_sample_workbook.py        # synthetic workbook -> data/sample_workbook.xlsx
python load_pl.py                     # workbook -> pl.db
python run_sql.py sql/views.sql       # build metric + QA views
python run_sql.py sql/dscr_report.sql # DSCR report + QA summary

pytest                                # 28 tests: header parsing + end-to-end
```

`./run.sh` (macOS/Linux) or `run.bat` (Windows) runs the four pipeline steps.

## The problem

The source workbook has one P&L sheet per entity. Pulling one metric for one date range
means finding the right sheet, row and column, and the column is where it goes wrong.

**Column offsets differ between sheets.** Every entity sheet has the same row layout, but
the same month sits in a different column on each one: in the sample, Jan 2026 is column
`Q` on one sheet and `AG` on another. A formula that hardcodes a column silently reads the
wrong month on the next sheet.

**Two year conventions coexist.** Some entities keep books on an Apr–Mar fiscal year,
labelled `2025-2026`; others use a Jan–Dec calendar year, labelled `2026`. A calendar range
such as *2026 Jan–Jul* straddles two blocks on the first kind of sheet and none on the
second.

**Year labels are sparse and incomplete.** A label is written only on the column that starts
each block, some sheets open with a stub of prior-year months to the *left* of the first
label, and annual `Total` columns sit between blocks.

## Design decisions

**Columns are resolved by parsing the header, never hardcoded.** `resolve_periods()` reads
the label and month rows, detects which dialect the sheet uses, and maps each monthly column
to a `YYYYMM` period id.

**Period assignment follows the labels and is verified against the sequence.** The year
carries forward from each block's label and rolls over when the month number drops
(Dec → Jan); columns left of the first label are filled in by walking backwards. Every
column is then checked against the one to its left: consecutive monthly columns must be
consecutive months. A mistyped label breaks the chain and raises, naming the column and the
expected period, instead of silently shifting a year of data.

An earlier version anchored on the first `Apr` column and assigned the rest by offset. It
worked, but used the labels only once, so a mistyped label was invisible. Reading every
label and cross-checking it against the sequence uses both sources of truth.

**One long-format fact table.** `raw_pl` stores one row per (entity, period, account,
amount) instead of months as columns. Adding an entity or a period adds rows, never schema
changes.

**Metric lines are looked up by label, not recomputed and not addressed by row number.** The
workbook already carries EBITDA and Normalized EBITDA as computed rows, so `v_metric`
selects them by label; a row inserted upstream shifts row numbers but not labels. `raw_pl`
holds subtotals next to their components, so summing across accounts would double count;
the views never do this.

**Normalization is kept visible.** `v_dscr` returns reported EBITDA, normalized EBITDA and
the difference side by side, so every adjustment can be traced back to its source.

**QA is part of the pipeline.** `qa_unmatched_metric`, `qa_missing_metric`,
`qa_duplicate_metric` and `qa_period_gaps` should each return zero rows.
`qa_large_adjustments` flags months where normalization moves EBITDA by more than 10%, for
a human to review.

`qa_unmatched_metric` exists because of a real bug: the loader strips spaces from labels,
but the revenue label in the SQL kept its leading space, so revenue was NULL everywhere and
no check noticed. It is now covered by a QA view and a regression test.

## Notes on the numbers

DSCR here is Normalized EBITDA ÷ borrowings interest expense. Interest is split three ways
in the source (borrowings, lease liabilities, other) and only the borrowings line belongs in
the denominator. The facilities are bullet repayment, interest only, so there is no
scheduled principal: this is effectively an interest coverage measure.

Monthly DSCR is distorted at fiscal year end: annual true-ups land in March for the fiscal
entities and produce a large negative EBITDA in one month. `v_dscr_ttm` gives the
trailing-twelve-month view and returns NULL until a full 12-month window exists.

## Tests

- `tests/test_resolve_periods.py`: header parsing, including fiscal Jan–Mar year
  assignment, prior-year stubs, `Total` columns, identical months at different offsets, and
  mistyped or mixed labels raising.
- `tests/test_pipeline.py`: generates the synthetic workbook, loads it, builds the views,
  and checks every DSCR value against the generator's own numbers, plus the QA views, the
  fiscal-year straddle, and the TTM window.

GitHub Actions runs both, then the full pipeline, on every push.

## Validation on the real data

Against the private source workbook, monthly DSCR for two entities over Jan–Jul 2026
reconciled to the existing Excel calculation, and the zero-row QA views returned no rows.

## Using a different workbook

Put it anywhere (`data/` is git-ignored) and map its sheets to entity codes in
`entities.json` (also git-ignored; see `entities.example.json` for the format):

```bash
python load_pl.py --workbook data/my_workbook.xlsx --entities entities.json --check
```

`--check` prints the column → period mapping it inferred for each sheet.

## Layout

```
make_sample_workbook.py   synthetic workbook with the same layout quirks
load_pl.py                workbook -> pl.db
run_sql.py                runs a .sql file and prints result sets
sql/views.sql             metric views, DSCR, TTM, QA checks
sql/dscr_report.sql       DSCR report + QA summary
tests/                    pytest suite
entities.example.json     sheet -> entity mapping format
```
