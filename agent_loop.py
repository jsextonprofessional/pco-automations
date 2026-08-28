"""
Agent loop for the song-tracking sync.

Wraps the four functions in song_tools.py as model-callable tools.
No framework — just the raw call -> execute -> feed back -> repeat loop.
"""

import json
import os
import time

from dotenv import load_dotenv
import anthropic
import gspread

from song_tools import (
    get_this_weeks_songs,
    get_existing_songs,
    append_date_to_song,
    create_song_entry,
    SHEETS_SERVICE_ACCOUNT_FILE,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)

load_dotenv()

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
ws = gc.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)

TOOLS = [
    {
        "name": "get_this_weeks_songs",
        "description": "Fetch the songs played in the most recently completed (not upcoming) worship service, pulled from Planning Center. Call this first, once per sync run, to find out what needs to be recorded. Returns a list of song titles and the service date formatted as M/D (e.g. \"8/23\"). Takes no input — the service type and account are already configured.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_existing_songs",
        "description": "Read the current state of the tracking spreadsheet's Column A and return every song already on record. Call this once near the start of a sync, before deciding whether to update or create a row for each song from get_this_weeks_songs. Returns a dictionary keyed by lowercased song title, where each entry has the row number, the original (non-lowercased) title, and the comma-separated list of dates already logged for that song. Use the presence or absence of a title in this result to decide between calling append_date_to_song or create_song_entry. Takes no input.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "append_date_to_song",
        "description": "Add this week's service date to a song that is already tracked in the spreadsheet. Only call this for a song title that get_existing_songs returned a match for — calling it on a title with no existing row will fail. It is safe to call even if you're not sure the date was already recorded: if that exact date is already on the row, this is a no-op and nothing is duplicated. Use the exact title as returned by get_existing_songs, not the raw Planning Center title, since matching is case-insensitive but exact-string based.",
        "input_schema": {
            "type": "object",
            "properties": {
                "song_title": {
                    "type": "string",
                    "description": "The song's existing title, exactly as returned by get_existing_songs, used to look up which spreadsheet row to update.",
                },
                "sing_date": {
                    "type": "string",
                    "description": "The service date to add, formatted as M/D (e.g. \"8/23\"), matching the format returned by get_this_weeks_songs.",
                },
            },
            "required": ["song_title", "sing_date"],
        },
    },
    {
        "name": "create_song_entry",
        "description": "Add a brand-new row to the bottom of the spreadsheet's Column A for a song that has never been sung before per this sheet's history. Only call this for a song title that get_existing_songs did NOT find a match for. Calling this for a song that already has a row will create a duplicate entry instead of updating the existing one — always check get_existing_songs first and prefer append_date_to_song if a match exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The song title exactly as it appears in Planning Center (from get_this_weeks_songs), to be recorded as the new row's title.",
                },
                "sing_date": {
                    "type": "string",
                    "description": "The service date this song was sung, formatted as M/D (e.g. \"8/23\").",
                },
            },
            "required": ["title", "sing_date"],
        },
    },
]

# Scratchpad: populated by get_existing_songs, consulted by append_date_to_song
# to translate the model's song_title string back into a real row entry.
existing_cache = {}


def dispatch(name, args):
    if name == "get_this_weeks_songs":
        songs, sing_date = get_this_weeks_songs()
        return {"songs": songs, "sing_date": sing_date}

    if name == "get_existing_songs":
        existing_cache.clear()
        existing_cache.update(get_existing_songs(ws))
        # Model-facing view — no need to expose row numbers.
        return {entry["title"]: entry["dates"] for entry in existing_cache.values()}

    if name == "append_date_to_song":
        key = args["song_title"].strip().lower()
        entry = existing_cache.get(key)
        if entry is None:
            return {"error": f"No existing row found for '{args['song_title']}'. Call get_existing_songs first."}
        result = append_date_to_song(ws, entry, args["sing_date"])
        return {"updated": result} if result else {"skipped": "date already recorded"}

    if name == "create_song_entry":
        result = create_song_entry(ws, args["title"], args["sing_date"])
        return {"created": result}

    return {"error": f"Unknown tool: {name}"}


SYSTEM_PROMPT = (
    "You sync songs from this week's church service into a tracking spreadsheet. "
    "Steps: call get_this_weeks_songs once, then get_existing_songs once. For each "
    "song from this week, check whether its title (case-insensitive) appears in what "
    "get_existing_songs returned — if it does, call append_date_to_song; if it doesn't, "
    "call create_song_entry. Use the sing_date from get_this_weeks_songs for every call. "
    "Once every song from this week has been handled, reply with a short plain-text "
    "summary and make no further tool calls."
)

MAX_ITERS = 10

# claude-sonnet-5 rates, per token — update if pricing changes.
INPUT_RATE_PER_TOKEN = 2.00 / 1_000_000
OUTPUT_RATE_PER_TOKEN = 10.00 / 1_000_000


def run():
    messages = [{"role": "user", "content": "Run this week's sync."}]

    total_input_tokens = 0
    total_output_tokens = 0
    total_latency = 0.0

    for i in range(MAX_ITERS):
        start = time.perf_counter()
        resp = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        latency = time.perf_counter() - start

        in_tok = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        total_input_tokens += in_tok
        total_output_tokens += out_tok
        total_latency += latency
        print(f"[iter {i}] API call: {latency:.2f}s, input={in_tok} tokens, output={out_tok} tokens")

        messages.append({"role": "assistant", "content": resp.content})

        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            final_text = "".join(b.text for b in resp.content if b.type == "text")
            _print_summary(i + 1, total_latency, total_input_tokens, total_output_tokens)
            print(f"\nDone:\n{final_text}")
            return

        results = []
        for call in calls:
            result = dispatch(call.name, call.input)
            print(f"[iter {i}] {call.name}({call.input}) -> {result}")
            results.append(
                {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result)}
            )
        messages.append({"role": "user", "content": results})

    raise RuntimeError(f"Hit MAX_ITERS ({MAX_ITERS}) without finishing.")


def _print_summary(api_calls, total_latency, total_input_tokens, total_output_tokens):
    cost = total_input_tokens * INPUT_RATE_PER_TOKEN + total_output_tokens * OUTPUT_RATE_PER_TOKEN
    print(
        f"\n--- run summary ---\n"
        f"API calls: {api_calls}\n"
        f"Total latency: {total_latency:.2f}s\n"
        f"Tokens: {total_input_tokens} in / {total_output_tokens} out\n"
        f"Estimated cost: ${cost:.5f}"
    )


if __name__ == "__main__":
    run()