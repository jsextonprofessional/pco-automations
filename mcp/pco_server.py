"""
Stage 3 (MCP port): PCO song-tracking tools, exposed as a standalone
MCP server instead of an in-process SDK server.

Same underlying logic as agent-demo/sdk_loop.py's in-process tools —
this file is a thin wrapper. It imports and calls song_tools.py
directly; it does not reimplement any PCO/Sheets API calls. That's
the whole point of the port: same behavior, different transport.

Stage 4: streamable HTTP transport, deployed to Fly.io, bearer-token
authenticated. Run standalone to smoke-test:
    <venv>/bin/python mcp/pco_server.py
Serves on http://127.0.0.1:8000/mcp (or MCP_HOST/MCP_PORT if set) with
no console output on success. Ctrl+C to exit.

Requires MCP_BEARER_TOKEN in the environment — generate one with:
    python -c "import secrets; print(secrets.token_urlsafe(32))"
Every request needs "Authorization: Bearer <that token>" or it's
rejected with a 401 before any tool code runs.

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
from pydantic import AnyHttpUrl
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from song_tools import (
    get_this_weeks_songs as _get_this_weeks_songs,
    get_existing_songs as _get_existing_songs,
    append_date_to_song as _append_date_to_song,
    create_song_entry as _create_song_entry,
    SHEETS_SERVICE_ACCOUNT_FILE,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)

# song_tools.py already raises at import time if SPREADSHEET_ID is unset —
# this assert just narrows the type for the checker from str | None to
# str, since that guarantee doesn't carry across the module boundary.
assert SPREADSHEET_ID is not None

# Host/port/hostname need to be known now, not just at run() time, because
# AuthSettings.resource_server_url is baked into the MCPServer at
# construction. transport_security (below) is still passed to run() —
# that one really is transport-only, per the SDK — but the same
# host/hostname logic feeds both, so it's computed once, here.
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
FLY_APP_NAME = os.environ.get("FLY_APP_NAME")

if FLY_APP_NAME:
    _hostname = f"{FLY_APP_NAME}.fly.dev"
    PUBLIC_BASE_URL = f"https://{_hostname}"
else:
    _hostname = None
    PUBLIC_BASE_URL = f"http://{MCP_HOST}:{MCP_PORT}"

RESOURCE_SERVER_URL = f"{PUBLIC_BASE_URL}/mcp"

# --- Auth: bearer-token resource server ---
#
# Single trusted client today (you), so there's no separate authorization
# server issuing tokens through a login flow — you mint the token yourself
# and hand it to whatever's calling this server. The server's job is only
# the resource-server half: check the Authorization header, accept or
# reject. If a second real client ever needs its own token, add it to
# _VALID_TOKENS (or swap this for a real lookup) rather than sharing one
# token across consumers.
#
# MCP_BEARER_TOKEN must be set — generate one with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
_BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN")
if not _BEARER_TOKEN:
    raise ValueError(
        "Missing MCP_BEARER_TOKEN in environment / .env file. Generate one "
        "with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )
_VALID_TOKENS = {
    _BEARER_TOKEN: AccessToken(token=_BEARER_TOKEN, client_id="pco-agent", scopes=["pco:sync"]),
}


class StaticTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        return _VALID_TOKENS.get(token)


mcp = MCPServer(
    "pco-song-sync",
    token_verifier=StaticTokenVerifier(),
    auth=AuthSettings(
        # No real external authorization server exists yet, so this is a
        # placeholder rather than a live discovery URL — nothing in this
        # setup does a full OAuth discovery round-trip against it, since
        # the one client attaches MCP_BEARER_TOKEN directly. It only needs
        # to be a well-formed URL to satisfy AuthSettings' schema.
        issuer_url=AnyHttpUrl(PUBLIC_BASE_URL),
        resource_server_url=AnyHttpUrl(RESOURCE_SERVER_URL),
        required_scopes=["pco:sync"],
    ),
)

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
    # Local (Phase 1): 127.0.0.1, no explicit transport_security needed —
    # the SDK auto-enables DNS rebinding protection for loopback hosts.
    #
    # Deployed (Phase 2): bind 0.0.0.0 (Fly's proxy terminates TLS and
    # forwards here) and explicitly allowlist the *.fly.dev hostname —
    # without this, every request gets a 421, since Fly's hostname isn't
    # loopback and the auto-protection stops applying.
    security = None
    if _hostname:
        security = TransportSecuritySettings(
            allowed_hosts=[_hostname, f"{_hostname}:*"],
            allowed_origins=[f"https://{_hostname}"],
        )

    mcp.run(
        transport="streamable-http",
        host=MCP_HOST,
        port=MCP_PORT,
        transport_security=security,
        # No sampling, elicitation, or subscriptions here — just tool
        # calls and one resource read, so there's nothing that needs a
        # persistent session. Stateless mode means every request is
        # self-contained, which is what actually makes auto_stop_machines
        # safe to keep: with sessions, a machine stopping between two
        # requests loses the session and the client gets "Session not
        # found" on the next call, which is exactly what just happened.
        stateless_http=True,
    )