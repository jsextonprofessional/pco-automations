"""
Sets dummy credentials before any test module imports pco_script or
song_tools. Both modules validate required env vars at import time
(fine for the real script — fail fast if creds are missing — but it
would otherwise crash pytest collection anywhere a real .env doesn't
exist, e.g. CI).

Plain assignment, not setdefault: this guarantees tests never
accidentally pick up real credentials from a developer's shell
environment or local .env file. Every real API call is mocked in
every test, so these values are never actually used to talk to a
real service — they only need to exist.
"""

import os

os.environ["PCO_CLIENT_ID"] = "test-client-id"
os.environ["PCO_SECRET"] = "test-secret"
os.environ["PCO_SERVICE_TYPE_ID"] = "test-service-type"
os.environ["SPREADSHEET_ID"] = "test-spreadsheet-id"
os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"] = "test-service-account.json"
os.environ["WORKSHEET_NAME"] = "TEST"