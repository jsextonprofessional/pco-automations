"""
PCO Song Bank Backfill Script

Fetches Planning Center plans within a specified date range (or all
plans, if no range given) and backfills songs into Column A of the
correct year's Google Sheet, in chronological order. Each plan's songs
go into the worksheet matching THAT plan's actual year (created
automatically if it doesn't exist yet) — a backfill spanning multiple
years populates each year's tab correctly, rather than dumping every
year's songs into one fixed sheet.
"""

import argparse
import os
import re
import sys
from datetime import datetime

from dotenv import load_dotenv

import gspread
import requests
from requests.auth import HTTPBasicAuth

from sheet_utils import (
    get_column_a_songs,
    get_or_create_year_worksheet,
    RUN_LOG_WORKSHEET_NAME,
    write_new_song_row,
    log_line_to_run_log,
    log_agent_run,
    utc_timestamp,
)

# Load environment variables from .env
load_dotenv()

# --- Configuration -----------------------------------------------------------

PCO_CLIENT_ID = os.getenv("PCO_CLIENT_ID")
PCO_SECRET = os.getenv("PCO_SECRET")
PCO_SERVICE_TYPE_ID = os.getenv("PCO_SERVICE_TYPE_ID")

SHEETS_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

if not PCO_CLIENT_ID or not PCO_SECRET:
    raise ValueError("Missing PCO_CLIENT_ID or PCO_SECRET in environment / .env file.")
if not SPREADSHEET_ID:
    raise ValueError("Missing SPREADSHEET_ID in environment / .env file.")

# --- Planning Center API ------------------------------------------------------

PCO_BASE = "https://api.planningcenteronline.com/services/v2"
_pco_auth = HTTPBasicAuth(PCO_CLIENT_ID, PCO_SECRET)
_pco_headers = {"Accept": "application/json"}


def pco_get(url, params=None):
    resp = requests.get(url, auth=_pco_auth, params=params, headers=_pco_headers)
    resp.raise_for_status()
    return resp.json()


def get_all_plans(service_type_id=PCO_SERVICE_TYPE_ID):
    """Fetch all plans for the given service type, handling pagination."""
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


