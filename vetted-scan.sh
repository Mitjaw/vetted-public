#!/bin/bash
# Daily scan trigger for Vetted.
# Called by launchd at 06:00. Reads credentials from .env in the project dir.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2
  exit 1
fi

# Parse DASHBOARD_PASSWORD from .env (ignores comment lines)
PASSWORD=$(grep -E '^DASHBOARD_PASSWORD=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")

if [ -z "$PASSWORD" ]; then
  echo "ERROR: DASHBOARD_PASSWORD not set in .env" >&2
  exit 1
fi

echo "[$(date)] Triggering daily scan..."
HTTP_STATUS=$(curl -s -o /tmp/vetted-scan-response.txt -w "%{http_code}" \
  -X POST \
  -u "vetted:$PASSWORD" \
  --max-time 30 \
  http://127.0.0.1:8000/admin/scan/all)

if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "303" ]; then
  echo "[$(date)] Scan triggered successfully (HTTP $HTTP_STATUS)."
else
  echo "[$(date)] ERROR: Scan trigger failed (HTTP $HTTP_STATUS)." >&2
  cat /tmp/vetted-scan-response.txt >&2
  exit 1
fi
