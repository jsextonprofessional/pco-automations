"""
PCO Song Bank Sync — tool functions

Same read/write logic as pco_script.py, split into four standalone
functions so each can become an individually-callable tool for the
agent loop. Matches the sheet's existing format: "Title M/D, M/D"
(no colon), header rows skipped, titles matched case-insensitively,
already-recorded dates skipped on rerun.
"""

import os
import re
from datetime import date, datetime
from dotenv import load_dotenv

import gspread
import requests
from requests.auth import HTTPBasicAuth

load_dotenv()

PCO_CLIENT_ID = os.getenv("PCO_CLIENT_ID")
PCO_SECRET = os.getenv("PCO_SECRET")
PCO_SERVICE_TYPE_ID = os.getenv("PCO_SERVICE_TYPE_ID")

SHEETS_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "2026")

if not PCO_CLIENT_ID or not PCO_SECRET:
    raise ValueError("Missing PCO_CLIENT_ID or PCO_SECRET in environment / .env file.")
if not SPREADSHEET_ID:
    raise ValueError("Missing SPREADSHEET_ID in environment / .env file.")

# Matches "Title 1/4, 2/18" or "Title: 1/4, 2/18" or "Title: 2026-01-04"
_DATE_START_REGEX = re.compile(
    r"^(.*?)(?::\s*|\s+)(\d{1,2}/\d{1,2}.*|\d{4}-\d{2}-\d{2}.*)$"
)

PCO_BASE = "https://api.planningcenteronline.com/services/v2"
_pco_auth = HTTPBasicAuth(PCO_CLIENT_ID, PCO_SECRET)
_pco_headers = {"Accept": "application/json"}


def pco_get(url, params=None):
    resp = requests.get(url, auth=_pco_auth, params=params, headers=_pco_headers)
    resp.raise_for_status()
    return resp.json()


def get_this_weeks_songs():
    """
    Song titles + service date (M/D) from the most recently completed plan.
    Returns (song_titles, sing_date).
    """
    plans = pco_get(
        f"{PCO_BASE}/service_types/{PCO_SERVICE_TYPE_ID}/plans",
        params={"filter": "past", "per_page": 5, "order": "-sort_date"},
    )
    if not plans.get("data"):
        plans = pco_get(
            f"{PCO_BASE}/service_types/{PCO_SERVICE_TYPE_ID}/plans",
            params={"per_page": 20, "order": "-sort_date"},
        )
    if not plans.get("data"):
        raise RuntimeError(f"No plans found for service type {PCO_SERVICE_TYPE_ID}.")

    today_str = date.today().isoformat()
    selected_plan = None
    for p in plans["data"]:
        sort_date = p["attributes"].get("sort_date")
        if sort_date and sort_date[:10] <= today_str:
            selected_plan = p
            break
    if not selected_plan:
        selected_plan = plans["data"][0]

    plan_id = selected_plan["id"]
    sort_date = selected_plan["attributes"].get("sort_date", "")
    if sort_date:
        dt = datetime.fromisoformat(sort_date.replace("Z", "+00:00"))
        sing_date = f"{dt.month}/{dt.day}"
    else:
        today = date.today()
        sing_date = f"{today.month}/{today.day}"

    items = pco_get(f"{PCO_BASE}/service_types/{PCO_SERVICE_TYPE_ID}/plans/{plan_id}/items")
    songs = [
        item["attributes"]["title"].strip()
        for item in items.get("data", [])
        if item["attributes"].get("item_type") == "song" and item["attributes"].get("title")
    ]
    return songs, sing_date


def get_existing_songs(ws):
    """
    {normalized_title: {row, title, dates, raw}} read from Column A.

    A row counts as song data only if it matches the "Title <dates>"
    pattern — not based on row position. Real song rows always have at
    least one date, so anything that doesn't match is a header, a
    blank, or stray text, wherever it sits in the column.
    """
    col_a_values = ws.col_values(1)
    existing = {}
    for row_idx, val in enumerate(col_a_values, start=1):
        if not val.strip():
            continue
        raw = val.strip()
        m = _DATE_START_REGEX.match(raw)
        if not m:
            continue
        title, dates = m.group(1).strip(), m.group(2).strip()
        existing[title.lower()] = {"row": row_idx, "title": title, "dates": dates, "raw": raw}
    return existing


def append_date_to_song(ws, entry, sing_date):
    """
    Add sing_date to an existing song's row. Returns None (no-op) if
    that date is already recorded — safe to call on reruns.
    """
    date_tokens = [d.strip() for d in re.split(r",\s*", entry["dates"])] if entry["dates"] else []
    if sing_date in date_tokens:
        return None
    new_val = (
        f"{entry['title']} {entry['dates']}, {sing_date}"
        if entry["dates"]
        else f"{entry['title']} {sing_date}"
    )
    ws.update_cell(entry["row"], 1, new_val)
    return new_val


def create_song_entry(ws, title, sing_date):
    """Add a new row at the bottom of Column A."""
    new_val = f"{title} {sing_date}"
    ws.append_row([new_val])
    return new_val


if __name__ == "__main__":
    gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
    ws = gc.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)

    songs, sing_date = get_this_weeks_songs()
    print(f"This week's songs ({sing_date}):", songs)

    existing = get_existing_songs(ws)
    print("Existing tracked songs:", list(existing.keys()))

    for title in songs:
        key = title.lower()
        if key in existing:
            result = append_date_to_song(ws, existing[key], sing_date)
            if result:
                print(f"Updated: {result}")
            else:
                print(f"Skipped (already recorded): {existing[key]['title']}")
        else:
            result = create_song_entry(ws, title, sing_date)
            print(f"Created: {result}")