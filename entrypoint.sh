#!/bin/sh
set -e

# The service account key can't live in the repo or the image — it's set as
# a Fly secret (GOOGLE_SERVICE_ACCOUNT_JSON, the raw file contents) and
# written to disk here at container start. song_tools.py's
# GOOGLE_SERVICE_ACCOUNT_FILE default ("service_account.json") then finds it
# relative to /app, same as it does locally.
if [ -n "$GOOGLE_SERVICE_ACCOUNT_JSON" ]; then
  echo "$GOOGLE_SERVICE_ACCOUNT_JSON" > /app/service_account.json
fi

exec python mcp/pco_server.py