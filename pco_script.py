"""
PCO Song Bank Sync

Fetches songs from the most recent Planning Center plan for your service
and updates Column A of the 2026 Google Sheet with the date played.
"""

import os
import re
import sys
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

    A row counts as song data only if it matches the "Title <dates>"
    pattern — not based on row position. Real song rows always have at
    least one date (create_song_entry never writes a bare title), so
    anything that doesn't match is a header, a blank, or stray text,
    wherever it happens to sit in the column.
    """
    col_a_values = worksheet.col_values(1)
    existing_songs = {}

    for row_idx, val in enumerate(col_a_values, start=1):
        if not val.strip():
            continue

        raw = val.strip()
        m = _DATE_START_REGEX.match(raw)
        if not m:
            # Doesn't look like "Title <dates>" — header row or stray
            # text, not song data. Skip regardless of row position.
            continue

        title = m.group(1).strip()
        dates = m.group(2).strip()
        norm_title = title.lower()
        existing_songs[norm_title] = {
            "row": row_idx,
            "title": title,
            "dates": dates,
            "raw": raw,
        }

    return existing_songs, len(col_a_values)


def resolve_worksheet(sh, configured_name, service_year):
    """
    If configured_name looks like a bare year (e.g. "2026"), treat it as
    a year-tracking tab: when service_year doesn't match, switch to (or
    create) the worksheet named for service_year instead. Any other
    configured_name (e.g. "TEST") is used exactly as given, with no
    rollover behavior — keeps manual/test overrides unaffected.
    """
    if not re.fullmatch(r"\d{4}", configured_name):
        return sh.worksheet(configured_name)

    target_name = str(service_year)
    if target_name == configured_name:
        return sh.worksheet(configured_name)

    print(f"[YEAR ROLLOVER] Service year {target_name} != configured tab '{configured_name}'.")
    try:
        ws = sh.worksheet(target_name)
        print(f"Using existing worksheet '{target_name}'.")
    except gspread.exceptions.WorksheetNotFound:
        print(f"Creating new worksheet '{target_name}'.")
        ws = sh.add_worksheet(title=target_name, rows=1000, cols=5)
        ws.update_cell(1, 1, f"{target_name} Song Bank")
        ws.update_cell(2, 1, "Familiar Contemporary Songs")
    return ws


def sync_songs_to_sheet():
    """
    Sync this week's songs to the sheet.

    Returns a summary dict: {"sing_date", "created", "updated", "skipped"}
    (the latter three are lists of song titles). Raises on unrecoverable
    errors (auth failure, no plan found, etc.) — the caller is
    responsible for catching and reporting those.
    """
    # 1. Get songs from Planning Center first — the worksheet to write
    # to depends on the service's actual year, so this has to happen
    # before connecting to Sheets now.
    print(f"Fetching recent plan and songs from Planning Center (Service Type {PCO_SERVICE_TYPE_ID})...")
    plan, songs, sing_date = get_latest_plan_and_songs(PCO_SERVICE_TYPE_ID)
    plan_date_label = plan["attributes"].get("dates", sing_date)
    print(f"Plan ID {plan['id']} ({plan_date_label}) - Service Date: {sing_date}")
    print(f"Found {len(songs)} song(s): {songs}\n")

    sort_date = plan["attributes"].get("sort_date", "")
    service_year = sort_date[:4] if sort_date else str(date.today().year)

    # 2. Connect to Google Sheets, resolving year-rollover if configured
    print(f"Connecting to Google Sheet '{SPREADSHEET_ID}' (configured tab '{WORKSHEET_NAME}')...")
    gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = resolve_worksheet(sh, WORKSHEET_NAME, service_year)

    summary = {"sing_date": sing_date, "created": [], "updated": [], "skipped": []}

    if not songs:
        print("No songs found in this plan.")
        return summary

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
                summary["skipped"].append(entry["title"])
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
            summary["updated"].append(entry["title"])
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
            summary["created"].append(song_title)

    return summary


def print_summary(summary):
    print("\n--- sync summary ---")
    print(f"Service date: {summary['sing_date']}")
    print(f"Created: {len(summary['created'])} — {summary['created']}")
    print(f"Updated: {len(summary['updated'])} — {summary['updated']}")
    print(f"Skipped (already recorded): {len(summary['skipped'])} — {summary['skipped']}")


if __name__ == "__main__":
    try:
        result = sync_songs_to_sheet()
        print_summary(result)
    except Exception as exc:
        print(f"\n[FAILED] Sync did not complete: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)