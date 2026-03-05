#!/bin/bash
# Copy the read-only config snapshot to a writable location.
# The Claude CLI needs ~/.claude.json to be writable (it writes config updates).
if [ -f /tmp/claude-config.json ]; then
  cp /tmp/claude-config.json /root/.claude.json
  echo "[entrypoint] Copied claude config to /root/.claude.json"
fi

# Ensure MCP tools are pre-approved in global settings so agents don't get
# permission denials. Also disable fast mode (not available in npm CLI).
SETTINGS_FILE="/root/.claude/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
  python3 -c "
import json, sys
with open('$SETTINGS_FILE') as f:
    s = json.load(f)
# Pre-approve MCP tool patterns
allows = s.setdefault('permissions', {}).setdefault('allow', [])
auto_allow = [
    'mcp__canvas__*',
    'mcp__orchestrator__*',
    'Bash',
    'Read',
    'Write',
    'Edit',
    'Glob',
    'Grep',
    'Agent',
    'WebFetch',
    'WebSearch',
    'NotebookEdit',
]
for p in auto_allow:
    if p not in allows:
        allows.append(p)
# Deny AskUserQuestion globally — agents must be fully autonomous
denies = s.setdefault('permissions', {}).setdefault('deny', [])
if 'AskUserQuestion' not in denies:
    denies.append('AskUserQuestion')
# Disable fast mode (npm CLI doesn't support it)
s['fastMode'] = False
with open('$SETTINGS_FILE', 'w') as f:
    json.dump(s, f, indent=2)
print('[entrypoint] Updated settings: MCP permissions + fastMode=false')
"
fi

exec "$@"
