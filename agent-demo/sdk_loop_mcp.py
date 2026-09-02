"""
Stage 3 (MCP port): same agent as sdk_loop.py, but sourcing tools from
the standalone mcp/pco_server.py process instead of an in-process SDK
server. Everything else — system prompt, permission logic, logging —
is unchanged, so any behavior difference is attributable to the
transport swap, not the agent design.

Run this and sdk_loop.py back-to-back against the same test worksheet
for the cost/latency diff (both already log per-call cost via
sheet_utils.log_agent_run / whatever cost logging sdk_loop.py added
in Stage 3).

Point WORKSHEET_NAME at a duplicate/test tab before running — same
caution as sdk_loop.py.
"""

import asyncio
import os
import sys

# sheet_utils.py lives at the project root, one level up from
# agent-demo/. Add it to sys.path before importing so this resolves
# regardless of what directory this script is launched from.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    TextBlock,
    PermissionResultAllow,
    PermissionResultDeny,
)

from song_tools import SHEETS_SERVICE_ACCOUNT_FILE, SPREADSHEET_ID, WORKSHEET_NAME
import gspread
from sheet_utils import log_agent_run

gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
sh = gc.open_by_key(SPREADSHEET_ID)

# The MCP server's own get_existing_songs/append_date_to_song calls
# validate against sing_date server-side isn't done here — that logic
# lives in pco_server.py's tool bodies now, not in a permission
# callback with access to shared module state. See note below on
# expected_sing_date.
expected_sing_date_holder: dict[str, str | None] = {"value": None}

# Absolute paths — same interpreter and file you already smoke-tested
# standalone. Adjust if your venv or repo layout differs.
VENV_PYTHON = os.path.join(PROJECT_ROOT, "venv", "bin", "python")
PCO_SERVER_PATH = os.path.join(PROJECT_ROOT, "mcp", "pco_server.py")

WRITE_TOOLS = {"mcp__song__append_date_to_song", "mcp__song__create_song_entry"}


async def can_use_tool(tool_name: str, input_data: dict, context) -> PermissionResultAllow | PermissionResultDeny:
    """
    Same allow/deny shape as sdk_loop.py. One real difference: the
    external server doesn't share Python module state with this
    process, so expected_sing_date can't be set as a side effect of
    the get_this_weeks_songs *tool call* the way sdk_loop.py's version
    does. Instead we parse it out of that tool's own return text the
    first time we see it. Good enough for this diff; a cleaner version
    would have the server return structured JSON instead of a string.
    """
    if tool_name == "mcp__song__get_this_weeks_songs":
        return PermissionResultAllow()

    if tool_name not in WRITE_TOOLS:
        print(f"[permission] DENIED {tool_name} — not a recognized write tool")
        return PermissionResultDeny(message=f"{tool_name} is not permitted.")

    requested_date = input_data.get("sing_date")
    expected = expected_sing_date_holder["value"]
    if expected is not None and requested_date != expected:
        print(
            f"[permission] DENIED {tool_name}({input_data}) — "
            f"date mismatch, expected {expected!r}"
        )
        return PermissionResultDeny(
            message=(
                f"sing_date {requested_date!r} does not match this week's "
                f"actual service date {expected!r}."
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
    model="claude-sonnet-5",
    tools=[],
    mcp_servers={
        "song": {
            "type": "stdio",
            "command": VENV_PYTHON,
            "args": [PCO_SERVER_PATH],
        }
    },
    allowed_tools=[
        "mcp__song__get_this_weeks_songs",
        "mcp__song__get_existing_songs",
    ],
    can_use_tool=can_use_tool,
    system_prompt=SYSTEM_PROMPT,
)


async def main():
    final_text_parts = []
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Run this week's sync.")
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(block.text)
                            final_text_parts.append(block.text)
                            if "sing_date:" in block.text and expected_sing_date_holder["value"] is None:
                                try:
                                    expected_sing_date_holder["value"] = (
                                        block.text.split("sing_date:")[1].strip().rstrip(".")
                                    )
                                except IndexError:
                                    pass
                else:
                    print(msg)
    except Exception as exc:
        log_agent_run(sh, "sdk_loop_mcp.py", "FAILED", str(exc))
        raise

    summary_text = " ".join(final_text_parts)[:150] or "completed with no summary text"
    log_agent_run(sh, "sdk_loop_mcp.py", "SUCCESS", summary_text)


if __name__ == "__main__":
    asyncio.run(main())