# Planning Center Online (PCO) Song Bank Sync

Automates syncing songs from Planning Center Services plans into a Google Sheet Song Bank (Column A).

---

## Features

- **Automated Sync (`pco_script.py`)**: Fetches the most recent service plan from Planning Center Online, reads all songs used in that service, and updates Column A in Google Sheets.
  - **Existing song**: Appends the service date (`M/D`, e.g., `8/23`) to the song's existing date history.
  - **New song**: Appends a new row at the bottom of Column A formatted as `<Song Title> <M/D>`.
  - **Idempotent**: Skips dates that have already been recorded to prevent duplicates.
- **Historical Backfill (`backfill.py`)**: Traverses past plans across any custom date range in chronological order and updates or creates song records.
- **Verification Tools**: Standalone scripts (`verify_pco.py` and `verify_sheets.py`) to test API authentication and permissions independently.

---

## Prerequisites

1. **Python 3.10+**
2. **Planning Center Online Account** with access to Services and API Personal Access Tokens.
3. **Google Cloud Service Account** with Google Sheets API enabled.

---

## Setup Instructions

### 1. Clone the repository & create a virtual environment

```bash
git clone <your-repo-url>
cd pco-automations

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On macOS / Linux:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Google Sheets & Service Account

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **Google Sheets API** and **Google Drive API**.
3. Create a **Service Account** (under IAM & Admin > Service Accounts).
4. Create a new JSON key for the service account and download it into this project directory (e.g., `service_account.json`).
5. Open your target Google Sheet in your browser, click **Share**, and add the `client_email` address from your service account JSON file as an **Editor**.

### 4. Configure Planning Center Online API

1. In Planning Center, go to your account settings > **Developer** > **Personal Access Tokens**.
2. Create a new Personal Access Token.
3. Note the **Application ID / Client ID** and **Secret**.

### 5. Set up Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Planning Center API Credentials
PCO_CLIENT_ID=your_pco_application_id_here
PCO_SECRET=your_pco_secret_here
PCO_SERVICE_TYPE_ID=1196126

# Google Sheets Configuration
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
SPREADSHEET_ID=your_google_spreadsheet_id_here
WORKSHEET_NAME=2026
```

> **Note**: `SPREADSHEET_ID` is found in the Google Sheet URL between `/d/` and `/edit`:
> `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`

---

## Verifying Setup

### Test Planning Center connection:

```bash
python verify_pco.py
```

Lists available service types and recent plans, then prints songs found in a selected plan.

### Test Google Sheets connection:

```bash
python verify_sheets.py
```

Appends a test row to the configured worksheet. Remember to remove the test row from the sheet afterward.

---

## Usage

### 1. Regular Weekly Sync

Runs the latest completed service plan and updates Column A:

```bash
python pco_script.py
```

### 2. Historical Backfill

To backfill multiple plans chronologically:

```bash
# Default backfill range (or edit defaults in script):
python backfill.py

# Custom date range (start date exclusive, end date inclusive in YYYY-MM-DD):
python backfill.py --start 2026-01-01 --end 2026-08-23

# Custom service type ID:
python backfill.py --start 2026-05-24 --end 2026-08-23 --service-type 1196126
```

---

## Security & Sensitive Files Policy

### ⚠️ **DO NOT COMMIT JSON KEY FILES OR `.env`**

The `pco-song-bank-scrape-and-fill-*.json` file (and any Google Cloud service account JSON key) contains private cryptographic keys and credentials.

- **Why it must NOT be committed**: Committing this file grants anyone with repository read access full programmatic authorization to act as your service account and access linked Google Cloud and Google Drive / Sheets resources.
- **How it is protected**:
  - `.gitignore` explicitly ignores `.env` and `*.json` (all credential keys).
  - Never run `git add -f <name>.json` or remove `*.json` from `.gitignore`.
  - Always keep the actual JSON key file stored locally on your machine and referenced via `GOOGLE_SERVICE_ACCOUNT_FILE` in your local `.env`.
- **Handling new environments / machines**:
  - When cloning this repo elsewhere, generate or download a new service account key from Google Cloud Console (or securely copy your existing key via a password manager / secure channel).
  - Place it in the directory and set `GOOGLE_SERVICE_ACCOUNT_FILE=<your-key-filename>.json` in that machine's `.env`.
- **If a key is accidentally committed/pushed**:
  1. Immediately delete or disable the key in [Google Cloud IAM & Admin > Service Accounts > Keys](https://console.cloud.google.com/iam-admin/serviceaccounts).
  2. Generate a new key and update your local `.env`.
  3. Revoke any leaked Personal Access Tokens in Planning Center and issue new ones.
