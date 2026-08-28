"""
Verify Google Sheets write access.

Before running this:
1. Open your service account's JSON key file and copy the "client_email" value.
2. Open the target Google Sheet, click Share, and add that client_email as an Editor.
   (Skip this and you'll get a 403 PERMISSION_DENIED error below.)
3. Ensure .env has GOOGLE_SERVICE_ACCOUNT_FILE, SPREADSHEET_ID, and WORKSHEET_NAME configured.

Then run:

    python verify_sheets.py
"""

import os
from datetime import date
from dotenv import load_dotenv
import gspread

load_dotenv()

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "2026")

if not SPREADSHEET_ID:
    raise ValueError("Missing SPREADSHEET_ID in environment / .env file.")


def main():
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)

    test_row = ["TEST SONG - DELETE ME " + str(date.today())]
    # test_row should print song and date in one cell
    ws.append_row(test_row)

    print(f"Wrote test row to '{WORKSHEET_NAME}': {test_row}")
    print("Go check the sheet now, confirm the row is there, then delete it.")


if __name__ == "__main__":
    main()
