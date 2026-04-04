#!/usr/bin/env bash
# sync-anthropic-token.sh
#
# Syncs the Anthropic OAuth token from CLIProxyAPI (Docker) into
# ~/.local-ai/anthropic-token.json for the patched openusage build.
#
# This path is SAFE — it is NOT read by Claude Code CLI on startup.
# Claude Code reads only ~/.claude/.credentials.json, which we never touch.
#
# Run manually:   ./scripts/sync-anthropic-token.sh
# Run on schedule: launchd agent com.local-ai.sync-anthropic-token (every 15 min)
#
# Token TTL is ~1hr. CLIProxyAPI auto-refreshes it server-side; this script
# just copies the current value so openusage always has a fresh token to read.

set -euo pipefail

OUTPUT_FILE="$HOME/.local-ai/anthropic-token.json"
CONTAINER="cli-proxy-api"
# The Claude OAuth account file in CLIProxyAPI.
# To list all accounts: docker exec cli-proxy-api ls /root/.cli-proxy-api/
CLAUDE_AUTH_FILE="/root/.cli-proxy-api/REDACTED-SERVICE-ACCOUNT.json"

# --- Verify Docker container is running ---
if ! docker ps --filter "name=^${CONTAINER}$" --filter "status=running" --format "{{.Names}}" | grep -q "^${CONTAINER}$"; then
  echo "❌ Container '${CONTAINER}' is not running." >&2
  echo "   cd ~/dev\\ env/local-ai && docker compose up -d" >&2
  exit 1
fi

# --- Extract token from CLIProxyAPI ---
TOKEN_JSON=$(docker exec "$CONTAINER" cat "$CLAUDE_AUTH_FILE" 2>/dev/null) || {
  echo "❌ Could not read token. File may not exist: $CLAUDE_AUTH_FILE" >&2
  echo "Available auth files:" >&2
  docker exec "$CONTAINER" ls /root/.cli-proxy-api/ 2>/dev/null | sed 's/^/   /' >&2
  exit 1
}

# --- Convert CLIProxyAPI format → openusage claudeAiOauth format ---
# CLIProxyAPI: { access_token, refresh_token, expired (ISO8601+tz), email }
# openusage expects: { claudeAiOauth: { accessToken, refreshToken, expiresAt (ms epoch) } }
echo "$TOKEN_JSON" | python3 -c "
import sys, json, os, time
from datetime import datetime

src = json.load(sys.stdin)
output_file = os.path.expanduser('$OUTPUT_FILE')

access_token  = src['access_token']
refresh_token = src['refresh_token']
expired_str   = src['expired']
email         = src.get('email', 'unknown')

# Parse ISO8601 expiry → milliseconds epoch
# CLIProxyAPI uses offsets like +08:00; strptime needs +0800
s = expired_str
for old, new in [('+08:00','+0800'),('+07:00','+0700'),('+09:00','+0900'),
                 ('-04:00','-0400'),('-05:00','-0500'),('-07:00','-0700'),('-08:00','-0800')]:
    s = s.replace(old, new)
try:
    dt = datetime.strptime(s, '%Y-%m-%dT%H:%M:%S%z')
    expires_at_ms = int(dt.timestamp() * 1000)
except Exception:
    # Fallback: 55 minutes from now (safe margin within 1hr TTL)
    expires_at_ms = int(time.time() * 1000) + 55 * 60 * 1000

cred = {'claudeAiOauth': {'accessToken': access_token, 'refreshToken': refresh_token, 'expiresAt': expires_at_ms}}
os.makedirs(os.path.dirname(output_file), exist_ok=True)
with open(output_file, 'w') as f:
    json.dump(cred, f, separators=(',', ':'))  # minified — matches what openusage writes on refresh

print(f'✓ Token synced — {email} — expires {expired_str}')
print(f'  Written to {output_file}')
"
