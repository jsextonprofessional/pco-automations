"""
Stage 3 (MCP port): PCO song-tracking tools, exposed as a standalone
MCP server instead of an in-process SDK server.

Same underlying logic as agent-demo/sdk_loop.py's in-process tools —
this file is a thin wrapper. It imports and calls song_tools.py
directly; it does not reimplement any PCO/Sheets API calls. That's
the whole point of the port: same behavior, different transport.

Run standalone to smoke-test:
    <venv>/bin/python mcp/pco_server.py
Should hang silently (stdio loop running). Ctrl+C to exit.

Point WORKSHEET_NAME at a duplicate/test tab before wiring this into
sdk_loop.py's write paths — same caution as agent_loop.py and
sdk_loop.py's original docstrings.
"""

import json
import os
import sys
from typing import Any

# song_tools.py lives in ../agent-demo relative to this file. Add it to
# sys.path so `from song_tools import ...` resolves regardless of the
# cwd the parent process (Claude Desktop, sdk_loop.py) launches us from.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent-demo")
)

import gspread
from mcp.server.mcpserver import MCPServer

from song_tools import (
    get_this_weeks_songs as _get_this_weeks_songs,
    get_existing_songs as _get_existing_songs,
    append_date_to_song as _append_date_to_song,
    create_song_entry as _create_song_entry,
    SHEETS_SERVICE_ACCOUNT_FILE,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)

mcp = MCPServer("pco-song-sync")

# Sheet handle is opened once at process start and reused across every
# tool call for the life of this server process — same pattern as
# sdk_loop.py's module-level `ws`.
_gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
_sh = _gc.open_by_key(SPREADSHEET_ID)
_ws = _sh.worksheet(WORKSHEET_NAME)

# Scratchpad — mirrors sdk_loop.py's existing_cache. Populated by
# get_existing_songs, read by append_date_to_song. Lives for the life
# of this server process (one Claude session = one process = one cache).
_existing_cache: dict[str, Any] = {}


@mcp.tool()
def get_this_weeks_songs() -> str:
    """Fetch the songs played in the most recently completed (not
    upcoming) worship service. Returns song titles and the service
    date (M/D). Takes no input."""
    songs, sing_date = _get_this_weeks_songs()
    return f"Songs: {songs}, sing_date: {sing_date}"


@mcp.tool()
def get_existing_songs() -> str:
    """Read the tracking spreadsheet's Column A and return every song
    already on record, keyed by title, with their logged dates. Call
    this before deciding whether to update or create a row for each
    of this week's songs. Takes no input."""
    _existing_cache.clear()
    _existing_cache.update(_get_existing_songs(_ws))
    view = {entry["title"]: entry["dates"] for entry in _existing_cache.values()}
    return str(view)


@mcp.tool()
def append_date_to_song(song_title: str, sing_date: str) -> str:
    """Add this week's service date to a song already tracked in the
    spreadsheet. Only call for a title get_existing_songs returned a
    match for. Safe to call even if unsure the date is already
    recorded — a duplicate date is a no-op."""
    key = song_title.strip().lower()
    entry = _existing_cache.get(key)
    if entry is None:
        return f"No existing row for '{song_title}'. Call get_existing_songs first."
    result = _append_date_to_song(_ws, entry, sing_date)
    return f"Updated: {result}" if result else "Already recorded, no change made."


@mcp.tool()
def create_song_entry(title: str, sing_date: str) -> str:
    """Add a new row for a song that has never been tracked before.
    Only call for a title get_existing_songs did NOT find a match for."""
    result = _create_song_entry(_ws, title, sing_date)
    return f"Created: {result}"


@mcp.resource("sheet://tracking/current")
def tracking_sheet_state() -> str:
    """
    Current raw state of the tracking sheet's Column A — every row,
    in order, exactly as stored. This is deliberately NOT the same
    shape as get_existing_songs' return value: that tool returns a
    parsed {title: dates} view for decision-making; this resource
    returns the unparsed source of truth, for a client to read
    directly without spending a tool-call round-trip on it.
    """
    col_a = _ws.col_values(1)
    payload = {
        "worksheet": WORKSHEET_NAME,
        "spreadsheet_id": SPREADSHEET_ID,
        "row_count": len(col_a),
        "rows": col_a,
    }
    return json.dumps(payload, indent=2)


if __name__ == "__main__":
    mcp.run()