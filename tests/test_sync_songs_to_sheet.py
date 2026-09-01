"""
Integration-style tests for sync_songs_to_sheet() — the full
create/update/skip decision logic, exercised end to end against an
in-memory fake worksheet. Nothing here touches a real Google Sheet or
the real Planning Center API.
"""

from unittest.mock import patch

import pytest

import pco_script
from tests.fakes import FakeSpreadsheet, FakeWorksheet, patch_gspread


def fake_gspread(monkeypatch, initial_rows=None, worksheet_name="TEST"):
    """Patches gspread.service_account so sync_songs_to_sheet() writes
    to an in-memory FakeWorksheet instead of a real Google Sheet."""
    ws = FakeWorksheet(initial_rows)
    spreadsheet = FakeSpreadsheet({worksheet_name: ws})
    patch_gspread(monkeypatch, pco_script, spreadsheet)
    return ws


def test_creates_new_songs_on_empty_sheet(monkeypatch):
    ws = fake_gspread(monkeypatch, initial_rows=[])
    fake_plan = {"id": "p1", "attributes": {"dates": "8/30"}}

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, ["Song A", "Song B"], "8/30")):
        summary = pco_script.sync_songs_to_sheet()

    assert summary["created"] == ["Song A", "Song B"]
    assert summary["updated"] == []
    assert summary["skipped"] == []
    assert ws.rows == ["Song A 8/30", "Song B 8/30"]


def test_appends_date_to_existing_song(monkeypatch):
    ws = fake_gspread(monkeypatch, initial_rows=["Song A 8/2, 8/9"])
    fake_plan = {"id": "p1", "attributes": {"dates": "8/30"}}

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, ["Song A"], "8/30")):
        summary = pco_script.sync_songs_to_sheet()

    assert summary["updated"] == ["Song A"]
    assert summary["created"] == []
    assert summary["skipped"] == []
    assert ws.rows == ["Song A 8/2, 8/9, 8/30"]


def test_skips_song_already_recorded_this_date(monkeypatch):
    ws = fake_gspread(monkeypatch, initial_rows=["Song A 8/23, 8/30"])
    fake_plan = {"id": "p1", "attributes": {"dates": "8/30"}}

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, ["Song A"], "8/30")):
        summary = pco_script.sync_songs_to_sheet()

    assert summary["skipped"] == ["Song A"]
    assert summary["updated"] == []
    assert summary["created"] == []
    assert ws.rows == ["Song A 8/23, 8/30"]  # unchanged


def test_mixed_create_update_skip_in_one_run(monkeypatch):
    ws = fake_gspread(
        monkeypatch,
        initial_rows=[
            "2026 Song Bank",
            "Familiar Contemporary Songs",
            "Existing Song 8/23",
            "Already Done Today 8/30",
        ],
    )
    fake_plan = {"id": "p1", "attributes": {"dates": "8/30"}}
    songs = ["New Song", "Existing Song", "Already Done Today"]

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, songs, "8/30")):
        summary = pco_script.sync_songs_to_sheet()

    assert summary["created"] == ["New Song"]
    assert summary["updated"] == ["Existing Song"]
    assert summary["skipped"] == ["Already Done Today"]


def test_no_songs_found_returns_empty_summary_without_error(monkeypatch):
    ws = fake_gspread(monkeypatch, initial_rows=[])
    fake_plan = {"id": "p1", "attributes": {"dates": "8/30"}}

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, [], "8/30")):
        summary = pco_script.sync_songs_to_sheet()

    assert summary == {"sing_date": "8/30", "created": [], "updated": [], "skipped": []}
    assert ws.rows == []


