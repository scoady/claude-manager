#!/usr/bin/env bash
# Extract Claude OAuth token from macOS Keychain and start the backend.
set -euo pipefail

CRED_JSON=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true)
if [ -z "$CRED_JSON" ]; then
  echo "ERROR: Could not read Claude credentials from macOS Keychain."
  echo "Run 'claude auth login' first to authenticate."
  exit 1
fi

export CLAUDE_CODE_OAUTH_TOKEN
CLAUDE_CODE_OAUTH_TOKEN=$(echo "$CRED_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])")

# Snapshot ~/.claude.json so the container doesn't read a partially-written file
# (the host's Claude Code session writes to it non-atomically)
SNAPSHOT_DIR="$(cd "$(dirname "$0")/.." && pwd)/.claude-snapshot"
mkdir -p "$SNAPSHOT_DIR"
cp "$HOME/.claude.json" "$SNAPSHOT_DIR/claude.json"
export CLAUDE_JSON_SNAPSHOT="$SNAPSHOT_DIR/claude.json"

echo "OAuth token loaded from Keychain (${#CLAUDE_CODE_OAUTH_TOKEN} chars)"
exec docker compose up --build "$@"
