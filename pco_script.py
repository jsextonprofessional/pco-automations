"""
PCO Song Bank Sync

Fetches songs from the most recent Planning Center plan for your service
and updates Column A of the 2026 Google Sheet with the date played.
"""

import os
import re
from datetime import date, datetime
from dotenv import load_dotenv

import gspread
import requests
from requests.auth import HTTPBasicAuth

# Load environment variables from .env
load_dotenv()

# --- Configuration -----------------------------------------------------------

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

# --- Planning Center API ------------------------------------------------------

PCO_BASE = "https://api.planningcenteronline.com/services/v2"
_pco_auth = HTTPBasicAuth(PCO_CLIENT_ID, PCO_SECRET)
_pco_headers = {"Accept": "application/json"}


def pco_get(url, params=None):
    resp = requests.get(url, auth=_pco_auth, params=params, headers=_pco_headers)
    resp.raise_for_status()
    return resp.json()


def get_latest_plan_and_songs(service_type_id=PCO_SERVICE_TYPE_ID):
    """
    Fetch the most recent past/completed plan and its songs.
    Returns (plan_info, song_titles, formatted_date).
    """
    plans = pco_get(
        f"{PCO_BASE}/service_types/{service_type_id}/plans",
        params={"filter": "past", "per_page": 5, "order": "-sort_date"},
    )
    if not plans.get("data"):
        # Fallback to general order by -sort_date <= today if filter: past returns empty
        plans = pco_get(
            f"{PCO_BASE}/service_types/{service_type_id}/plans",
            params={"per_page": 20, "order": "-sort_date"},
        )
    if not plans.get("data"):
        raise RuntimeError(f"No plans found for service type {service_type_id}.")

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

    # Format date as M/D (e.g., 8/23) to match sheet conventions
    if sort_date:
        dt = datetime.fromisoformat(sort_date.replace("Z", "+00:00"))
        sing_date = f"{dt.month}/{dt.day}"
    else:
        today = date.today()
        sing_date = f"{today.month}/{today.day}"

    items = pco_get(f"{PCO_BASE}/service_types/{service_type_id}/plans/{plan_id}/items")
    songs = [
        item["attributes"]["title"].strip()
        for item in items.get("data", [])
        if item["attributes"].get("item_type") == "song" and item["attributes"].get("title")
    ]

    return selected_plan, songs, sing_date


# --- Google Sheets (Column A) -------------------------------------------------

def get_column_a_songs(worksheet):
    """
    Read Column A and return a dictionary:
    {
        normalized_title: {
            'row': row_number,
            'title': original_title,
            'dates': dates_string,
            'raw': raw_cell_value
        }
    }
    """
    col_a_values = worksheet.col_values(1)
    existing_songs = {}

    # Skip header row(s) (first 2 rows)
    for row_idx, val in enumerate(col_a_values, start=1):
        if row_idx <= 2 or not val.strip():
            continue

        raw = val.strip()
        m = _DATE_START_REGEX.match(raw)
        if m:
            title = m.group(1).strip()
            dates = m.group(2).strip()
        else:
            title = raw
            dates = ""

        norm_title = title.lower()
        existing_songs[norm_title] = {
            "row": row_idx,
            "title": title,
            "dates": dates,
            "raw": raw,
        }

    return existing_songs, len(col_a_values)


def sync_songs_to_sheet():
    # 1. Connect to Google Sheets
    print(f"Connecting to Google Sheet '{SPREADSHEET_ID}', tab '{WORKSHEET_NAME}'...")
    gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)

    # 2. Get songs from Planning Center
    print(f"Fetching recent plan and songs from Planning Center (Service Type {PCO_SERVICE_TYPE_ID})...")
    plan, songs, sing_date = get_latest_plan_and_songs(PCO_SERVICE_TYPE_ID)
    plan_date_label = plan["attributes"].get("dates", sing_date)
    print(f"Plan ID {plan['id']} ({plan_date_label}) - Service Date: {sing_date}")
    print(f"Found {len(songs)} song(s): {songs}\n")

    if not songs:
        print("No songs found in this plan.")
        return

    # 3. Read existing songs in Column A
    existing_songs, last_row = get_column_a_songs(ws)

    # 4. Process each song
    for song_title in songs:
        norm_title = song_title.lower()

        if norm_title in existing_songs:
            entry = existing_songs[norm_title]
            row_num = entry["row"]
            existing_dates = entry["dates"]

            # Check if date is already recorded
            date_tokens = [d.strip() for d in re.split(r",\s*", existing_dates)]
            if sing_date in date_tokens or any(d.startswith(sing_date) for d in date_tokens):
                print(f"[SKIPPED] '{entry['title']}' already has date {sing_date} in Row {row_num}")
                continue

            if existing_dates:
                new_val = f"{entry['title']} {existing_dates}, {sing_date}"
            else:
                new_val = f"{entry['title']} {sing_date}"

            ws.update_cell(row_num, 1, new_val)

            # Update local cache
            entry["dates"] = f"{existing_dates}, {sing_date}" if existing_dates else sing_date
            entry["raw"] = new_val

            print(f"[UPDATED] Row {row_num:2d}: '{new_val}'")
        else:
            # Create a new entry at the bottom of Column A
            new_val = f"{song_title} {sing_date}"
            ws.append_row([new_val])
            last_row += 1
            existing_songs[norm_title] = {
                "row": last_row,
                "title": song_title,
                "dates": sing_date,
                "raw": new_val,
            }
            print(f"[CREATED] Row {last_row:2d}: '{new_val}'")


if __name__ == "__main__":
    sync_songs_to_sheet()