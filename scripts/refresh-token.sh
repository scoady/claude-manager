#!/usr/bin/env bash
# Refresh the Claude OAuth token from macOS Keychain.
# Called by launchd every 4 hours (see scripts/install-token-refresh.sh).
set -euo pipefail

TOKEN_FILE="${HOME}/git/claude-manager/.claude-snapshot/oauth-token"

CRED_JSON=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true)
if [ -z "$CRED_JSON" ]; then
  echo "$(date): Keychain lookup failed — is Claude Code logged in?" >&2
  exit 1
fi

mkdir -p "$(dirname "$TOKEN_FILE")"
echo "$CRED_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])" > "$TOKEN_FILE"
echo "$(date): Token refreshed ($(wc -c < "$TOKEN_FILE" | tr -d ' ') chars)"
