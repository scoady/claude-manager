#!/bin/bash
# UX Monitor — periodic screenshot + UX analysis agent dispatch
#
# Usage: bash scripts/ux-monitor.sh &
# Or: nohup bash scripts/ux-monitor.sh > /tmp/ux-monitor.log 2>&1 &
#
# Takes a screenshot of the dashboard every 10 minutes, stores it in
# ~/.claude/canvas/screenshots/, and dispatches a claude-manager agent
# via the API to analyze the UX and suggest (or apply) improvements.

SCREENSHOT_DIR="$HOME/.claude/canvas/screenshots"
API="http://localhost:4040"
INTERVAL=600  # 10 minutes

mkdir -p "$SCREENSHOT_DIR"

while true; do
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    SCREENSHOT="$SCREENSHOT_DIR/dashboard-$TIMESTAMP.png"

    # Capture screen (silent, main display)
    screencapture -x "$SCREENSHOT"

    if [ -f "$SCREENSHOT" ]; then
        echo "[$(date)] Screenshot saved: $SCREENSHOT"

        # Dispatch UX analysis agent to claude-manager project
        curl -s -X POST "$API/api/projects/claude-manager/dispatch" \
            -H 'Content-Type: application/json' \
            -d "{\"task\":\"A screenshot of the dashboard was taken at $TIMESTAMP and saved to $SCREENSHOT. Please read this screenshot image file using the Read tool (it supports images), then analyze the UX. Look for: 1) Visual issues — overlapping elements, broken layouts, unreadable text, poor contrast. 2) Interaction issues — unclear affordances, missing hover states, confusing navigation. 3) Design consistency — mismatched colors, inconsistent spacing, font variations. 4) Suggestions — specific actionable improvements. Write your findings concisely, then if you see any easy wins (CSS fixes, layout adjustments), go ahead and fix them directly in the frontend code or by updating canvas widgets via canvas_put MCP tool. Be concise and specific.\"}"

        echo "[$(date)] UX analysis agent dispatched"
    fi

    # Clean up old screenshots (keep last 20)
    ls -t "$SCREENSHOT_DIR"/dashboard-*.png 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null

    sleep $INTERVAL
done
