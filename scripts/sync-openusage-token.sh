#!/usr/bin/env bash
# sync-openusage-token.sh
#
# Copies the current Anthropic OAuth token from CLIProxyAPI (Docker) into
# ~/.claude/.credentials.json so that openusage can show Anthropic usage.
#
# ⚠️  IMPORTANT: This file is read by Claude Code CLI on startup. If you start
# a fresh Claude Code session while this file exists, it will use the OAuth session
# directly instead of the LiteLLM proxy. Remove it after checking usage:
#
#   rm ~/.claude/.credentials.json
#
# Usage:
#   ./scripts/sync-openusage-token.sh          — write credentials, open openusage
#   ./scripts/sync-openusage-token.sh --cleanup — remove credentials (restore proxy routing)

set -euo pipefail

CREDENTIALS_FILE="$HOME/.claude/.credentials.json"
CONTAINER="cli-proxy-api"
# The Claude OAuth account in CLIProxyAPI — update if your account file name changes.
# To see all accounts: docker exec cli-proxy-api ls /root/.cli-proxy-api/
CLAUDE_AUTH_FILE="/root/.cli-proxy-api/REDACTED-SERVICE-ACCOUNT.json"

cleanup() {
  if [[ -f "$CREDENTIALS_FILE" ]]; then
    rm "$CREDENTIALS_FILE"
    echo "✓ Removed $CREDENTIALS_FILE — proxy routing restored."
  else
    echo "Nothing to clean up (file not present)."
  fi
  exit 0
}

[[ "${1:-}" == "--cleanup" ]] && cleanup

# --- Verify Docker container is running ---
if ! docker ps --filter "name=^${CONTAINER}$" --filter "status=running" --format "{{.Names}}" | grep -q "^${CONTAINER}$"; then
  echo "❌ Container '${CONTAINER}' is not running. Start the main stack first:"
  echo "   docker compose up -d"
  exit 1
fi

# --- Extract token from CLIProxyAPI ---
TOKEN_JSON=$(docker exec "$CONTAINER" cat "$CLAUDE_AUTH_FILE" 2>/dev/null) || {
  echo "❌ Could not read token from container. File may not exist:"
  echo "   $CLAUDE_AUTH_FILE"
  echo ""
  echo "Available auth files:"
  docker exec "$CONTAINER" ls /root/.cli-proxy-api/ 2>/dev/null | sed 's/^/   /'
  exit 1
}

ACCESS_TOKEN=$(echo "$TOKEN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
REFRESH_TOKEN=$(echo "$TOKEN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['refresh_token'])")
EXPIRED_STR=$(echo "$TOKEN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['expired'])")

# Convert ISO expiry to Unix timestamp
EXPIRES_AT=$(python3 -c "
from datetime import datetime
import time
s = '${EXPIRED_STR}'.replace('+08:00','+0800').replace('-05:00','-0500').replace('-04:00','-0400')
try:
    dt = datetime.strptime(s, '%Y-%m-%dT%H:%M:%S%z')
    print(int(dt.timestamp()))
except Exception:
    # Fallback: 1 hour from now
    print(int(time.time()) + 3600)
")

# --- Write credentials file ---
mkdir -p "$(dirname "$CREDENTIALS_FILE")"
printf '{"accessToken":"%s","refreshToken":"%s","expiresAt":%s}' \
  "$ACCESS_TOKEN" "$REFRESH_TOKEN" "$EXPIRES_AT" > "$CREDENTIALS_FILE"

echo "✓ Anthropic OAuth token synced to $CREDENTIALS_FILE"
echo "  Account: $(echo "$TOKEN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('email','unknown'))")"
echo "  Expires: $EXPIRED_STR"
echo ""
echo "⚠️  openusage will now show Anthropic usage."
echo "   When done, run:  ./scripts/sync-openusage-token.sh --cleanup"
echo "   Or: rm $CREDENTIALS_FILE"
echo ""
echo "   Do NOT start a new Claude Code terminal session until you clean up"
echo "   (the credentials file would redirect Claude Code away from the LiteLLM proxy)."
