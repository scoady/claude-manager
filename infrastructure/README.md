# Infrastructure

This directory contains everything needed to run the full claude-manager stack in Kubernetes.

## Clusters

| Cluster | Purpose | Ports | Context |
|---------|---------|-------|---------|
| `scoady` | Production. Managed by `~/git/kind-infra/`. DO NOT modify from here. | 80, 443 | `kind-scoady` |
| `claude-manager-dev` | Dev/experimentation. Full stack (backend + frontend + MCP) in k8s. | 8080, 8443 | `kind-claude-manager-dev` |

## Quick Start: Dev Cluster

### 1. Create the cluster

```bash
bash infrastructure/kind/setup-dev-cluster.sh
```

This creates the kind cluster, installs NGINX ingress, deploys an in-cluster registry,
creates the `claude-manager` namespace, adds `/etc/hosts` entries, and sets up RBAC.

### 2. Build and push images

```bash
# Backend
docker build -f backend/Dockerfile -t registry.registry.svc.cluster.local:5000/claude-manager/backend:latest .
docker push registry.registry.svc.cluster.local:5000/claude-manager/backend:latest

# Frontend
docker build -f ci/Dockerfile.frontend -t registry.registry.svc.cluster.local:5000/claude-manager/frontend:latest .
docker push registry.registry.svc.cluster.local:5000/claude-manager/frontend:latest
```

> Note: `docker push` to the in-cluster registry requires that the registry DNS is
> configured on the host. The setup script handles the kind nodes, but for the host
> you may need to port-forward: `kubectl port-forward -n registry svc/registry 5000:5000 --context kind-claude-manager-dev`

### 3. Deploy with Helm

```bash
helm upgrade --install claude-manager ./infrastructure/helm/claude-manager \
  --namespace claude-manager \
  --context kind-claude-manager-dev \
  --values ./infrastructure/helm/values-dev.yaml
```

### 4. Access the UI

Open http://claude-manager-dev.localhost:8080

## Switching kubectl Context

```bash
# Production (scoady) — for normal operations
kubectl config use-context kind-scoady

# Dev cluster — for experimentation
kubectl config use-context kind-claude-manager-dev
```

Always verify your context before running commands:

```bash
kubectl config current-context
```

## Directory Layout

```
infrastructure/
  kind/
    cluster.yaml              Kind cluster config (claude-manager-dev)
    setup-dev-cluster.sh      Bootstrap script (create cluster, ingress, registry, RBAC)
  helm/
    claude-manager/
      Chart.yaml              Helm chart metadata
      values.yaml             Default values (backend disabled, frontend only)
      templates/
        _helpers.tpl           Common template helpers
        frontend/
          deployment.yaml      Frontend nginx deployment
          service.yaml         Frontend ClusterIP service
          ingress.yaml         Ingress with path-based routing (/api -> backend, / -> frontend)
        backend/
          deployment.yaml      Backend + MCP sidecars deployment
          service.yaml         Backend ClusterIP service (ports 4040, 4041, 4042)
          serviceaccount.yaml  ServiceAccount + ClusterRoleBinding (cluster-admin)
          configmap.yaml       MCP config JSON for in-pod localhost URLs
    values-dev.yaml            Dev cluster overrides (backend enabled, hostPaths, registry)
```

## Architecture (Dev Cluster)

```
                  claude-manager-dev kind cluster
  +--------------------------------------------------------------+
  |                                                              |
  |  ingress-nginx (:8080)                                       |
  |    /api/* /ws  --> backend Service :4040                     |
  |    /*          --> frontend Service :80                      |
  |                                                              |
  |  claude-manager namespace                                    |
  |  +--------------------------------------------------------+ |
  |  | backend pod (worker node)                               | |
  |  |   [backend]        :4040 FastAPI + agent subprocess mgr | |
  |  |   [mcp-canvas]     :4041 MCP canvas tools               | |
  |  |   [mcp-orchestrator]:4042 MCP orchestrator tools         | |
  |  |                                                         | |
  |  |   hostPath mounts:                                      | |
  |  |     ~/.claude, ~/git/claude-managed-projects,           | |
  |  |     ~/git/claude-manager, ~/git/kind-infra,             | |
  |  |     ~/git/helm-platform, ~/.ssh, ~/.gitconfig,          | |
  |  |     /var/run/docker.sock                                | |
  |  +--------------------------------------------------------+ |
  |  +---------------------------+                               |
  |  | frontend pod              |                               |
  |  |   [nginx] :80 static SPA  |                               |
  |  +---------------------------+                               |
  |                                                              |
  |  registry namespace                                          |
  |  +---------------------------+                               |
  |  | registry pod :5000        |                               |
  |  +---------------------------+                               |
  +--------------------------------------------------------------+
```

## Port Assignments

| Service | Cluster Port | Host Port | Notes |
|---------|-------------|-----------|-------|
| Ingress HTTP | 80 | 8080 | Avoids conflict with scoady on :80 |
| Ingress HTTPS | 443 | 8443 | Avoids conflict with scoady on :443 |
| Backend | 4040 | (internal) | Exposed via ingress /api/ and /ws |
| MCP Canvas | 4041 | (internal) | Sidecar, localhost only |
| MCP Orchestrator | 4042 | (internal) | Sidecar, localhost only |
| Frontend | 80 | (internal) | Exposed via ingress / |
| Registry | 5000 | (internal) | In-cluster only |

## OAuth Token

The backend reads the OAuth token from `~/.claude/oauth-token`, which is hostPath-mounted.
The macOS host must refresh this file periodically. The existing launchd plist
(`com.claude-manager.token-refresh`) handles this, or run manually:

```bash
bash scripts/start.sh  # extracts token to .claude-snapshot/oauth-token
# Then copy to the expected path:
cp .claude-snapshot/oauth-token ~/.claude/oauth-token
```

## Destroying the Dev Cluster

```bash
kind delete cluster --name claude-manager-dev
```

This does not affect the scoady production cluster.