def run_backfill(start_date=None, end_date=None, service_type_id=PCO_SERVICE_TYPE_ID):
    """
    Backfill songs across plans matching start_date < sort_date <= end_date
    (or all plans, if neither is given). Dates should be ISO format
    strings (YYYY-MM-DD). Each plan's songs are written into the
    worksheet matching that plan's actual year, created automatically
    if it doesn't exist.

    Returns a summary dict: {"plans_processed", "created", "updated", "skipped"}.
    Raises on unrecoverable errors — first logging a failure entry (if a
    Sheets connection was established) that includes the date of the
    last plan being processed, so a resumed run knows exactly where to
    pick up with --start.
    """
    sh = None
    stage = "starting"
    current_plan_date = None
    try:
        stage = "connecting to Google Sheets"
        print(f"Connecting to Google Sheet '{SPREADSHEET_ID}'...")
        gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
        sh = gc.open_by_key(SPREADSHEET_ID)

        stage = "fetching all plans from Planning Center"
        print(f"Fetching all plans from Planning Center (Service Type {service_type_id})...")
        all_plans = get_all_plans(service_type_id)
        print(f"Total plans retrieved: {len(all_plans)}")

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

        print(
            f"\nFound {len(target_plans)} plans in date range "
            f"[{start_date or 'beginning'} to {end_date or 'latest'}]:"
        )
        for p in target_plans:
            sd = p["attributes"].get("sort_date", "")[:10]
            label = p["attributes"].get("dates", "")
            print(f"  Plan {p['id']}: {sd} ({label})")

        summary = {"plans_processed": 0, "created": 0, "updated": 0, "skipped": 0}

        if not target_plans:
            print("No matching plans to process.")
            return summary

        # Per-year cache: {year_str: {"ws": worksheet, "existing": {...}, "last_row": int}}
        # Populated lazily as each year is first encountered while walking
        # plans chronologically, so a multi-year range only connects to each
        # year's worksheet once.
        year_cache = {}

        stage = "processing plans"
        for p in target_plans:
            pid = p["id"]
            sd = p["attributes"].get("sort_date", "")
            current_plan_date = sd[:10]
            dt = datetime.fromisoformat(sd.replace("Z", "+00:00"))
            sing_date = f"{dt.month}/{dt.day}"
            year_str = sd[:4]
            date_label = p["attributes"].get("dates", sing_date)

            if year_str not in year_cache:
                ws = get_or_create_year_worksheet(sh, year_str)
                existing_songs, last_row = get_column_a_songs(ws)
                year_cache[year_str] = {"ws": ws, "existing": existing_songs, "last_row": last_row}
            cache = year_cache[year_str]
            ws = cache["ws"]
            existing_songs = cache["existing"]

            songs = get_plan_songs(service_type_id, pid)
            print(f"\n--- Processing Plan {pid} ({date_label} -> {sing_date}, tab '{year_str}') with {len(songs)} songs ---")
            summary["plans_processed"] += 1

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
                        summary["skipped"] += 1
                        continue

                    new_val = (
                        f"{entry['title']} {existing_dates}, {sing_date}"
                        if existing_dates
                        else f"{entry['title']} {sing_date}"
                    )
                    ws.update_cell(row_num, 1, new_val)
                    entry["dates"] = f"{existing_dates}, {sing_date}" if existing_dates else sing_date
                    entry["raw"] = new_val
                    print(f"  [UPDATED] Row {row_num:2d}: {new_val}")
                    summary["updated"] += 1
                else:
                    new_val = f"{full_title} {sing_date}"
                    cache["last_row"] += 1
                    write_new_song_row(ws, cache["last_row"], new_val)
                    existing_songs[full_title.lower()] = {
                        "row": cache["last_row"],
                        "title": full_title,
                        "dates": sing_date,
                        "raw": new_val,
                    }
                    if base_title.lower() != full_title.lower():
                        existing_songs[base_title.lower()] = existing_songs[full_title.lower()]
                    print(f"  [CREATED] Row {cache['last_row']:2d}: {new_val}")
                    summary["created"] += 1

        print(f"\nBackfill completed successfully! {summary}")

        try:
            years_touched = ", ".join(sorted(year_cache.keys()))
            line = (
                f"{utc_timestamp()} — backfill.py SUCCESS "
                f"[{start_date or 'beginning'} to {end_date or 'latest'}]: "
                f"{summary['plans_processed']} plans, {summary['created']} created, "
                f"{summary['updated']} updated, {summary['skipped']} skipped, years: {years_touched}"
            )
            log_line_to_run_log(sh, line)
        except Exception as exc:
            print(f"[WARNING] Could not write to '{RUN_LOG_WORKSHEET_NAME}' tab: {exc}")

        return summary

    except Exception as exc:
        if current_plan_date:
            error_msg = f"Failed while {stage} (last plan date: {current_plan_date}): {exc}"
        else:
            error_msg = f"Failed while {stage}: {exc}"
        print(f"\n[FAILED] {error_msg}", file=sys.stderr)
        if sh is not None:
            log_agent_run(sh, "backfill.py", "FAILED", error_msg)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill PCO songs into Google Sheets, one tab per year")
    parser.add_argument(
        "--start",
        default=None,
        help="Start date (exclusive, YYYY-MM-DD). Omit to include all plans from the beginning.",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date (inclusive, YYYY-MM-DD). Omit to include all plans through the most recent.",
    )
    parser.add_argument("--service-type", default=PCO_SERVICE_TYPE_ID, help="PCO Service Type ID")
    args = parser.parse_args()

    run_backfill(start_date=args.start, end_date=args.end, service_type_id=args.service_type)