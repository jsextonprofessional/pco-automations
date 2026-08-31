"""
Tests for resolve_worksheet() — year-rollover logic.

Gated behind WORKSHEET_NAME looking like a bare 4-digit year: any other
configured name (like "TEST", used throughout this project's manual
testing) is used exactly as given, with no rollover behavior at all.
"""

import pco_script
from tests.fakes import FakeSpreadsheet, FakeWorksheet


def test_non_year_configured_name_used_as_is_no_rollover():
    """The "TEST" tab case — must never trigger rollover logic."""
    test_ws = FakeWorksheet(["existing"])
    sh = FakeSpreadsheet({"TEST": test_ws})
    result = pco_script.resolve_worksheet(sh, "TEST", "2027")
    assert result is test_ws
    assert sh.added == []


def test_matching_year_uses_existing_tab_no_rollover_needed():
    ws_2026 = FakeWorksheet(["existing"])
    sh = FakeSpreadsheet({"2026": ws_2026})
    result = pco_script.resolve_worksheet(sh, "2026", "2026")
    assert result is ws_2026
    assert sh.added == []


def test_year_mismatch_creates_new_worksheet_with_headers():
    ws_2026 = FakeWorksheet(["existing"])
    sh = FakeSpreadsheet({"2026": ws_2026})
    result = pco_script.resolve_worksheet(sh, "2026", "2027")
    assert sh.added == ["2027"]
    assert result.rows[0] == "2027 Song Bank"
    assert result.rows[1] == "Familiar Contemporary Songs"


def test_year_mismatch_reuses_new_year_tab_if_it_already_exists():
    """If the 2027 tab already exists (e.g. created by a previous run
    that partially completed), don't recreate it or lose its data."""
    ws_2026 = FakeWorksheet(["old"])
    ws_2027 = FakeWorksheet(["already here"])
    sh = FakeSpreadsheet({"2026": ws_2026, "2027": ws_2027})
    result = pco_script.resolve_worksheet(sh, "2026", "2027")
    assert result is ws_2027
    assert sh.added == []