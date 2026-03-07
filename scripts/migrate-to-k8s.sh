#!/usr/bin/env bash
# Migrate claude-manager backend from docker-compose to the scoady k8s cluster.
#
# Prerequisites:
#   1. Cluster recreated with updated cluster.yaml.tmpl (docker.sock + extra mounts)
#   2. Backend image built and pushed to registry
#   3. OAuth token refresh launchd plist installed
#   4. GH_TOKEN k8s secret created
#
# This script:
#   - Installs the launchd plist for OAuth token refresh
#   - Runs an initial token refresh
#   - Creates the k8s secret for GH_TOKEN
#   - Stops docker-compose backend
#   - Deploys backend via helm upgrade
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== claude-manager: Migrate Backend to k8s ==="
echo ""

# ── Step 1: Install OAuth refresh launchd plist ───────────────────────────────
echo "[1/5] Installing OAuth token refresh launchd plist..."
PLIST_SRC="$PROJECT_DIR/scripts/com.claude-manager.oauth-refresh.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.claude-manager.oauth-refresh.plist"

if [ -f "$PLIST_DST" ]; then
  launchctl unload "$PLIST_DST" 2>/dev/null || true
fi
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"
echo "  Launchd plist installed and loaded."

# ── Step 2: Run initial token refresh ─────────────────────────────────────────
echo "[2/5] Running initial OAuth token refresh..."
bash "$PROJECT_DIR/scripts/refresh-oauth-token.sh"
echo "  Token refreshed at ~/.claude/oauth-token"

# ── Step 3: Create k8s secret for GH_TOKEN ────────────────────────────────────
echo "[3/5] Creating k8s secret for GH_TOKEN..."
GH_TOKEN="${GH_TOKEN:-}"
if [ -z "$GH_TOKEN" ] && [ -f "$PROJECT_DIR/.claude-snapshot/gh-token" ]; then
  GH_TOKEN=$(cat "$PROJECT_DIR/.claude-snapshot/gh-token")
fi
if [ -n "$GH_TOKEN" ]; then
  kubectl create namespace claude-manager 2>/dev/null || true
  kubectl create secret generic claude-backend-secrets \
    --namespace claude-manager \
    --from-literal=gh-token="$GH_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "  Secret claude-backend-secrets created/updated."
else
  echo "  WARNING: No GH_TOKEN found. Skipping secret creation."
fi

# ── Step 4: Stop docker-compose backend ───────────────────────────────────────
echo "[4/5] Stopping docker-compose backend..."
cd "$PROJECT_DIR"
docker compose down 2>/dev/null || true
echo "  Docker-compose services stopped."

# ── Step 5: Deploy backend via helm ───────────────────────────────────────────
echo "[5/5] Deploying backend to k8s..."
helm upgrade --install claude-manager \
  "$PROJECT_DIR/infrastructure/helm/claude-manager" \
  --namespace claude-manager \
  --create-namespace \
  --values "$PROJECT_DIR/infrastructure/helm/claude-manager/values.yaml" \
  --values "$PROJECT_DIR/infrastructure/helm/values-scoady.yaml" \
  --wait \
  --timeout 5m

echo ""
echo "=== Migration Complete ==="
echo ""
echo "Verify:"
echo "  kubectl get pods -n claude-manager"
echo "  curl -s http://claude-manager.localhost/api/health | python3 -m json.tool"
echo ""
echo "To rollback:"
echo "  helm uninstall claude-manager -n claude-manager"
echo "  cd $PROJECT_DIR && bash scripts/start.sh -d"
