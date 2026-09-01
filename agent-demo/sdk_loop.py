"""
Stage 3: song-tracking agent rebuilt on Claude Agent SDK.

Same task, same underlying logic as agent_loop.py (stage 2) — reuses
song_tools.py directly, just exposed via @tool + an in-process MCP
server instead of raw JSON tool schemas.

Read tools (get_this_weeks_songs, get_existing_songs) are pre-approved
via allowed_tools — nothing destructive, no need to gate. Write tools
(append_date_to_song, create_song_entry) are deliberately NOT
pre-approved: the first time the model tries one, the SDK's permission
system should block it. That's intentional for this run.

Point WORKSHEET_NAME at a duplicate/test tab before running this —
same reasoning as testing agent_loop.py's write paths in stage 2.
This wiring is unverified until you've watched it work once.
"""

import asyncio
from typing import Any

import gspread
from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    TextBlock,
    PermissionResultAllow,
    PermissionResultDeny,
)

from song_tools import (
    get_this_weeks_songs as _get_this_weeks_songs,
    get_existing_songs as _get_existing_songs,
    append_date_to_song as _append_date_to_song,
    create_song_entry as _create_song_entry,
    SHEETS_SERVICE_ACCOUNT_FILE,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)

gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
ws = gc.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)

# Scratchpad — same role it played in agent_loop.py: translates the
# model's song_title string back into the real row entry.
existing_cache: dict[str, Any] = {}

# Set by get_this_weeks_songs, checked by can_use_tool below — guards
# against a write call using a date that doesn't match this run's actual
# service date.
expected_sing_date: str | None = None


@tool(
    "get_this_weeks_songs",
    "Fetch the songs played in the most recently completed (not upcoming) "
    "worship service. Returns song titles and the service date (M/D). "
    "Takes no input.",
    {},
)
async def get_this_weeks_songs(args: dict[str, Any]) -> dict[str, Any]:
    global expected_sing_date
    songs, sing_date = _get_this_weeks_songs()
    expected_sing_date = sing_date
    return {"content": [{"type": "text", "text": f"Songs: {songs}, sing_date: {sing_date}"}]}


@tool(
    "get_existing_songs",
    "Read the tracking spreadsheet's Column A and return every song already "
    "on record, keyed by title, with their logged dates. Call this before "
    "deciding whether to update or create a row for each of this week's songs. "
    "Takes no input.",
    {},
)
async def get_existing_songs(args: dict[str, Any]) -> dict[str, Any]:
    existing_cache.clear()
    existing_cache.update(_get_existing_songs(ws))
    view = {entry["title"]: entry["dates"] for entry in existing_cache.values()}
    return {"content": [{"type": "text", "text": str(view)}]}


@tool(
    "append_date_to_song",
    "Add this week's service date to a song already tracked in the "
    "spreadsheet. Only call for a title get_existing_songs returned a match "
    "for. Safe to call even if unsure the date is already recorded — a "
    "duplicate date is a no-op.",
    {"song_title": str, "sing_date": str},
)
async def append_date_to_song(args: dict[str, Any]) -> dict[str, Any]:
    key = args["song_title"].strip().lower()
    entry = existing_cache.get(key)
    if entry is None:
        return {
            "content": [{"type": "text", "text": f"No existing row for '{args['song_title']}'."}],
            "is_error": True,
        }
    result = _append_date_to_song(ws, entry, args["sing_date"])
    text = f"Updated: {result}" if result else "Already recorded, no change made."
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "create_song_entry",
    "Add a new row for a song that has never been tracked before. Only call "
    "for a title get_existing_songs did NOT find a match for.",
    {"title": str, "sing_date": str},
)
async def create_song_entry(args: dict[str, Any]) -> dict[str, Any]:
    result = _create_song_entry(ws, args["title"], args["sing_date"])
    return {"content": [{"type": "text", "text": f"Created: {result}"}]}


server = create_sdk_mcp_server(
    name="song",
    version="1.0.0",
    tools=[get_this_weeks_songs, get_existing_songs, append_date_to_song, create_song_entry],
)

WRITE_TOOLS = {"mcp__song__append_date_to_song", "mcp__song__create_song_entry"}


async def can_use_tool(tool_name: str, input_data: dict, context) -> PermissionResultAllow | PermissionResultDeny:
    """
    Only invoked for tools not already pre-approved via allowed_tools —
    i.e. only our two write tools, since the read tools are listed there.
    Validates the write's sing_date actually matches what
    get_this_weeks_songs returned this run, rather than blanket-approving.
    """
    if tool_name not in WRITE_TOOLS:
        print(f"[permission] DENIED {tool_name} — not a recognized write tool")
        return PermissionResultDeny(message=f"{tool_name} is not permitted.")

    requested_date = input_data.get("sing_date")
    if requested_date != expected_sing_date:
        print(
            f"[permission] DENIED {tool_name}({input_data}) — "
            f"date mismatch, expected {expected_sing_date!r}"
        )
        return PermissionResultDeny(
            message=(
                f"sing_date {requested_date!r} does not match this week's "
                f"actual service date {expected_sing_date!r}."
            )
        )

    print(f"[permission] ALLOWED {tool_name}({input_data})")
    return PermissionResultAllow()


SYSTEM_PROMPT = (
    "You sync songs from this week's church service into a tracking spreadsheet. "
    "Call get_this_weeks_songs once, then get_existing_songs once. For each song, "
    "if its title (case-insensitive) appears in get_existing_songs' result, call "
    "append_date_to_song; otherwise call create_song_entry. Use the sing_date from "
    "get_this_weeks_songs for every call. Reply with a short plain-text summary "
    "once every song has been handled."
)

options = ClaudeAgentOptions(
    model="claude-sonnet-5",  # pin explicitly — omitting this let it default to Opus last run
    tools=[],  # no built-in Claude Code tools — confirmed working, keeps cost down
    mcp_servers={"song": server},
    # Read tools pre-approved. Write tools deliberately absent — should
    # trigger the permission system the first time the model tries one.
    allowed_tools=[
        "mcp__song__get_this_weeks_songs",
        "mcp__song__get_existing_songs",
    ],
    can_use_tool=can_use_tool,
    system_prompt=SYSTEM_PROMPT,
)


async def main():
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Run this week's sync.")
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(block.text)
            else:
                print(msg)


if __name__ == "__main__":
    asyncio.run(main())