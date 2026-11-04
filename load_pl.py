"""
Consolidated P&L workbook -> DuckDB loader

Reads every entity P&L sheet in a consolidated workbook and flattens it into one
long-format fact table:  (entity, period, account, amount)

Why this is not a straight read_excel:
  1. Every entity sheet has the SAME row layout but DIFFERENT column offsets,
     so the same month sits in a different column letter on each sheet.
  2. Some entities keep Apr->Mar fiscal books and some keep Jan->Dec calendar
     books, so a calendar range like 2026 Jan-Jul straddles two fiscal blocks
     on one sheet and sits inside one block on the next.
  3. The year label is sparse (written only on the first column of a block),
     and some sheets open with a stub of the prior year to the LEFT of it.

So columns are resolved by PARSING the header, never by hardcoding letters.

Usage:
    python load_pl.py                           # data/sample_workbook.xlsx -> pl.db
    python load_pl.py --workbook path.xlsx      # any workbook with the same layout
    python load_pl.py --entities entities.json  # sheet -> entity mapping
    python load_pl.py --check                   # also print the inferred mapping
"""

import argparse
import json
import sys
from pathlib import Path

import duckdb
import openpyxl


WORKBOOK = Path("data/sample_workbook.xlsx")
DB_PATH = Path("pl.db")
ENTITIES_FILE = Path("entities.json")   

HEADER_FY_ROW = 3      
HEADER_MONTH_ROW = 4   
FIRST_DATA_ROW = 5
COL_ACCOUNT_LOCAL = 1  
COL_ACCOUNT_EN = 2     

DEFAULT_SHEETS = {
    "PL - Site A": {"code": "SITE_A", "name": "Site A (fiscal Apr-Mar)"},
    "PL - Site B": {"code": "SITE_B", "name": "Site B (fiscal Apr-Mar)"},
    "PL - Site C": {"code": "SITE_C", "name": "Site C (fiscal Apr-Mar)"},
    "PL - HoldCo": {"code": "HOLDCO", "name": "Holding company (calendar)"},
    "PL - OpCo":   {"code": "OPCO",   "name": "Operating company (calendar)"},
}

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}



def month_num(label):
    """'June' -> 6, 'Jan' -> 1, anything else -> None."""
    if label is None:
        return None
    key = str(label).strip().lower()[:3]
    return MONTHS.get(key)


def year_label(label):
    """
    '2025-2026' -> (2025, 'fiscal')   Apr-Mar books
    '2026'      -> (2026, 'calendar') Jan-Dec books
    anything else -> (None, None)
    """
    if label is None:
        return None, None
    if isinstance(label, float) and label.is_integer():
        label = int(label)          
    text = str(label).strip()
    if "-" in text:
        head = text.split("-")[0].strip()
        if head.isdigit() and len(head) == 4:
            return int(head), "fiscal"
    elif text.isdigit() and len(text) == 4:
        return int(text), "calendar"
    return None, None


def next_period(period_id):
    """202612 -> 202701, 202603 -> 202604."""
    return period_id + (89 if period_id % 100 == 12 else 1)


def resolve_periods(ws, sheet_name):
    """
    Return ({column_index: period_id}, dialect) where period_id is YYYYMM.

    Two header dialects:

      fiscal   -- Books run Apr->Mar and the label reads '2025-2026', so a
                  month in Jan-Mar belongs to the SECOND year in the label.
      calendar -- Books run Jan->Dec and the label is a bare '2026'.

    A block label only appears on the column that STARTS the block, so the
    year is carried forward and rolled over when the month number drops
    (Dec -> Jan).  Columns to the left of the first label (a stub of the prior
    year) are filled in by walking backwards from that label.

    Every column is then checked against the one to its left: consecutive
    monthly columns must be consecutive months.  A mistyped block label breaks
    that chain and raises, rather than silently shifting a year of data into
    the wrong period.
    """
    fy_row = [c.value for c in ws[HEADER_FY_ROW]]
    mo_row = [c.value for c in ws[HEADER_MONTH_ROW]]
    width = max(len(fy_row), len(mo_row))
    fy_row += [None] * (width - len(fy_row))
    mo_row += [None] * (width - len(mo_row))

    # ordered monthly columns: (col_index_1based, month, label_year, label_kind)
    # label_year is set ONLY on a column that literally carries a label.
    monthly = []
    for i in range(width):
        m = month_num(mo_row[i])
        if m is not None:
            y, kind = year_label(fy_row[i])
            monthly.append((i + 1, m, y, kind))

    if not monthly:
        raise ValueError(f"{sheet_name}: no monthly columns found in row {HEADER_MONTH_ROW}")

    dialects = {kind for *_, kind in monthly if kind}
    if len(dialects) != 1:
        raise ValueError(f"{sheet_name}: mixed or missing year dialects {dialects or '{}'}")
    dialect = dialects.pop()

    first = next(i for i, t in enumerate(monthly) if t[2] is not None)
    periods = {}

    # Forward from the first labelled column.  A label resets the year (for a
    # fiscal label, to its FIRST year, since books open in April); inside a
    # block the year rolls over when the month number drops.
    cur_year, prev_month = None, None
    for col, m, label_year, _ in monthly[first:]:
        if label_year is not None:
            cur_year = label_year
        elif prev_month is not None and m < prev_month:
            cur_year += 1
        periods[col] = cur_year * 100 + m
        prev_month = m

    # Backward for any columns to the LEFT of the first label.  Walking left
    # the month normally decreases; if it increases we crossed Jan -> Dec.
    for i in range(first - 1, -1, -1):
        col, m = monthly[i][0], monthly[i][1]
        right_col, right_m = monthly[i + 1][0], monthly[i + 1][1]
        right_year = periods[right_col] // 100
        periods[col] = (right_year - 1 if m > right_m else right_year) * 100 + m

    # Continuity check, both dialects: each monthly column must be exactly one
    # month after the monthly column to its left.
    problems = []
    ordered = [periods[col] for col, *_ in monthly]
    for (col, *_), prev, cur in zip(monthly[1:], ordered, ordered[1:]):
        if cur != next_period(prev):
            problems.append(f"col {col}: header gives {cur}, "
                            f"but the previous column implies {next_period(prev)}")

    if problems:
        raise ValueError(f"{sheet_name}: header inconsistent -> " + "; ".join(problems[:5]))

    return periods, dialect


