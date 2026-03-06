#!/usr/bin/env bash
# Extract Claude OAuth token from macOS Keychain and start the backend.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TOKEN_FILE="$PROJECT_DIR/.claude-snapshot/oauth-token"

# ── Extract token from Keychain or credentials file ────────────────────────
_refresh_token() {
  local token=""

  # Try macOS Keychain first
  local cred_json
  cred_json=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true)
  if [ -n "$cred_json" ]; then
    token=$(echo "$cred_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])" 2>/dev/null || true)
  fi

  # Fall back to ~/.claude/.credentials.json
  if [ -z "$token" ]; then
    local creds_file="$HOME/.claude/.credentials.json"
    if [ -f "$creds_file" ]; then
      token=$(python3 -c "import json; print(json.load(open('$creds_file'))['claudeAiOauth']['accessToken'])" 2>/dev/null || true)
      [ -n "$token" ] && echo "OAuth token read from $creds_file"
    fi
  fi

  if [ -z "$token" ]; then
    echo "ERROR: Could not read Claude credentials from Keychain or ~/.claude/.credentials.json."
    echo "Run 'claude auth login' first to authenticate."
    return 1
  fi

  mkdir -p "$(dirname "$TOKEN_FILE")"
  echo "$token" > "$TOKEN_FILE"
  echo "OAuth token refreshed → $TOKEN_FILE ($(wc -c < "$TOKEN_FILE" | tr -d ' ') chars)"

  # Also write to ~/.claude/oauth-token so k8s pods (hostPath mount) can read it
  local k8s_token_file="$HOME/.claude/oauth-token"
  echo "$token" > "$k8s_token_file"
  echo "OAuth token also written → $k8s_token_file"
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

# ── Start host build runner (macOS builds for managed projects) ──────────
BUILD_RUNNER="$SCRIPT_DIR/build-runner.py"
if [ -f "$BUILD_RUNNER" ]; then
  # Kill any existing build runner
  pkill -f "build-runner.py" 2>/dev/null || true
  python3 "$BUILD_RUNNER" &
  BUILD_RUNNER_PID=$!
  echo "Build runner started (PID $BUILD_RUNNER_PID, port 4050)"
  # Clean up on exit
  trap "kill $BUILD_RUNNER_PID 2>/dev/null" EXIT
fi

exec docker compose up --build "$@"
