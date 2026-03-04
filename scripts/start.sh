#!/usr/bin/env bash
# Extract Claude OAuth token from macOS Keychain and start the backend.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TOKEN_FILE="$PROJECT_DIR/.claude-snapshot/oauth-token"

# ── Extract token from Keychain and write to file ──────────────────────────
_refresh_token() {
  local cred_json
  cred_json=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true)
  if [ -z "$cred_json" ]; then
    echo "ERROR: Could not read Claude credentials from macOS Keychain."
    echo "Run 'claude auth login' first to authenticate."
    return 1
  fi
  mkdir -p "$(dirname "$TOKEN_FILE")"
  echo "$cred_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])" > "$TOKEN_FILE"
  echo "OAuth token refreshed → $TOKEN_FILE ($(wc -c < "$TOKEN_FILE" | tr -d ' ') chars)"
}

_refresh_token || exit 1

# Also export for backwards compat (initial container env)
export CLAUDE_CODE_OAUTH_TOKEN
CLAUDE_CODE_OAUTH_TOKEN=$(cat "$TOKEN_FILE")

# Snapshot ~/.claude.json so the container doesn't read a partially-written file
# (the host's Claude Code session writes to it non-atomically)
SNAPSHOT_DIR="$PROJECT_DIR/.claude-snapshot"
cp "$HOME/.claude.json" "$SNAPSHOT_DIR/claude.json"
export CLAUDE_JSON_SNAPSHOT="$SNAPSHOT_DIR/claude.json"

# GitHub PAT — read from env or file
GH_TOKEN_FILE="$SNAPSHOT_DIR/gh-token"
if [ -z "${GH_TOKEN:-}" ] && [ -f "$GH_TOKEN_FILE" ]; then
  export GH_TOKEN
  GH_TOKEN=$(cat "$GH_TOKEN_FILE")
  echo "GitHub PAT loaded from file (${#GH_TOKEN} chars)"
elif [ -n "${GH_TOKEN:-}" ]; then
  echo "GitHub PAT loaded from env (${#GH_TOKEN} chars)"
fi

echo "OAuth token loaded from Keychain (${#CLAUDE_CODE_OAUTH_TOKEN} chars)"
exec docker compose up --build "$@"
