#!/usr/bin/env bash
# Bootstrap the claude-manager-dev kind cluster.
#
# This creates the cluster, installs NGINX ingress, sets up an in-cluster
# container registry, creates the claude-manager namespace, and configures
# /etc/hosts entries for *.localhost routing.
#
# Usage:
#   bash infrastructure/kind/setup-dev-cluster.sh
#
# IMPORTANT: This script NEVER touches the scoady production cluster.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLUSTER_NAME="claude-manager-dev"
CLUSTER_YAML="$SCRIPT_DIR/cluster.yaml"

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; }
header(){ echo -e "\n${CYAN}=== $* ===${NC}"; }

# ── Prerequisites ─────────────────────────────────────────────────────────────
header "Checking prerequisites"
for cmd in kind kubectl helm docker; do
  if command -v "$cmd" &>/dev/null; then
    info "$cmd found"
  else
    error "$cmd not found — install it first"
    exit 1
  fi
done

# Safety: refuse to run if scoady cluster is somehow targeted
if [[ "${KUBECONFIG:-}" == *"scoady"* ]]; then
  error "KUBECONFIG points to scoady — refusing to proceed. Unset KUBECONFIG first."
  exit 1
fi

# ── Step 1: Create kind cluster ──────────────────────────────────────────────
header "Step 1: Create kind cluster '${CLUSTER_NAME}'"
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
  warn "Cluster '${CLUSTER_NAME}' already exists — skipping creation"
else
  info "Creating kind cluster '${CLUSTER_NAME}'..."
  kind create cluster --config "$CLUSTER_YAML"
  info "Cluster created"
fi

# Switch kubectl context to the dev cluster
kubectl cluster-info --context "kind-${CLUSTER_NAME}"
info "kubectl context set to kind-${CLUSTER_NAME}"

# ── Step 2: Install NGINX Ingress Controller ─────────────────────────────────
header "Step 2: Install NGINX Ingress Controller"

# Label control-plane node for ingress scheduling
kubectl label node "${CLUSTER_NAME}-control-plane" ingress-ready=true --overwrite \
  --context "kind-${CLUSTER_NAME}"

NGINX_VERSION="v1.12.1"
info "Applying NGINX Ingress Controller ${NGINX_VERSION}..."
kubectl apply --context "kind-${CLUSTER_NAME}" \
  -f "https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-${NGINX_VERSION}/deploy/static/provider/kind/deploy.yaml"

info "Waiting for ingress controller to be ready..."
kubectl wait --namespace ingress-nginx \
  --context "kind-${CLUSTER_NAME}" \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=300s

# Patch admission webhook to fail-open (avoids timeouts during deploys)
kubectl patch validatingwebhookconfiguration ingress-nginx-admission \
  --context "kind-${CLUSTER_NAME}" \
  --type=json -p='[{"op":"replace","path":"/webhooks/0/failurePolicy","value":"Ignore"}]' 2>/dev/null || true

info "NGINX Ingress Controller ready"

# ── Step 3: In-cluster container registry ────────────────────────────────────
header "Step 3: Set up in-cluster container registry"

kubectl create namespace registry --context "kind-${CLUSTER_NAME}" --dry-run=client -o yaml \
  | kubectl apply --context "kind-${CLUSTER_NAME}" -f -

# Deploy a simple registry (same pattern as scoady's helm-platform registry)
cat <<'REGEOF' | kubectl apply --context "kind-${CLUSTER_NAME}" -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: registry
  namespace: registry
spec:
  replicas: 1
  selector:
    matchLabels:
      app: registry
  template:
    metadata:
      labels:
        app: registry
    spec:
      containers:
        - name: registry
          image: registry:2
          ports:
            - containerPort: 5000
          volumeMounts:
            - name: data
              mountPath: /var/lib/registry
          env:
            - name: REGISTRY_STORAGE_DELETE_ENABLED
              value: "true"
      volumes:
        - name: data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: registry
  namespace: registry
spec:
  selector:
    app: registry
  ports:
    - port: 5000
      targetPort: 5000
  type: ClusterIP
REGEOF

info "Waiting for registry pod..."
kubectl wait --namespace registry \
  --context "kind-${CLUSTER_NAME}" \
  --for=condition=ready pod \
  --selector=app=registry \
  --timeout=120s

# Configure registry DNS on nodes
REGISTRY_IP=$(kubectl get svc registry -n registry \
  --context "kind-${CLUSTER_NAME}" \
  -o jsonpath='{.spec.clusterIP}')
info "Registry ClusterIP: ${REGISTRY_IP}"

REGISTRY_ADDR="registry.registry.svc.cluster.local:5000"
for NODE in $(kind get nodes --name "$CLUSTER_NAME"); do
  # Add /etc/hosts entry
  docker exec "$NODE" sh -c \
    "grep -v registry.registry.svc.cluster.local /etc/hosts > /tmp/h && cp /tmp/h /etc/hosts && echo '${REGISTRY_IP} registry.registry.svc.cluster.local' >> /etc/hosts"
  info "$NODE: registry DNS configured"

  # Configure containerd to use the registry without TLS
  docker exec "$NODE" mkdir -p "/etc/containerd/certs.d/${REGISTRY_ADDR}"
  docker exec "$NODE" sh -c "cat > /etc/containerd/certs.d/${REGISTRY_ADDR}/hosts.toml <<'TOML'
