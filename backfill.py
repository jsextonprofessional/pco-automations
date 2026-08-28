"""
PCO Song Bank Backfill Script

Fetches all Planning Center plans within a specified date range (or all past plans)
and backfills/syncs songs into Column A of the target Google Sheet in chronological order.
"""

import argparse
import os
import re
from datetime import datetime
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


def get_all_plans(service_type_id=PCO_SERVICE_TYPE_ID):
    """Fetch all plans for the given service type handling pagination."""
    all_plans = []
    url = f"{PCO_BASE}/service_types/{service_type_id}/plans?per_page=100&order=-sort_date"
    while url:
        res = pco_get(url)
        all_plans.extend(res.get("data", []))
        url = res.get("links", {}).get("next")
    return all_plans


def get_plan_songs(service_type_id, plan_id):
    """Fetch song titles from a plan's items."""
    items = pco_get(f"{PCO_BASE}/service_types/{service_type_id}/plans/{plan_id}/items")
    return [
        item["attributes"]["title"].strip()
        for item in items.get("data", [])
        if item["attributes"].get("item_type") == "song" and item["attributes"].get("title")
    ]


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


def run_backfill(start_date=None, end_date=None, service_type_id=PCO_SERVICE_TYPE_ID):
    """
    Backfill songs across plans matching start_date < sort_date <= end_date (or all).
    Dates should be ISO format strings (YYYY-MM-DD).
    """
    print(f"Connecting to Google Sheet '{SPREADSHEET_ID}', tab '{WORKSHEET_NAME}'...")
    gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)

    print(f"Fetching all plans from Planning Center (Service Type {service_type_id})...")
    all_plans = get_all_plans(service_type_id)
    print(f"Total plans retrieved: {len(all_plans)}")

    # Filter plans by date range
    target_plans = []
    for p in all_plans:
        sd = p["attributes"].get("sort_date", "")
        if not sd:
            continue
        dt_str = sd[:10]
        if start_date and dt_str <= start_date:
            continue
        if end_date and dt_str > end_date:
            continue
        target_plans.append(p)

    # Sort plans chronologically ascending so dates are added in order
    target_plans.sort(key=lambda p: p["attributes"].get("sort_date", ""))

    print(f"\nFound {len(target_plans)} plans in date range [{start_date or 'beginning'} to {end_date or 'latest'}]:")
    for p in target_plans:
        sd = p["attributes"].get("sort_date", "")[:10]
        label = p["attributes"].get("dates", "")
        print(f"  Plan {p['id']}: {sd} ({label})")

    if not target_plans:
        print("No matching plans to process.")
        return

    # Cache existing Column A state
    existing_songs, last_row = get_column_a_songs(ws)

    for p in target_plans:
        pid = p["id"]
        sd = p["attributes"].get("sort_date", "")
        dt = datetime.fromisoformat(sd.replace("Z", "+00:00"))
        sing_date = f"{dt.month}/{dt.day}"
        date_label = p["attributes"].get("dates", sing_date)

        songs = get_plan_songs(service_type_id, pid)
        print(f"\n--- Processing Plan {pid} ({date_label} -> {sing_date}) with {len(songs)} songs ---")

        for full_title in songs:
            base_title = re.split(r"\s+-\s+", full_title)[0].strip()
            match_key = None
            if full_title.lower() in existing_songs:
                match_key = full_title.lower()
            elif base_title.lower() in existing_songs:
                match_key = base_title.lower()

            if match_key:
                entry = existing_songs[match_key]
                row_num = entry["row"]
                existing_dates = entry["dates"]

                tokens = [d.strip() for d in re.split(r",\s*", existing_dates)]
                if sing_date in tokens or any(d.startswith(sing_date) for d in tokens):
                    print(f"  [SKIPPED] {entry['title']} already has {sing_date}")
                    continue

                new_val = f"{entry['title']} {existing_dates}, {sing_date}" if existing_dates else f"{entry['title']} {sing_date}"
                ws.update_cell(row_num, 1, new_val)
                entry["dates"] = f"{existing_dates}, {sing_date}" if existing_dates else sing_date
                entry["raw"] = new_val
                print(f"  [UPDATED] Row {row_num:2d}: {new_val}")
            else:
                new_val = f"{full_title} {sing_date}"
                ws.append_row([new_val])
                last_row += 1
                existing_songs[full_title.lower()] = {
                    "row": last_row,
                    "title": full_title,
                    "dates": sing_date,
                    "raw": new_val,
                }
                if base_title.lower() != full_title.lower():
                    existing_songs[base_title.lower()] = existing_songs[full_title.lower()]
                print(f"  [CREATED] Row {last_row:2d}: {new_val}")

    print("\nBackfill completed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill PCO songs into Google Sheets Column A")
    parser.add_argument("--start", default="2026-05-24", help="Start date (exclusive, YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-08-23", help="End date (inclusive, YYYY-MM-DD)")
    parser.add_argument("--service-type", default=PCO_SERVICE_TYPE_ID, help="PCO Service Type ID")
    args = parser.parse_args()

    run_backfill(start_date=args.start, end_date=args.end, service_type_id=args.service_type)