def test_no_header_rows_still_works_end_to_end(monkeypatch):
    """Regression test for the header-detection bug, exercised through
    the full sync path, not just the parsing function in isolation."""
    ws = fake_gspread(monkeypatch, initial_rows=["Song A 8/23"])
    fake_plan = {"id": "p1", "attributes": {"dates": "8/30"}}

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, ["Song A"], "8/30")):
        summary = pco_script.sync_songs_to_sheet()

    assert summary["updated"] == ["Song A"]
    assert ws.rows == ["Song A 8/23, 8/30"]


def test_new_song_lands_at_correct_row_despite_trailing_blanks(monkeypatch):
    """
    Integration-level regression test for the actual production bug:
    a sheet where col_values(1) returns trailing blanks (simulating
    other columns having data further down than column A) must still
    get new songs written immediately after column A's real content —
    not scattered to wherever len(col_a_values) would have pointed.
    """
    ws = fake_gspread(monkeypatch, initial_rows=["Existing Song 8/2", "", "", ""])
    fake_plan = {"id": "p1", "attributes": {"dates": "8/30"}}

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, ["New Song"], "8/30")):
        summary = pco_script.sync_songs_to_sheet()

    assert summary["created"] == ["New Song"]
    assert ws.rows[1] == "New Song 8/30"  # row 2, right after the real content — not row 5
    """Proves the rollover wiring works end to end, not just the
    isolated resolve_worksheet unit — conftest.py sets WORKSHEET_NAME
    to "TEST" by default, so this test overrides it to a bare year to
    exercise the rollover path specifically."""
    monkeypatch.setattr(pco_script, "WORKSHEET_NAME", "2026")

    old_ws = FakeWorksheet(["Old Song 1/1"])
    spreadsheet = FakeSpreadsheet({"2026": old_ws, "Run Log": FakeWorksheet([])})
    patch_gspread(monkeypatch, pco_script, spreadsheet)

    fake_plan = {
        "id": "p1",
        "attributes": {"dates": "January 3, 2027", "sort_date": "2027-01-03T09:00:00Z"},
    }

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, ["New Year Song"], "1/3")):
        summary = pco_script.sync_songs_to_sheet()

    assert spreadsheet.added == ["2027"]
    new_ws = spreadsheet.worksheet("2027")
    assert summary["created"] == ["New Year Song"]
    assert new_ws.rows[-1] == "New Year Song 1/3"
    assert new_ws.rows[0] == "2027 Song Bank"


def test_failure_after_sheets_connection_logs_to_run_log_with_stage(monkeypatch):
    """When something fails partway through — after a Sheets connection
    exists — the failure should land in the Run Log tab with context
    about which stage broke, not just vanish into a stack trace."""
    log_ws = FakeWorksheet([])
    spreadsheet = FakeSpreadsheet({"TEST": FakeWorksheet([]), "Run Log": log_ws})
    patch_gspread(monkeypatch, pco_script, spreadsheet)

    fake_plan = {"id": "p1", "attributes": {"dates": "8/30"}}

    with patch("pco_script.get_latest_plan_and_songs", return_value=(fake_plan, ["Song A"], "8/30")):
        with patch("pco_script.get_column_a_songs", side_effect=RuntimeError("simulated Sheets failure")):
            with pytest.raises(RuntimeError):
                pco_script.sync_songs_to_sheet()

    assert len(log_ws.rows) == 1
    assert "FAILED" in log_ws.rows[0]
    assert "reading existing songs" in log_ws.rows[0]
    assert "simulated Sheets failure" in log_ws.rows[0]


def test_failure_before_sheets_connection_does_not_crash_on_missing_sh(monkeypatch, capsys):
    """If Planning Center itself fails before any Sheets connection
    exists, there's nothing to log to — should still raise cleanly,
    not crash on referencing a connection that was never established."""
    with patch("pco_script.get_latest_plan_and_songs", side_effect=RuntimeError("PCO auth failed")):
        with pytest.raises(RuntimeError):
            pco_script.sync_songs_to_sheet()

    captured = capsys.readouterr()
    assert "FAILED" in captured.err
    assert "fetching plan from Planning Center" in captured.err