def read_sheet(ws, entity_code, sheet_name):
    """Return rows of (entity_code, period_id, row_index, account_local, account_en, amount)."""
    periods, dialect = resolve_periods(ws, sheet_name)
    out = []
    for row in ws.iter_rows(min_row=FIRST_DATA_ROW, max_row=ws.max_row):
        r = row[0].row
        local = row[COL_ACCOUNT_LOCAL - 1].value
        en = row[COL_ACCOUNT_EN - 1].value
        # Labels are stripped here, so SQL must match them WITHOUT surrounding spaces.
        local = str(local).strip() if local is not None else None
        en = str(en).strip() if en is not None else None
        if not local and not en:
            continue
        for col, period_id in periods.items():
            if col > len(row):
                continue
            v = row[col - 1].value
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append((entity_code, period_id, r, local, en, float(v)))
    return out, periods, dialect


def load_entities(path):
    """Sheet -> {code, name} mapping: entities.json if present, else the sample default."""
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_SHEETS

def build(workbook, db_path, sheets, check=False, log=print):
    """Load every mapped sheet from `workbook` into a fresh DuckDB file at `db_path`."""
    wb = openpyxl.load_workbook(workbook, data_only=True)

    rows, skipped = [], []
    for sheet_name, ent in sheets.items():
        code = ent["code"]
        if sheet_name not in wb.sheetnames:
            skipped.append(f"{sheet_name} (not in workbook)")
            continue
        try:
            data, periods, dialect = read_sheet(wb[sheet_name], code, sheet_name)
        except ValueError as e:
            skipped.append(str(e))
            continue
        rows.extend(data)
        span = f"{min(periods.values())}-{max(periods.values())}"
        log(f"  {code:10s} {len(data):6d} values  periods {span}  [{dialect}]")
        if check:
            for col in sorted(periods)[:3] + sorted(periods)[-3:]:
                log(f"       col {col:3d} -> {periods[col]}")

    for s in skipped:
        log(f"  SKIPPED: {s}")
    if not rows:
        raise RuntimeError("nothing loaded")

    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))

    con.execute("""
        CREATE TABLE raw_pl (
            entity_code   VARCHAR,
            period_id     INTEGER,
            row_index     INTEGER,
            account_local VARCHAR,
            account_en    VARCHAR,
            amount        DOUBLE
        )
    """)
    con.executemany("INSERT INTO raw_pl VALUES (?,?,?,?,?,?)", rows)

    con.execute("""
        CREATE TABLE dim_calendar AS
        SELECT DISTINCT
            period_id,
            period_id // 100                                   AS year,
            period_id %  100                                   AS month,
            CASE WHEN period_id % 100 >= 4
                 THEN period_id // 100 ELSE period_id // 100 - 1
            END                                                AS fiscal_year_start
        FROM raw_pl ORDER BY period_id
    """)

    con.execute("CREATE TABLE dim_entity (entity_code VARCHAR, entity_name VARCHAR)")
    con.executemany("INSERT INTO dim_entity VALUES (?, ?)",
                    [(e["code"], e.get("name", e["code"])) for e in sheets.values()])

    n = con.execute("SELECT count(*) FROM raw_pl").fetchone()[0]
    con.close()
    log(f"\nloaded {n:,} rows into {db_path}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", default=str(WORKBOOK))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--entities", default=str(ENTITIES_FILE),
                    help="JSON sheet->entity mapping (default: entities.json if present)")
    ap.add_argument("--check", action="store_true", help="print inferred header mapping")
    args = ap.parse_args()

    wb_path = Path(args.workbook)
    if not wb_path.exists():
        sys.exit(f"workbook not found: {wb_path}\n"
                 f"run `python make_sample_workbook.py` to create a synthetic one")

    print(f"reading {wb_path}")
    try:
        build(wb_path, args.db, load_entities(args.entities), check=args.check)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
