"""
PCO Song Bank Sync

Fetches songs from the most recent Planning Center plan for your service
and updates Column A of the correct year's Google Sheet with the date
played. Automatically switches to a new year's worksheet when the
service's year no longer matches the configured one.
"""

import os
import re
import sys
from datetime import date, datetime

from dotenv import load_dotenv

import gspread
import requests
from requests.auth import HTTPBasicAuth

from sheet_utils import (
    get_column_a_songs,
    get_or_create_year_worksheet,
    get_or_create_run_log_tab,
    RUN_LOG_WORKSHEET_NAME,
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
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "2026")

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


# --- Google Sheets --------------------------------------------------------


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
    return get_or_create_year_worksheet(sh, target_name)


def log_run_to_sheet(sh, summary, sing_date):
    """
    Append a status line to a dedicated 'Run Log' tab — deliberately
    NOT the same tab as tracked songs. A line in this format matches
    the same "Title <dates>" pattern get_column_a_songs() uses to
    detect real song rows and would get silently parsed as a bogus
    song if it ever ended up in that column.
    """
    line = (
        f"{utc_timestamp()} — Service {sing_date}: "
        f"{len(summary['created'])} created, {len(summary['updated'])} updated, "
        f"{len(summary['skipped'])} skipped"
    )
    log_ws = get_or_create_run_log_tab(sh)
    log_ws.append_row([line])


def _try_log_run(sh, summary, sing_date):
    """log_run_to_sheet, but a failure here (e.g. a transient write
    error on the log tab) shouldn't mark an otherwise-successful sync
    as failed."""
    try:
        log_run_to_sheet(sh, summary, sing_date)
    except Exception as exc:
        print(f"[WARNING] Could not write to '{RUN_LOG_WORKSHEET_NAME}' tab: {exc}")


def _try_log_failure(sh, error_msg):
    """Best-effort: write a failure entry to the Run Log tab. Swallows
    any error here (e.g. if the Sheets connection itself is what broke) —
    the caller already has the real error via stderr and re-raises it."""
    try:
        log_ws = get_or_create_run_log_tab(sh)
        log_ws.append_row([f"{utc_timestamp()} — FAILED: {error_msg}"])
    except Exception as log_exc:
        print(f"[WARNING] Could not log failure to '{RUN_LOG_WORKSHEET_NAME}' tab: {log_exc}", file=sys.stderr)


def sync_songs_to_sheet():
    """
    Sync this week's songs to the sheet.

    Returns a summary dict: {"sing_date", "created", "updated", "skipped"}
    (the latter three are lists of song titles). Raises on unrecoverable
    errors — but first prints a stage-labeled failure message to stderr,
    and (if a Sheets connection was established) logs it to the Run Log
    tab, so a failure is debuggable from the sheet alone, not just by
    re-reading raw CI logs.
    """
    sh = None
    stage = "starting"
    try:
        stage = "fetching plan from Planning Center"
        print(f"Fetching recent plan and songs from Planning Center (Service Type {PCO_SERVICE_TYPE_ID})...")
        plan, songs, sing_date = get_latest_plan_and_songs(PCO_SERVICE_TYPE_ID)
        plan_date_label = plan["attributes"].get("dates", sing_date)
        print(f"Plan ID {plan['id']} ({plan_date_label}) - Service Date: {sing_date}")
        print(f"Found {len(songs)} song(s): {songs}\n")

        sort_date = plan["attributes"].get("sort_date", "")
        service_year = sort_date[:4] if sort_date else str(date.today().year)

        stage = "connecting to Google Sheets"
        print(f"Connecting to Google Sheet '{SPREADSHEET_ID}' (configured tab '{WORKSHEET_NAME}')...")
        gc = gspread.service_account(filename=SHEETS_SERVICE_ACCOUNT_FILE)
        sh = gc.open_by_key(SPREADSHEET_ID)

        stage = "resolving worksheet (year rollover check)"
        ws = resolve_worksheet(sh, WORKSHEET_NAME, service_year)

        summary = {"sing_date": sing_date, "created": [], "updated": [], "skipped": []}

        if not songs:
            print("No songs found in this plan.")
            _try_log_run(sh, summary, sing_date)
            return summary

        stage = "reading existing songs from the sheet"
        existing_songs, last_row = get_column_a_songs(ws)

        stage = "processing songs"
        for song_title in songs:
            norm_title = song_title.lower()

            if norm_title in existing_songs:
                entry = existing_songs[norm_title]
                row_num = entry["row"]
                existing_dates = entry["dates"]

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
                entry["dates"] = f"{existing_dates}, {sing_date}" if existing_dates else sing_date
                entry["raw"] = new_val

                print(f"[UPDATED] Row {row_num:2d}: '{new_val}'")
                summary["updated"].append(entry["title"])
            else:
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

        _try_log_run(sh, summary, sing_date)
        return summary

    except Exception as exc:
        error_msg = f"Failed while {stage}: {exc}"
        print(f"\n[FAILED] {error_msg}", file=sys.stderr)
        if sh is not None:
            _try_log_failure(sh, error_msg)
        raise


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
    except Exception:
        # Detailed failure message (with stage context) and a best-effort
        # Run Log entry are already handled inside sync_songs_to_sheet().
        sys.exit(1)
    sys.exit(0)