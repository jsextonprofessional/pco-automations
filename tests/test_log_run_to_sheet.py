"""
Tests for log_run_to_sheet() — writes to a dedicated 'Run Log' tab,
fully isolated from the song-tracking column (see
test_run_log_style_line_would_be_misparsed_if_ever_in_same_column in
test_get_column_a_songs.py for why that isolation is load-bearing).
"""

import pco_script
from tests.fakes import FakeSpreadsheet, FakeWorksheet


def test_creates_run_log_tab_if_missing():
    sh = FakeSpreadsheet({})
    summary = {"created": ["Song A"], "updated": [], "skipped": []}

    pco_script.log_run_to_sheet(sh, summary, "8/30")

    assert sh.added == ["Run Log"]
    log_ws = sh.worksheet("Run Log")
    assert len(log_ws.rows) == 1
    assert "Service 8/30" in log_ws.rows[0]
    assert "1 created" in log_ws.rows[0]


def test_appends_to_existing_run_log_tab_without_recreating_it():
    existing_log = FakeWorksheet(["previous entry"])
    sh = FakeSpreadsheet({"Run Log": existing_log})
    summary = {"created": [], "updated": ["Song A"], "skipped": []}

    pco_script.log_run_to_sheet(sh, summary, "8/30")

    assert sh.added == []
    assert len(existing_log.rows) == 2
    assert existing_log.rows[0] == "previous entry"
    assert "1 updated" in existing_log.rows[1]