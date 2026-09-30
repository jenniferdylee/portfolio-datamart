"""
Build a synthetic consolidated P&L workbook with the same layout quirks as the
real (private) source, so the pipeline can be run and tested by anyone.

All numbers are generated from a fixed seed. No real company data is involved.

Quirks reproduced on purpose:
  * the same month lands in a different column on every sheet
    (each sheet has a different number of leading / total columns)
  * two year dialects: fiscal Apr-Mar labelled '2025-2026', calendar labelled '2026'
  * year labels only on the first column of each block
  * Site A opens with a stub of prior-year months to the LEFT of its first label
  * 'Total' columns between blocks that must be skipped
  * labels padded with leading spaces, as exported from an accounting system
  * subtotal rows sitting next to their components (summing would double count)
  * a large March true-up on fiscal entities, and some one-time items

    python make_sample_workbook.py                 # -> data/sample_workbook.xlsx
    python make_sample_workbook.py --out other.xlsx
"""

import argparse
import random
from pathlib import Path

import openpyxl

OUT = Path("data/sample_workbook.xlsx")
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "June",
               "July", "Aug", "Sep", "Oct", "Nov", "Dec"]

LAST_PERIOD = 202607   # months after this exist in the header but are blank

# (sheet, dialect, first period shown, blank leading columns, revenue scale)
SHEETS = [
    ("PL - Site A", "fiscal",   202501, 1, 4.9e9),   # starts with a Jan-Mar stub
    ("PL - Site B", "fiscal",   202404, 3, 3.2e9),
    ("PL - Site C", "fiscal",   202404, 2, 1.1e9),
    ("PL - HoldCo", "calendar", 202501, 0, 0.4e9),
    ("PL - OpCo",   "calendar", 202401, 4, 2.6e9),
]

# row layout, identical on every sheet (local-language label, English label)
ROWS = [
    ("Ⅰ. 매출액",               " Ⅰ. Revenue"),
    ("  1. 보관매출액",          "  1. Storage revenue"),
    ("  2. 용역매출액",          "  2. Service revenue"),
    ("Ⅱ. 매출원가",             " Ⅱ. Cost of sales"),
    ("Ⅲ. 매출총이익",           " Ⅲ. Gross profit"),
    ("Ⅳ. 판매비와관리비",       " Ⅳ. SG&A"),
    ("VI. EBITDA",              " VI. EBITDA"),
    ("  일회성 항목",            "  One-time items"),
    ("  VI - ①. 조정 EBITDA",   "  VI - ①. Normalized EBITDA"),
    ("이자수익",                "Interest income"),
    ("차입금 이자비용",          "Borrowings Interest expenses"),
    ("리스부채 이자비용",        "Current lease liabilities Interest expenses"),
    ("기타 이자비용",            "Other Interest Expenses"),
]


def month_seq(start, end):
    """All YYYYMM periods from start to end inclusive."""
    out, p = [], start
    while p <= end:
        out.append(p)
        p = p + (89 if p % 100 == 12 else 1)
    return out


def block_start(period, dialect):
    """True where a new year block (and therefore a year label) begins."""
    m = period % 100
    return m == 4 if dialect == "fiscal" else m == 1


def block_label(period, dialect):
    y = period // 100
    return f"{y}-{y + 1}" if dialect == "fiscal" else str(y)


def block_end(period, dialect):
    m = period % 100
    return m == 3 if dialect == "fiscal" else m == 12


def month_values(rng, period, dialect, scale):
    """One month of P&L lines. Returns a dict keyed by English label (stripped)."""
    storage = scale / 12 * rng.uniform(0.9, 1.1)
    service = storage * rng.uniform(0.05, 0.2)
    revenue = storage + service
    cogs = revenue * rng.uniform(0.35, 0.45)
    sga = revenue * rng.uniform(0.08, 0.12)
    ebitda = revenue - cogs - sga

    # annual true-up lands in the fiscal closing month
    if dialect == "fiscal" and period % 100 == 3:
        ebitda -= revenue * rng.uniform(1.2, 1.8)

    one_time = 0.0
    if rng.random() < 0.25:
        one_time = abs(ebitda) * rng.uniform(0.05, 0.3)

    borrow_int = scale * 0.045 / 12
    return {
        "Ⅰ. Revenue": revenue,
        "1. Storage revenue": storage,
        "2. Service revenue": service,
        "Ⅱ. Cost of sales": cogs,
        "Ⅲ. Gross profit": revenue - cogs,
        "Ⅳ. SG&A": sga,
        "VI. EBITDA": ebitda,
        "One-time items": one_time,
        "VI - ①. Normalized EBITDA": ebitda + one_time,
        "Interest income": revenue * 0.001,
        "Borrowings Interest expenses": borrow_int,
        "Current lease liabilities Interest expenses": borrow_int * 0.1,
        "Other Interest Expenses": borrow_int * 0.02,
    }


def build(out=OUT, seed=7):
    """Write the workbook and return {(sheet, period): {label: value}} for tests."""
    rng = random.Random(seed)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    truth = {}

    for sheet, dialect, first, lead, scale in SHEETS:
        ws = wb.create_sheet(sheet)
        ws.cell(1, 1, f"Monthly income statement - {sheet[5:]}")
        ws.cell(2, 1, "Synthetic data")
        ws.cell(3, 1, "계정과목")
        ws.cell(3, 2, "Account")
        for i, (local, en) in enumerate(ROWS):
            ws.cell(5 + i, 1, local)
            ws.cell(5 + i, 2, en)

        col = 3 + lead
        # the header runs to the end of the current year block, values stop at LAST_PERIOD
        header_end = 202703 if dialect == "fiscal" else 202612
        for p in month_seq(first, header_end):
            if block_start(p, dialect):
                ws.cell(3, col, block_label(p, dialect))
            ws.cell(4, col, MONTH_NAMES[p % 100 - 1])
            if p <= LAST_PERIOD:
                vals = {k: round(v) for k, v in
                        month_values(rng, p, dialect, scale).items()}
                truth[(sheet, p)] = vals
                for i, (_, en) in enumerate(ROWS):
                    ws.cell(5 + i, col, vals[en.strip()])
            col += 1
            if block_end(p, dialect):
                ws.cell(4, col, "Total")   # annual total column -- not a month
                col += 1

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    truth = build(args.out, args.seed)
    print(f"wrote {args.out}  ({len(truth)} entity-months)")


if __name__ == "__main__":
    main()
