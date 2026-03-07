#!/usr/bin/env bash
# Refresh the Claude OAuth token from macOS Keychain and write it to
# ~/.claude/oauth-token so that k8s pods (via hostPath mount) can read it.
#
# Intended to run via launchd every 30 minutes. See:
#   scripts/com.claude-manager.oauth-refresh.plist
set -euo pipefail

TOKEN_FILE="$HOME/.claude/oauth-token"
LOG_FILE="$HOME/.claude/oauth-refresh.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"; }

token=""

# Try macOS Keychain first
cred_json=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true)
if [ -n "$cred_json" ]; then
  token=$(echo "$cred_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])" 2>/dev/null || true)
fi

# Fall back to credentials file
if [ -z "$token" ]; then
  creds_file="$HOME/.claude/.credentials.json"
  if [ -f "$creds_file" ]; then
    token=$(python3 -c "import json; print(json.load(open('$creds_file'))['claudeAiOauth']['accessToken'])" 2>/dev/null || true)
  fi
fi

if [ -z "$token" ]; then
  log "ERROR: Could not extract OAuth token"
  exit 1
fi

echo "$token" > "$TOKEN_FILE"
log "OK: token refreshed ($(wc -c < "$TOKEN_FILE" | tr -d ' ') chars)"

# Trim log to last 100 lines
if [ -f "$LOG_FILE" ]; then
  tail -100 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
