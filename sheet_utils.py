"""
Shared Google Sheets logic between pco_script.py and backfill.py.

Consolidated after the same header-detection bug appeared independently
in both files (and a third time in song_tools.py), and to guarantee the
weekly sync and the historical backfill create/format year worksheets
and run-log entries identically, rather than by two people remembering
to keep two copies in sync.
"""

import re
from datetime import datetime, timezone

import gspread

# Matches "Title 1/4, 2/18" or "Title: 1/4, 2/18" or "Title: 2026-01-04"
DATE_START_REGEX = re.compile(
    r"^(.*?)(?::\s*|\s+)(\d{1,2}/\d{1,2}.*|\d{4}-\d{2}-\d{2}.*)$"
)

RUN_LOG_WORKSHEET_NAME = "Run Log"


def get_column_a_songs(worksheet):
    """
    Read Column A and return (existing_songs, last_content_row):
    - existing_songs: {normalized_title: {row, title, dates, raw}}
    - last_content_row: the row number of the LAST NON-BLANK cell in
      column A specifically.

    Deliberately NOT len(col_a_values). gspread's col_values() can pad
    its returned list with blank entries up to the sheet's overall
    used-row extent when OTHER columns have data further down than
    column A does — trusting len() there was the root cause of new
    entries landing far below where column A's real content actually
    ends, once a sheet has pre-existing data in other columns.
    """
    col_a_values = worksheet.col_values(1)
    existing_songs = {}
    last_content_row = 0
    for row_idx, val in enumerate(col_a_values, start=1):
        if not val.strip():
            continue
        last_content_row = row_idx
        raw = val.strip()
        m = DATE_START_REGEX.match(raw)
        if not m:
            continue
        title = m.group(1).strip()
        dates = m.group(2).strip()
        existing_songs[title.lower()] = {
            "row": row_idx,
            "title": title,
            "dates": dates,
            "raw": raw,
        }
    return existing_songs, last_content_row


def write_new_song_row(worksheet, row_num, value):
    """
    Write a new song entry at an explicit row, in column A only.

    Deliberately NOT append_row(). append_row()'s automatic placement
    isn't scoped to a single column — on a sheet with pre-existing data
    in other columns, it can insert starting at the wrong row or even
    the wrong column, since it's detecting a "table range" based on the
    whole sheet, not specifically column A. update_cell with an
    explicit row/column has no such ambiguity.
    """
    worksheet.update_cell(row_num, 1, value)


def get_or_create_year_worksheet(sh, year_str):
    """
    Return the worksheet named year_str, creating it with standard
    headers ("<year> Song Bank" / "Familiar Contemporary Songs") if it
    doesn't exist yet. Only column A is ever used, so only column A
    is allocated.

    If a worksheet with this name already exists but its first row
    doesn't match our expected header, it's flagged loudly — this
    usually means the tab predates our automation (a legacy,
    manually-maintained sheet with its own layout), not one we created,
    and writing into it carries real risk of mixing incompatible data.
    """
    try:
        ws = sh.worksheet(year_str)
        first_row = ws.cell(1, 1).value or ""
        if first_row.strip() != f"{year_str} Song Bank":
            print(
                f"[WARNING] Worksheet '{year_str}' already exists but its header "
                f"doesn't match what our automation creates (expected "
                f"'{year_str} Song Bank' in cell A1, found {first_row!r}). "
                f"This looks like a pre-existing sheet, possibly with a different "
                f"layout — check it manually before trusting what gets written here."
            )
        else:
            print(f"Using existing worksheet '{year_str}'.")
        return ws
    except gspread.exceptions.WorksheetNotFound:
        print(f"Creating new worksheet '{year_str}'.")
        ws = sh.add_worksheet(title=year_str, rows=1000, cols=1)
        ws.update_cell(1, 1, f"{year_str} Song Bank")
        ws.update_cell(2, 1, "Familiar Contemporary Songs")
        return ws


def get_or_create_run_log_tab(sh):
    try:
        return sh.worksheet(RUN_LOG_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=RUN_LOG_WORKSHEET_NAME, rows=1000, cols=1)


def log_line_to_run_log(sh, line):
    """Append one already-formatted line to the Run Log tab, at an
    explicitly computed row rather than via append_row() (see
    write_new_song_row for why that's avoided everywhere now)."""
    log_ws = get_or_create_run_log_tab(sh)
    existing_values = log_ws.col_values(1)
    next_row = len(existing_values) + 1
    log_ws.update_cell(next_row, 1, line)


def log_agent_run(sh, script_name, outcome, detail):
    """
    Best-effort: log a script's run outcome to the Run Log tab, tagged
    with which script it was. Swallows any failure here (e.g. the
    Sheets connection itself broke) — logging a run should never mask
    or replace the real error, which the caller already has.
    """
    try:
        line = f"{utc_timestamp()} — {script_name} {outcome}: {detail}"
        log_line_to_run_log(sh, line)
    except Exception as exc:
        print(f"[WARNING] Could not write to '{RUN_LOG_WORKSHEET_NAME}' tab: {exc}")


def utc_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")