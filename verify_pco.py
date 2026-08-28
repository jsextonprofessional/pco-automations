"""
Verify Planning Center read access.

Set PCO_CLIENT_ID and PCO_SECRET in your .env file
(PCO account -> Developer -> Personal Access Tokens), then run:

    python verify_pco.py

It walks you through listing service types and plans, then prints
the song titles found in that plan's items.
"""

import os
from dotenv import load_dotenv
import requests
from requests.auth import HTTPBasicAuth

load_dotenv()

CLIENT_ID = os.getenv("PCO_CLIENT_ID")
SECRET = os.getenv("PCO_SECRET")

if not CLIENT_ID or not SECRET:
    raise ValueError("Missing PCO_CLIENT_ID or PCO_SECRET in environment / .env file.")

BASE = "https://api.planningcenteronline.com/services/v2"
auth = HTTPBasicAuth(CLIENT_ID, SECRET)
headers = {"Accept": "application/json"}


def get(url, params=None):
    resp = requests.get(url, auth=auth, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


def main():
    # Step 1: list service types so you can find the right one's ID
    service_types = get(f"{BASE}/service_types")
    print("Service types:")
    for st in service_types["data"]:
        print(f"  {st['id']}  {st['attributes']['name']}")

    service_type_id = input("\nPaste the service_type_id for your Sunday service: ").strip()

    # Step 2: list recent/upcoming plans for that service type
    plans = get(
        f"{BASE}/service_types/{service_type_id}/plans",
        params={"per_page": 30, "order": "-sort_date"},
    )
    print("\nRecent plans:")
    for p in plans["data"]:
        print(f"  {p['id']}  dates: {p['attributes'].get('dates')}")

    plan_id = input("\nPaste the plan_id for the service you want to check: ").strip()

    # Step 3: get that plan's items and pull out the songs
    items = get(f"{BASE}/service_types/{service_type_id}/plans/{plan_id}/items")

    if not items["data"]:
        print("\nNo items found on this plan.")
        return

    songs = [
        item["attributes"]["title"]
        for item in items["data"]
        if item["attributes"].get("item_type") == "song"
    ]

    if songs:
        print("\nSongs in this plan:")
        for title in songs:
            print(f"  - {title}")
    else:
        print(
            "\nNo items matched item_type == 'song'. "
            "Here are the raw attributes of the first item so you can "
            "check the actual field name/value to filter on:\n"
        )
        print(items["data"][0]["attributes"])


if __name__ == "__main__":
    main()