[host.\"http://${REGISTRY_ADDR}\"]
  capabilities = [\"pull\", \"resolve\", \"push\"]
  skip_verify = true
TOML"
  docker exec "$NODE" pkill -HUP containerd || true
  info "$NODE: containerd registry config created"
done

# ── Step 4: Create namespaces ────────────────────────────────────────────────
header "Step 4: Create namespaces"

for NS in claude-manager; do
  kubectl create namespace "$NS" --context "kind-${CLUSTER_NAME}" --dry-run=client -o yaml \
    | kubectl apply --context "kind-${CLUSTER_NAME}" -f -
  info "Namespace '$NS' ready"
done

# ── Step 5: /etc/hosts entries ───────────────────────────────────────────────
header "Step 5: /etc/hosts entries"

HOSTS_ENTRIES=(
  "127.0.0.1 claude-manager-dev.localhost"
)

for entry in "${HOSTS_ENTRIES[@]}"; do
  if grep -qF "$entry" /etc/hosts 2>/dev/null; then
    info "Already in /etc/hosts: $entry"
  else
    echo "$entry" | sudo tee -a /etc/hosts >/dev/null
    info "Added to /etc/hosts: $entry"
  fi
done

# ── Step 6: Claude CLI on worker node ────────────────────────────────────────
header "Step 6: Claude CLI on worker node"

CLAUDE_HOST_BIN=$(readlink -f "$HOME/.local/bin/claude" 2>/dev/null || echo "")
if [[ -n "$CLAUDE_HOST_BIN" && -f "$CLAUDE_HOST_BIN" ]]; then
  CLAUDE_VERSION=$(basename "$CLAUDE_HOST_BIN")
  VERSIONS_DIR="$HOME/.local/share/claude/versions"
  TARGET="$VERSIONS_DIR/$CLAUDE_VERSION"

  for NODE in $(kind get nodes --name "$CLUSTER_NAME"); do
    [[ "$NODE" == *control-plane* ]] && continue
    docker exec "$NODE" mkdir -p /usr/local/bin 2>/dev/null || true
    docker exec "$NODE" ln -sf "$TARGET" /usr/local/bin/claude 2>/dev/null || true
    docker exec "$NODE" mkdir -p "$(dirname "$HOME/.local/bin/claude")" 2>/dev/null || true
    docker exec "$NODE" ln -sf "$TARGET" "$HOME/.local/bin/claude" 2>/dev/null || true
    if docker exec "$NODE" test -x /usr/local/bin/claude 2>/dev/null; then
      info "$NODE: claude CLI linked -> $CLAUDE_VERSION"
    else
      warn "$NODE: claude CLI not executable (will work once versions dir is populated)"
    fi
  done
else
  warn "Claude CLI not found on host — agents will use the npm-installed CLI in the backend image"
fi

# ── Step 7: RBAC for backend ────────────────────────────────────────────────
header "Step 7: Backend RBAC"

cat <<'RBACEOF' | kubectl apply --context "kind-${CLUSTER_NAME}" -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: claude-manager-backend
  namespace: claude-manager
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: claude-manager-backend-admin
subjects:
  - kind: ServiceAccount
    name: claude-manager-backend
    namespace: claude-manager
roleRef:
  kind: ClusterRole
  name: cluster-admin
  apiGroup: rbac.authorization.k8s.io
RBACEOF

info "ServiceAccount + ClusterRoleBinding created"

# ── Summary ──────────────────────────────────────────────────────────────────
header "Dev cluster ready!"
echo ""
info "Cluster:   ${CLUSTER_NAME}"
info "Context:   kind-${CLUSTER_NAME}"
info "Registry:  registry.registry.svc.cluster.local:5000"
info "Ingress:   http://claude-manager-dev.localhost:8080"
info "Namespace: claude-manager"
echo ""
info "Next steps:"
echo "  1. Build and push backend image:"
echo "     docker build -f backend/Dockerfile -t registry.registry.svc.cluster.local:5000/claude-manager/backend:latest ."
echo "     docker push registry.registry.svc.cluster.local:5000/claude-manager/backend:latest"
echo ""
echo "  2. Build and push frontend image:"
echo "     docker build -f ci/Dockerfile.frontend -t registry.registry.svc.cluster.local:5000/claude-manager/frontend:latest ."
echo "     docker push registry.registry.svc.cluster.local:5000/claude-manager/frontend:latest"
echo ""
echo "  3. Deploy with Helm:"
echo "     helm upgrade --install claude-manager ./infrastructure/helm/claude-manager \\"
echo "       --namespace claude-manager \\"
echo "       --context kind-${CLUSTER_NAME} \\"
echo "       --values ./infrastructure/helm/values-dev.yaml"
echo ""
info "To switch between clusters:"
echo "  kubectl config use-context kind-scoady            # production"
echo "  kubectl config use-context kind-${CLUSTER_NAME}   # dev"
