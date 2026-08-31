"""
Shared in-memory fakes for Google Sheets, used across the test suite.

Previously duplicated across test_sync_songs_to_sheet.py and
test_resolve_worksheet.py, with divergent behavior — one had an
auto-extend guard on update_cell, the other didn't. Same failure mode
as pco_script.py/song_tools.py drifting apart earlier in this project.
One definition here instead.
"""

import gspread


class FakeWorksheet:
    """In-memory worksheet: column A only, 1-indexed rows.

    update_cell auto-extends the row list when writing past its
    current length — matching how a real (pre-sized) Google Sheet
    behaves. A real sheet has empty rows already present even when
    freshly created, so update_cell works on unwritten rows there too.
    """

    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def col_values(self, col):
        assert col == 1
        return list(self.rows)

    def update_cell(self, row, col, value):
        assert col == 1
        while len(self.rows) < row:
            self.rows.append("")
        self.rows[row - 1] = value

    def append_row(self, values):
        self.rows.append(values[0])


class FakeSpreadsheet:
    """In-memory spreadsheet: named worksheets, supports creating new ones."""

    def __init__(self, existing_worksheets=None):
        self._worksheets = dict(existing_worksheets or {})
        self.added = []

    def worksheet(self, name):
        if name not in self._worksheets:
            raise gspread.exceptions.WorksheetNotFound(name)
        return self._worksheets[name]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet([])
        self._worksheets[title] = ws
        self.added.append(title)
        return ws


def patch_gspread(monkeypatch, pco_script_module, spreadsheet):
    """Patches gspread.service_account so pco_script talks to `spreadsheet`
    (a FakeSpreadsheet) instead of a real Google account."""
    from unittest.mock import MagicMock

    fake_client = MagicMock()
    fake_client.open_by_key.return_value = spreadsheet
    monkeypatch.setattr(pco_script_module.gspread, "service_account", lambda filename: fake_client)