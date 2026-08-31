"""
Unit tests for get_column_a_songs() — pure parsing logic, no network.

test_no_header_rows_still_reads_row_1_and_2 is the regression test for
the actual bug found in production testing: header detection used to
be based on row position (skip rows 1-2 unconditionally), which
silently dropped real data when a sheet had no header rows. The fix
makes header detection content-based instead.
"""

from pco_script import get_column_a_songs


class FakeWorksheet:
    def __init__(self, values):
        self._values = values

    def col_values(self, col):
        assert col == 1
        return self._values


def test_empty_sheet_returns_empty_dict():
    ws = FakeWorksheet([])
    existing, last_row = get_column_a_songs(ws)
    assert existing == {}
    assert last_row == 0


def test_skips_header_rows_by_content_not_position():
    ws = FakeWorksheet(
        [
            "2026 Song Bank",
            "Familiar Contemporary Songs",
            "Build My Life 1/4, 7/5",
        ]
    )
    existing, _ = get_column_a_songs(ws)
    assert len(existing) == 1
    assert existing["build my life"]["row"] == 3


def test_no_header_rows_still_reads_row_1_and_2():
    """Regression test: this exact layout broke before the fix."""
    ws = FakeWorksheet(
        [
            "Revelation Song 8/30",
            "How Great Thou Art 8/30",
        ]
    )
    existing, _ = get_column_a_songs(ws)
    assert "revelation song" in existing
    assert existing["revelation song"]["row"] == 1
    assert "how great thou art" in existing
    assert existing["how great thou art"]["row"] == 2


def test_parses_multiple_dates():
    ws = FakeWorksheet(["Build My Life 1/4, 7/5, 7/26"])
    existing, _ = get_column_a_songs(ws)
    assert existing["build my life"]["dates"] == "1/4, 7/5, 7/26"


def test_handles_parenthetical_date_annotations():
    ws = FakeWorksheet(["Holy Forever 1/11, 2/18 (Ash Wed), 3/8, 5/17"])
    existing, _ = get_column_a_songs(ws)
    entry = existing["holy forever"]
    assert entry["title"] == "Holy Forever"
    assert "2/18 (Ash Wed)" in entry["dates"]


def test_slash_in_title_not_mistaken_for_date():
    ws = FakeWorksheet(["O Come to the Altar/Ven Ante Su Trono 3/1"])
    existing, _ = get_column_a_songs(ws)
    key = "o come to the altar/ven ante su trono"
    assert key in existing
    assert existing[key]["dates"] == "3/1"


def test_blank_rows_are_skipped_but_counted_in_last_row():
    ws = FakeWorksheet(["Build My Life 1/4", "", "  ", "Holy Forever 1/11"])
    existing, last_row = get_column_a_songs(ws)
    assert len(existing) == 2
    assert last_row == 4  # len(col_a_values) includes the blanks


def test_dict_key_is_lowercase_but_title_preserves_original_casing():
    ws = FakeWorksheet(["BUILD MY LIFE 1/4"])
    existing, _ = get_column_a_songs(ws)
    assert "build my life" in existing
    assert existing["build my life"]["title"] == "BUILD MY LIFE"


def test_run_log_style_line_would_be_misparsed_if_ever_in_same_column():
    """
    Demonstrates why the run log must live in its own tab, not proven
    by argument: a line in the log's actual format gets parsed as a
    bogus song. (Verified by running it, not predicted — the actual
    split point isn't where you'd expect: nothing precedes the leading
    ISO date to act as a separator, so the regex backtracks past it
    entirely and matches on the M/D-style date later in the string
    instead, producing an absurd "title" that swallows most of the line.)
    """
    ws = FakeWorksheet(["2026-08-31 09:00:00 UTC — Service 8/30: 4 created, 0 updated, 0 skipped"])
    existing, _ = get_column_a_songs(ws)
    assert len(existing) == 1  # the collision is real, not hypothetical
    bogus_title = next(iter(existing.values()))["title"]
    assert bogus_title == "2026-08-31 09:00:00 UTC — Service"