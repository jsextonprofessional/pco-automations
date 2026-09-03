"""
Stage 4 (final step): same agent as sdk_loop_mcp.py, but connecting to
the deployed Fly server over HTTPS instead of spawning a local
mcp/pco_server.py subprocess via stdio. This is the actual Phase 4
check — a client that never touches the local filesystem for the
server, talking to it purely over the network with a bearer token,
same as any real remote consumer would.

Everything except mcp_servers is identical to sdk_loop_mcp.py — same
permission logic, system prompt, and logging — so a successful run
here is attributable to the remote transport working, not a different
agent design.

Requires two env vars beyond the usual .env.mcp-test set:
    PCO_MCP_SERVER_URL   e.g. https://pco-mcp-server-aged-breeze-6434.fly.dev/mcp
    MCP_BEARER_TOKEN     the same token set as a Fly secret on the server

Point WORKSHEET_NAME at a duplicate/test tab before running — same
caution as every other script in this project. Note this env var is
read here only for the local log_agent_run() call; the actual sync
writes happen through whatever sheet the deployed server is
configured for via its own Fly secrets, which should be the same test
sheet.
"""

import asyncio
import os
import sys

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

expected_sing_date_holder: dict[str, str | None] = {"value": None}

SERVER_URL = os.environ.get("PCO_MCP_SERVER_URL")
BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN")
if not SERVER_URL:
    raise ValueError("Missing PCO_MCP_SERVER_URL in environment / .env file.")
if not BEARER_TOKEN:
    raise ValueError("Missing MCP_BEARER_TOKEN in environment / .env file.")

WRITE_TOOLS = {"mcp__song__append_date_to_song", "mcp__song__create_song_entry"}


async def can_use_tool(tool_name: str, input_data: dict, context) -> PermissionResultAllow | PermissionResultDeny:
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
            "type": "http",
            "url": SERVER_URL,
            "headers": {"Authorization": f"Bearer {BEARER_TOKEN}"},
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
        log_agent_run(sh, "sdk_loop_remote.py", "FAILED", str(exc))
        raise

    summary_text = " ".join(final_text_parts)[:150] or "completed with no summary text"
    log_agent_run(sh, "sdk_loop_remote.py", "SUCCESS", summary_text)


if __name__ == "__main__":
    asyncio.run(main())