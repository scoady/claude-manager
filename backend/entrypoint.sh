#!/bin/bash
# Copy the read-only config snapshot to a writable location.
# The Claude CLI needs ~/.claude.json to be writable (it writes config updates).
if [ -f /tmp/claude-config.json ]; then
  cp /tmp/claude-config.json /root/.claude.json
  echo "[entrypoint] Copied claude config to /root/.claude.json"
fi

exec "$@"
