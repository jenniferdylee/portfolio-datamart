"""End-to-end: synthetic workbook -> DuckDB -> views, checked against the generator's own numbers."""

from pathlib import Path

import duckdb
import pytest

import make_sample_workbook as sample
from load_pl import DEFAULT_SHEETS, build
from run_sql import run_file

VIEWS = Path(__file__).resolve().parents[1] / "sql" / "views.sql"


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pipeline")
    wb, db = tmp / "sample.xlsx", tmp / "test.db"
    truth = sample.build(wb)
    build(wb, db, DEFAULT_SHEETS, log=lambda *a: None)
    con = duckdb.connect(str(db))
    run_file(con, VIEWS, show=False)
    yield con, truth
    con.close()


CODE = {sheet: ent["code"] for sheet, ent in DEFAULT_SHEETS.items()}


def test_every_entity_month_loaded(pipeline):
    con, truth = pipeline
    got = set(con.execute("SELECT DISTINCT entity_code, period_id FROM raw_pl").fetchall())
    assert got == {(CODE[s], p) for s, p in truth}


@pytest.mark.parametrize("view", [
    "qa_unmatched_metric", "qa_missing_metric", "qa_duplicate_metric", "qa_period_gaps"])
def test_qa_views_are_empty(pipeline, view):
    con, _ = pipeline
    assert con.execute(f"SELECT count(*) FROM {view}").fetchone()[0] == 0


def test_dscr_matches_source(pipeline):
    con, truth = pipeline
    rows = con.execute("SELECT entity_code, period_id, dscr FROM v_dscr").fetchall()
    assert len(rows) == len(truth)
    by_key = {(CODE[s], p): v for (s, p), v in truth.items()}
    for code, period, dscr in rows:
        src = by_key[(code, period)]
        expected = src["VI - ①. Normalized EBITDA"] / src["Borrowings Interest expenses"]
        assert dscr == pytest.approx(expected), (code, period)


def test_revenue_is_populated(pipeline):
    # regression: a label with a leading space in views.sql used to match nothing
    con, _ = pipeline
    nulls = con.execute("SELECT count(*) FROM v_monthly WHERE revenue IS NULL").fetchone()[0]
    assert nulls == 0


def test_fiscal_year_straddle(pipeline):
    # Site B's 2026 Jan-Jul comes from two fiscal blocks and must be seven contiguous months
    con, _ = pipeline
    periods = [r[0] for r in con.execute("""
        SELECT period_id FROM v_dscr
        WHERE entity_code = 'SITE_B' AND period_id BETWEEN 202601 AND 202607
        ORDER BY period_id""").fetchall()]
    assert periods == [202601, 202602, 202603, 202604, 202605, 202606, 202607]


def test_ttm_null_until_full_window(pipeline):
    con, _ = pipeline
    # HoldCo data starts 2025-01, so the first full 12-month window ends 2025-12
    rows = dict(con.execute("""
        SELECT period_id, dscr_ttm FROM v_dscr_ttm WHERE entity_code = 'HOLDCO'""").fetchall())
    assert rows[202511] is None
    assert rows[202512] is not None


def test_large_adjustments_flagged_for_review(pipeline):
    # not an error -- but large normalizations must surface for a human
    con, _ = pipeline
    assert con.execute("SELECT count(*) FROM qa_large_adjustments").fetchone()[0] > 0
