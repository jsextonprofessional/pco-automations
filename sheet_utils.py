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
    Read Column A and return a dictionary:
    {normalized_title: {row, title, dates, raw}}

    A row counts as song data only if it matches the "Title <dates>"
    pattern — not based on row position. Real song rows always have at
    least one date, so anything that doesn't match is a header, a
    blank, or stray text, wherever it sits in the column.
    """
    col_a_values = worksheet.col_values(1)
    existing_songs = {}
    for row_idx, val in enumerate(col_a_values, start=1):
        if not val.strip():
            continue
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
    return existing_songs, len(col_a_values)


def get_or_create_year_worksheet(sh, year_str):
    """
    Return the worksheet named year_str, creating it with standard
    headers ("<year> Song Bank" / "Familiar Contemporary Songs") if it
    doesn't exist yet.
    """
    try:
        ws = sh.worksheet(year_str)
        print(f"Using existing worksheet '{year_str}'.")
        return ws
    except gspread.exceptions.WorksheetNotFound:
        print(f"Creating new worksheet '{year_str}'.")
        ws = sh.add_worksheet(title=year_str, rows=1000, cols=5)
        ws.update_cell(1, 1, f"{year_str} Song Bank")
        ws.update_cell(2, 1, "Familiar Contemporary Songs")
        return ws


def get_or_create_run_log_tab(sh):
    try:
        return sh.worksheet(RUN_LOG_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=RUN_LOG_WORKSHEET_NAME, rows=1000, cols=1)


def log_line_to_run_log(sh, line):
    """Append one already-formatted line to the Run Log tab."""
    log_ws = get_or_create_run_log_tab(sh)
    log_ws.append_row([line])


def utc_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")