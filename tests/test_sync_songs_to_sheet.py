"""
Integration-style tests for sync_songs_to_sheet() — the full
create/update/skip decision logic, exercised end to end against an
in-memory fake worksheet. Nothing here touches a real Google Sheet or
the real Planning Center API.
"""

from unittest.mock import MagicMock, patch

import pco_script


class FakeWorksheet:
    """In-memory worksheet: column A only, 1-indexed rows."""

    def __init__(self, initial_rows=None):
        self.rows = list(initial_rows or [])  # index 0 = row 1

    def col_values(self, col):
        assert col == 1
        return list(self.rows)

    def update_cell(self, row, col, value):
        assert col == 1
        self.rows[row - 1] = value

    def append_row(self, values):
        self.rows.append(values[0])


def fake_gspread(monkeypatch, initial_rows=None):
    """Patches gspread.service_account so sync_songs_to_sheet() writes
    to an in-memory FakeWorksheet instead of a real Google Sheet."""
    ws = FakeWorksheet(initial_rows)
    fake_sheet = MagicMock()
    fake_sheet.worksheet.return_value = ws
    fake_client = MagicMock()
    fake_client.open_by_key.return_value = fake_sheet
    monkeypatch.setattr(pco_script.gspread, "service_account", lambda filename: fake_client)
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