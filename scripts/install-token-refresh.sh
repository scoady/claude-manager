#!/usr/bin/env bash
# Install a macOS launchd agent to refresh the Claude OAuth token every 4 hours.
set -euo pipefail

LABEL="com.claude-manager.token-refresh"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
SCRIPT="$HOME/git/claude-manager/scripts/refresh-token.sh"
LOG="$HOME/git/claude-manager/.claude-snapshot/token-refresh.log"

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${SCRIPT}</string>
  </array>
  <key>StartInterval</key>
  <integer>14400</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG}</string>
  <key>StandardErrorPath</key>
  <string>${LOG}</string>
</dict>
</plist>
EOF

# Load (or reload) the agent
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Installed launchd agent: ${LABEL}"
echo "  Runs every 4 hours + on login"
echo "  Log: ${LOG}"
echo "  Plist: ${PLIST}"
