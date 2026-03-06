#!/usr/bin/env bash
# Test script for POC 2: Widget Data Buffer
# Usage: bash scripts/test-buffer-poc.sh [BASE_URL]
#
# Defaults to http://localhost:4040 (main backend).
# Pass http://localhost:4052 to test the POC2 docker-compose stack.

set -euo pipefail

BASE="${1:-http://localhost:4040}"
PROJECT="test-project"
WIDGET_ID="buffer-demo"

echo "=== POC 2: Widget Data Buffer Test ==="
echo "Backend: $BASE"
echo ""

# 1. Create the demo widget via canvas PUT
echo "1. Creating demo widget via canvas API..."
curl -s -X PUT "$BASE/api/canvas/$PROJECT/widgets/$WIDGET_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Buffer Demo",
    "html": "<div id=\"bd-root\"><div class=\"bd-body\">Waiting...</div></div>",
    "css": "",
    "js": "",
    "col_span": 2,
    "row_span": 2
  }' | python3 -m json.tool
echo ""

# 2. POST data to the buffer (items list)
echo "2. Writing items data to buffer..."
curl -s -X POST "$BASE/api/canvas/$PROJECT/buffer/$WIDGET_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "heading": "Agent Tasks",
      "items": [
        {"label": "Read PROJECT.md", "value": "done", "color": "#4ade80"},
        {"label": "Analyze codebase", "value": "active", "color": "#fbbf24"},
        {"label": "Write implementation", "value": "pending", "color": "#475569"}
      ]
    }
  }' | python3 -m json.tool
echo ""

# 3. Read back from buffer
echo "3. Reading buffer (single widget)..."
curl -s "$BASE/api/canvas/$PROJECT/buffer/$WIDGET_ID" | python3 -m json.tool
echo ""

# 4. POST metric data
echo "4. Writing metric data to buffer..."
curl -s -X POST "$BASE/api/canvas/$PROJECT/buffer/$WIDGET_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "metric": 42,
      "label": "tasks completed",
      "detail": "12 in progress, 8 pending"
    }
  }' | python3 -m json.tool
echo ""

# 5. POST log data
echo "5. Writing log data to buffer..."
curl -s -X POST "$BASE/api/canvas/$PROJECT/buffer/$WIDGET_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "log": [
        {"msg": "Agent spawned", "time": "12:01"},
        {"msg": "Reading PROJECT.md", "time": "12:02"},
        {"msg": "Planning phase complete", "time": "12:03"}
      ]
    }
  }' | python3 -m json.tool
echo ""

# 6. Read all buffers for the project
echo "6. Reading all project buffers..."
curl -s "$BASE/api/canvas/$PROJECT/buffer" | python3 -m json.tool
echo ""

# 7. Verify version incremented
echo "7. Checking version (should be 3)..."
VERSION=$(curl -s "$BASE/api/canvas/$PROJECT/buffer/$WIDGET_ID" | python3 -c "import sys,json; print(json.load(sys.stdin)['version'])")
echo "   Version: $VERSION"
if [ "$VERSION" = "3" ]; then
  echo "   PASS: Version correctly incremented"
else
  echo "   FAIL: Expected version 3, got $VERSION"
fi
echo ""

echo "=== All tests complete ==="
echo ""
echo "To test WebSocket delivery, open the browser at $BASE"
echo "and watch the console for 'widget_data' events while running:"
echo "  curl -X POST $BASE/api/canvas/$PROJECT/buffer/$WIDGET_ID \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"data\": {\"metric\": 99, \"label\": \"live update\"}}'"
