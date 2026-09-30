"""Unit tests for header parsing: the part of the loader most likely to go wrong silently."""

import openpyxl
import pytest

from load_pl import resolve_periods, year_label


def sheet(labels, months, lead=2):
    """A worksheet whose rows 3/4 hold `labels` / `months`, starting after `lead` columns."""
    ws = openpyxl.Workbook().active
    for i, (lab, mon) in enumerate(zip(labels, months)):
        if lab is not None:
            ws.cell(3, lead + 1 + i, lab)
        if mon is not None:
            ws.cell(4, lead + 1 + i, mon)
    return ws


def by_order(periods):
    return [periods[c] for c in sorted(periods)]


# ------------------------------------------------------------------ year labels

@pytest.mark.parametrize("raw, expected", [
    ("2025-2026", (2025, "fiscal")),
    (" 2025 - 2026 ", (2025, "fiscal")),
    ("2026", (2026, "calendar")),
    (2026, (2026, "calendar")),
    (2026.0, (2026, "calendar")),
    ("Total", (None, None)),
    (None, (None, None)),
])
def test_year_label(raw, expected):
    assert year_label(raw) == expected


# ------------------------------------------------------------------ calendar

def test_calendar_two_blocks():
    labels = ["2025"] + [None] * 11 + ["2026", None, None]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "June", "July",
              "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    periods, dialect = resolve_periods(sheet(labels, months), "cal")
    assert dialect == "calendar"
    assert by_order(periods)[0] == 202501
    assert by_order(periods)[11:] == [202512, 202601, 202602, 202603]


def test_total_columns_are_skipped():
    labels = ["2025", None, None, None, "2026", None]
    months = ["Oct", "Nov", "Dec", "Total", "Jan", "Feb"]
    periods, _ = resolve_periods(sheet(labels, months), "cal")
    assert by_order(periods) == [202510, 202511, 202512, 202601, 202602]
    assert 6 not in periods              # the 'Total' column (lead 2 + 4th) is not a period


def test_calendar_mistyped_label_raises():
    # second block mislabelled 2025 instead of 2026
    labels = ["2025", None, "2025", None]
    months = ["Nov", "Dec", "Jan", "Feb"]
    with pytest.raises(ValueError, match="header inconsistent"):
        resolve_periods(sheet(labels, months), "cal")


# ------------------------------------------------------------------ fiscal

def test_fiscal_jan_to_mar_belong_to_second_year():
    labels = ["2025-2026"] + [None] * 11
    months = ["Apr", "May", "June", "July", "Aug", "Sep",
              "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    periods, dialect = resolve_periods(sheet(labels, months), "fy")
    assert dialect == "fiscal"
    assert by_order(periods)[0] == 202504
    assert by_order(periods)[-3:] == [202601, 202602, 202603]


def test_fiscal_stub_left_of_first_label():
    # Jan-Mar 2025 appear BEFORE the first '2025-2026' label
    labels = [None, None, None, "2025-2026", None]
    months = ["Jan", "Feb", "Mar", "Apr", "May"]
    periods, _ = resolve_periods(sheet(labels, months), "fy")
    assert by_order(periods) == [202501, 202502, 202503, 202504, 202505]


def test_fiscal_range_straddles_blocks():
    # 2026 Jan-Jul spans the end of FY2025-26 and the start of FY2026-27
    labels = [None] * 3 + ["2026-2027"] + [None] * 3
    months = ["Jan", "Feb", "Mar", "Apr", "May", "June", "July"]
    periods, _ = resolve_periods(sheet(labels, months), "fy")
    assert by_order(periods) == [202601, 202602, 202603, 202604, 202605, 202606, 202607]


def test_same_months_at_different_offsets_resolve_identically():
    labels = ["2025-2026"] + [None] * 11
    months = ["Apr", "May", "June", "July", "Aug", "Sep",
              "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
    a, _ = resolve_periods(sheet(labels, months, lead=2), "a")
    b, _ = resolve_periods(sheet(labels, months, lead=7), "b")
    assert min(a) != min(b)              # different columns ...
    assert by_order(a) == by_order(b)    # ... same periods


def test_fiscal_mistyped_label_raises():
    # second block labelled 2025-2026 again instead of 2026-2027
    labels = ["2025-2026"] + [None] * 11 + ["2025-2026"]
    months = ["Apr", "May", "June", "July", "Aug", "Sep",
              "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr"]
    with pytest.raises(ValueError, match="header inconsistent"):
        resolve_periods(sheet(labels, months), "fy")


# ------------------------------------------------------------------ malformed

def test_mixed_dialects_raise():
    labels = ["2025-2026", None, "2026"]
    months = ["Apr", "May", "Jun"]
    with pytest.raises(ValueError, match="mixed or missing"):
        resolve_periods(sheet(labels, months), "mixed")


def test_no_labels_raise():
    with pytest.raises(ValueError, match="mixed or missing"):
        resolve_periods(sheet([None, None], ["Jan", "Feb"]), "none")


def test_no_months_raise():
    with pytest.raises(ValueError, match="no monthly columns"):
        resolve_periods(sheet(["2026"], ["Total"]), "empty")
