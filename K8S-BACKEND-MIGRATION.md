# K8s Backend Migration Plan

## Executive Summary

The claude-manager backend currently runs as a docker-compose service on the macOS host. It is a FastAPI app that spawns Claude CLI agent subprocesses, streams their output over WebSocket, and manages project files on disk. Moving it into the kind k8s cluster would unify the deployment model with the frontend (already in k8s) and enable the standard Jenkins CI/CD pipeline.

This document maps every host dependency, evaluates three architecture options, recommends one, and provides a step-by-step migration plan.

---

## Current State

```
                        macOS Host
  +------------------------------------------------------+
  |                                                       |
  |  docker compose up (port 4040)                        |
  |  +--------------------------------------------------+ |
  |  | backend container (python:3.11-slim)             | |
  |  |   - FastAPI + uvicorn                            | |
  |  |   - Claude CLI (npm global)                      | |
  |  |   - Spawns `claude --print` subprocesses         | |
  |  |   - MCP sidecar containers (canvas, orchestrator)| |
  |  +--------------------------------------------------+ |
  |       |          |           |          |              |
  |       v          v           v          v              |
  |  ~/.claude  ~/git/claude-  docker.sock  macOS         |
  |  (sessions) managed-projects            Keychain      |
  |                                         (OAuth)       |
  +------------------------------------------------------+
           ^
           | proxy /api/ + /ws → host.docker.internal:4040
  +-------------------+
  | kind k8s cluster  |
  | frontend (nginx)  |
  | claude-manager.   |
  |   localhost        |
  +-------------------+
```

### Services in docker-compose.yml

| Service            | Port | Purpose                              |
|--------------------|------|--------------------------------------|
| `backend`          | 4040 | FastAPI main server                  |
| `mcp-canvas`       | 4041 | MCP server for canvas widget tools   |
| `mcp-orchestrator` | 4042 | MCP server for orchestrator tools    |

---

## Dependency Matrix

| Dependency | Current Mechanism | k8s Equivalent | Risk | Notes |
|---|---|---|---|---|
| **~/.claude** (CLI sessions, settings, canvas, cron, roles) | Bind mount `${HOME}/.claude:/root/.claude` | hostPath volume (already mounted on kind workers) | Low | Already in `cluster.yaml.tmpl` |
| **~/.claude.json** (CLI config) | Snapshot copy via entrypoint.sh | hostPath + init container copy | Low | Needs writable copy; same entrypoint logic works |
| **OAuth token** | Extracted from macOS Keychain by `scripts/start.sh`, written to `.claude-snapshot/oauth-token`, bind-mounted read-only | k8s Secret, refreshed by host CronJob or launchd plist | Medium | Most complex dependency -- see section below |
| **Managed projects dir** (`~/git/claude-managed-projects`) | Bind mount, same abs path inside/outside | hostPath (already mounted on kind workers) | Low | Already in `cluster.yaml.tmpl` |
| **claude-manager source** (`~/git/claude-manager`) | Bind mount (for symlink resolution) | hostPath | Low | Add to `cluster.yaml.tmpl` |
| **Infrastructure repos** (`kind-infra`, `helm-platform`) | Bind mounts | hostPath | Low | Add to `cluster.yaml.tmpl` |
| **SSH keys** (`~/.ssh`) | Bind mount read-only | hostPath (already mounted on kind workers) | Low | Already in `cluster.yaml.tmpl` |
| **Git config** (`~/.gitconfig`) | Bind mount read-only | hostPath (already mounted on kind workers) | Low | Already in `cluster.yaml.tmpl` |
| **Docker socket** (`/var/run/docker.sock`) | Bind mount | hostPath `/var/run/docker.sock` | Medium | Kind nodes have Docker -- but it is the host Docker. See analysis. |
| **Kubeconfig** | Rewritten snapshot using `host.docker.internal` | In-cluster ServiceAccount + RBAC, or hostPath kubeconfig rewritten for internal API server | Medium | In k8s pod, use in-cluster config or mount a kubeconfig pointing to the kind API server's internal address |
| **Host CLI binaries** (docker, kubectl, helm, kind) | Linux ARM64 static binaries in `.host-bins/` | Bake into Docker image, or hostPath from kind worker node | Low | Already Linux ARM64; bake into image |
| **Claude CLI** (`claude` npm package) | `npm install -g @anthropic-ai/claude-code` in Dockerfile | Same -- already in image | None | No change needed |
| **GitHub PAT** (`GH_TOKEN`) | Env var from `start.sh` or `.claude-snapshot/gh-token` | k8s Secret | Low | Standard pattern |
| **Node.js** (for Claude CLI) | Installed in Dockerfile | Same | None | No change needed |
| **PostgreSQL** (optional) | `DATABASE_URL` env var | k8s Secret or ConfigMap; could use in-cluster PG | Low | Optional; env var injection |
| **Frontend static files** | Dev mount `./frontend:/app/frontend:ro` | Not needed in prod (frontend is separate pod) | None | Remove |

---

## Challenge Analysis

### 1. OAuth Token Refresh

**The Problem:** The Claude CLI authenticates via an OAuth token stored in the macOS Keychain. `scripts/start.sh` extracts it at startup via `security find-generic-password`. Inside a k8s pod, there is no macOS Keychain.

**Current Flow:**
1. `start.sh` calls `security find-generic-password -s "Claude Code-credentials" -w`
2. Parses JSON, extracts `claudeAiOauth.accessToken`
3. Writes to `.claude-snapshot/oauth-token`
4. Container reads this file on each agent spawn (`_get_spawn_env()` in `agent_session.py`)

**Proposed Solution:**
- A **launchd plist** on the macOS host runs a token-refresh script every 30 minutes
- The script writes the token to `~/.claude/oauth-token` (inside the already-mounted `~/.claude` hostPath)
- The backend pod reads from the same path (`/root/.claude/oauth-token` or wherever `~/.claude` is mounted)
- Change `OAUTH_TOKEN_FILE` constant in `agent_session.py` from `/run/claude-oauth-token` to `${CLAUDE_DATA_DIR}/oauth-token`
- **Fallback:** Also populate a k8s Secret with the token; the refresh script can `kubectl create secret` on each refresh

**Risk:** Token expiry between refreshes. Mitigation: 30-minute refresh cadence; token validity is typically hours.

### 2. Agent Subprocesses (Claude CLI)

**The Problem:** The backend spawns `claude --print --output-format stream-json` subprocesses. Each agent is a separate process consuming CPU and memory. Multiple agents can run concurrently.

**Resource Analysis:**
- Each `claude` subprocess is a Node.js process (~100-200MB RSS)
- With 4-6 concurrent agents: ~1-1.5GB memory
- CPU is bursty (mostly waiting on API responses, spiking during tool execution)

**Proposed Solution:**
- Set pod resource requests/limits generously: `requests: 1Gi/500m`, `limits: 4Gi/4000m`
- Use a single replica Deployment (no HPA -- agent processes are stateful)
- Consider `resources.limits` as a soft cap; monitor and adjust

**Risk:** Pod eviction under memory pressure. Mitigation: Set requests high enough; use `Guaranteed` QoS class (requests == limits) or node affinity to a dedicated worker.

### 3. Docker Socket Access

**The Problem:** Agents that build images need Docker. Currently the host Docker socket is mounted.

**In Kind Context:**
- Kind worker nodes run as Docker containers themselves
- The Docker socket inside a kind worker (`/var/run/docker.sock`) is the **host's** Docker socket (Docker Desktop on macOS)
- Mounting it as a hostPath in a pod gives the pod access to the host Docker daemon
- This is the same level of access the current docker-compose setup has

**Proposed Solution:**
- Mount `/var/run/docker.sock` as a hostPath volume
- The Linux ARM64 `docker` binary already works in the container
- **Alternative:** For builds specifically, agents already trigger Jenkins (which uses Kaniko). Docker socket access is primarily for `docker compose` operations on the host, which is the backend itself. Once the backend is in k8s, agents should use `kubectl`/`helm` for deploys, not `docker compose`.

**Risk:** Security (Docker socket is root-equivalent). Mitigation: Same risk as current setup; no regression.

### 4. Managed Projects Filesystem

**The Problem:** Agents read/write to `~/git/claude-managed-projects/`. Git worktrees are used for isolation.

**Kind Already Handles This:**
- `cluster.yaml.tmpl` already mounts `${HOME}/git/claude-managed-projects` on both worker nodes
- The mount is read-write
- Git worktrees create directories within the same repo, so they work within the same mount

**Proposed Solution:**
- Use hostPath volume pointing to the already-mounted path
- Ensure the pod's `securityContext.runAsUser` matches the file ownership, OR run as root (current behavior in docker-compose)

**Additional Mounts Needed:**
- `~/git/claude-manager` -- for symlink resolution from managed projects
- `~/git/kind-infra` and `~/git/helm-platform` -- same reason
- These need to be added to `cluster.yaml.tmpl`

### 5. WebSocket Connections

**The Problem:** The frontend connects to the backend via WebSocket at `/ws`. In k8s, nginx ingress needs to support WebSocket upgrade.

**Current Ingress Already Handles This:**
- The existing ingress has `proxy-read-timeout: "120"` and `proxy-send-timeout: "120"`
- For WebSocket, we need additional annotations

**Proposed Solution:**
- Add nginx ingress annotations for WebSocket support:
  ```yaml
  nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
  nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
  nginx.ingress.kubernetes.io/proxy-http-version: "1.1"
  nginx.ingress.kubernetes.io/upstream-hash-by: "$remote_addr"
  ```
- Update the frontend nginx.conf to proxy to the backend k8s Service (`http://backend:4040`) instead of `host.docker.internal:4040`

### 6. Kubeconfig for Agents

**The Problem:** Agents that run `kubectl` or `helm` need a kubeconfig. Currently a snapshot kubeconfig with `host.docker.internal` as the API server address is mounted.

**In k8s Pod Context:**
- The pod can use the in-cluster service account for its own API access
- But agents need to run arbitrary `kubectl` commands (deploying other apps)
- The kind API server is accessible from within the cluster at `https://kubernetes.default.svc` for the same cluster

**Proposed Solution:**
- Mount a kubeconfig that points to `https://kubernetes.default.svc:443`
- Or use the pod's service account with sufficient RBAC
- Create a `ClusterRole` with broad permissions and bind it to the backend's ServiceAccount
- Set `KUBECONFIG` env var or write to `~/.kube/config` in the pod

### 7. MCP Sidecar Services

**The Problem:** `mcp-canvas` (port 4041) and `mcp-orchestrator` (port 4042) are separate docker-compose services that talk to the backend via `http://backend:4040`.

**Proposed Solution:**
- Deploy as sidecar containers in the same Pod, or as separate Deployments with Services
- Sidecar approach is simpler: they share the pod network, so `localhost:4040` works
- The MCP config JSON files reference these servers by URL; update URLs accordingly

---

## Architecture Options

### Option A: hostPath Migration (Recommended)

Run the backend as a k8s Deployment, mounting all needed paths via hostPath. The kind cluster already has most paths bind-mounted into worker nodes.

**Changes Required:**
1. Add missing hostPath mounts to `cluster.yaml.tmpl` (`~/git/claude-manager`, `~/git/kind-infra`, `~/git/helm-platform`)
2. Create a backend Deployment with hostPath volumes for all dependencies
3. Create a launchd plist for OAuth token refresh to `~/.claude/oauth-token`
4. Update frontend nginx.conf to proxy to `http://backend.claude-manager.svc:4040`
5. Add backend Service and Ingress rules
6. Bake host CLI binaries into the Docker image
7. Update `agent_session.py` to read OAuth token from new path
8. Update MCP config URLs for in-pod/in-cluster communication
9. Add RBAC for kubectl/helm operations

**Pros:**
- Minimal code changes -- same filesystem layout, same process model
- Leverages existing kind bind-mounts
- Fastest path to production
- Unified deployment via Jenkins CI/CD
- Backend and frontend in same namespace; simple Service DNS

**Cons:**
- hostPath volumes tie the Deployment to specific nodes (need nodeAffinity)
- Not portable to a "real" k8s cluster (but this is a local dev cluster)
- Security: hostPath + Docker socket = broad host access

### Option B: Fully Containerized

All dependencies baked into the image or provided via k8s primitives. No hostPath mounts except for managed projects data.

**Changes Required:**
1. PersistentVolume for managed projects (backed by hostPath in kind, but abstracted)
2. Claude CLI sessions stored in a PV instead of hostPath
3. OAuth token as a k8s Secret with an ExternalSecret or CronJob refresh
4. SSH keys and git config as Secrets/ConfigMaps
5. Kubeconfig via ServiceAccount + RBAC only
6. No Docker socket -- agents use in-cluster CI (Jenkins/Kaniko) exclusively
7. All CLI binaries baked into image

**Pros:**
- More portable, closer to production k8s patterns
- Better security posture (no Docker socket, no hostPath)
- Could theoretically run on a remote cluster

**Cons:**
- Significantly more work -- every dependency needs a k8s-native alternative
- Managed projects path abstraction breaks symlinks between repos
- SSH keys as Secrets require rotation management
- Agents currently rely on the host filesystem being at known absolute paths; refactoring this is non-trivial
- The project is a local dev tool -- portability is not a real requirement

### Option C: Hybrid (Pod with Selective hostPath)

Like Option A, but minimize hostPath usage by converting some dependencies to k8s primitives.

**Changes:**
- OAuth token and GitHub PAT as k8s Secrets (not hostPath)
- SSH keys and gitconfig as Secrets/ConfigMaps
- Docker socket NOT mounted (agents use Jenkins for builds)
- Everything else via hostPath (managed projects, ~/.claude, repos)

**Pros:**
- Reduces host surface area
- Secrets management via k8s is more standard
- Removes Docker socket dependency

**Cons:**
- Agents that need `docker` commands will break
- More moving parts than Option A
- SSH key rotation requires Secret updates

---

## Recommendation: Option A (hostPath Migration)

**Justification:**

1. **The system is a local dev tool.** Portability to remote clusters is not a requirement. The kind cluster exists specifically to run local services with host filesystem access.

2. **Minimal risk.** The backend already runs in a Linux container (docker-compose). Moving it to a k8s pod with the same mounts is a lateral move, not an architectural change.

3. **Fastest path.** Most hostPath mounts already exist in `cluster.yaml.tmpl`. The primary work is Helm chart authoring and the OAuth token refresh mechanism.

4. **Unified CI/CD.** Once in k8s, the backend follows the same `gpush -> Jenkins build -> deploy` pipeline as every other app.

5. **Agents keep working.** No changes to how agents spawn or what they can access. The filesystem layout is identical.

---

## Helm Chart Design

### Directory Structure

```
infrastructure/helm/claude-manager/
  templates/
    frontend/
      deployment.yaml       (existing)
      service.yaml           (existing)
      ingress.yaml           (existing)
    backend/
      deployment.yaml        (new)
      service.yaml           (new)
      configmap.yaml         (new - entrypoint config)
      secret.yaml            (new - OAuth token, GH PAT)
      serviceaccount.yaml    (new)
      clusterrolebinding.yaml (new)
    _helpers.tpl             (existing)
  Chart.yaml                 (existing)
  values.yaml                (existing, extend)
```

### Backend Deployment Skeleton

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  namespace: {{ .Release.Namespace }}
spec:
  replicas: 1
  strategy:
    type: Recreate  # Single replica, stateful -- no rolling update
  selector:
    matchLabels:
      app: backend
  template:
    metadata:
      labels:
        app: backend
    spec:
      serviceAccountName: claude-manager-backend
      nodeSelector:
        # Pin to a worker node that has the hostPath mounts
        kubernetes.io/hostname: scoady-worker
      containers:
        # -- Main backend --
        - name: backend
          image: "{{ registry }}/claude-manager/backend:{{ tag }}"
          ports:
            - containerPort: 4040
          env:
            - name: CLAUDE_DATA_DIR
              value: /root/.claude
            - name: MANAGED_PROJECTS_DIR
              value: {{ .Values.backend.managedProjectsDir }}
            - name: CLAUDE_CODE_OAUTH_TOKEN
              valueFrom:
                secretKeyRef:
                  name: claude-backend-secrets
                  key: oauth-token
            - name: GH_TOKEN
              valueFrom:
                secretKeyRef:
                  name: claude-backend-secrets
                  key: gh-token
                  optional: true
          volumeMounts:
            - name: claude-data
              mountPath: /root/.claude
            - name: managed-projects
              mountPath: {{ .Values.backend.managedProjectsDir }}
            - name: claude-manager-src
              mountPath: {{ .Values.backend.repoDir }}/claude-manager
              readOnly: true
            - name: kind-infra-src
              mountPath: {{ .Values.backend.repoDir }}/kind-infra
              readOnly: true
            - name: helm-platform-src
              mountPath: {{ .Values.backend.repoDir }}/helm-platform
              readOnly: true
            - name: ssh-keys
              mountPath: /root/.ssh
              readOnly: true
            - name: gitconfig
              mountPath: /root/.gitconfig
              subPath: .gitconfig
              readOnly: true
            - name: docker-socket
              mountPath: /var/run/docker.sock
          resources:
            requests:
              memory: "1Gi"
              cpu: "500m"
            limits:
              memory: "4Gi"
              cpu: "4000m"
          readinessProbe:
            httpGet:
              path: /api/health
              port: 4040
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /api/health
              port: 4040
            initialDelaySeconds: 30
            periodSeconds: 30
            failureThreshold: 5

        # -- MCP Canvas sidecar --
        - name: mcp-canvas
          image: "{{ registry }}/claude-manager/backend:{{ tag }}"
          command: ["python3", "-m", "backend.mcp.canvas_server"]
          ports:
            - containerPort: 4041
          env:
            - name: CANVAS_API_URL
              value: "http://localhost:4040"

        # -- MCP Orchestrator sidecar --
        - name: mcp-orchestrator
          image: "{{ registry }}/claude-manager/backend:{{ tag }}"
          command: ["python3", "-m", "backend.mcp.orchestrator_server"]
          ports:
            - containerPort: 4042
          env:
            - name: MANAGER_API_URL
              value: "http://localhost:4040"

      volumes:
        - name: claude-data
          hostPath:
            path: {{ .Values.backend.hostHome }}/.claude
            type: Directory
        - name: managed-projects
          hostPath:
            path: {{ .Values.backend.managedProjectsDir }}
            type: Directory
        - name: claude-manager-src
          hostPath:
            path: {{ .Values.backend.repoDir }}/claude-manager
            type: Directory
        - name: kind-infra-src
          hostPath:
            path: {{ .Values.backend.repoDir }}/kind-infra
            type: Directory
        - name: helm-platform-src
          hostPath:
            path: {{ .Values.backend.repoDir }}/helm-platform
            type: Directory
        - name: ssh-keys
          hostPath:
            path: {{ .Values.backend.hostHome }}/.ssh
            type: Directory
        - name: gitconfig
          hostPath:
            path: {{ .Values.backend.hostHome }}/.gitconfig
            type: File
        - name: docker-socket
          hostPath:
            path: /var/run/docker.sock
            type: Socket
```

### Backend Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: {{ .Release.Namespace }}
spec:
  selector:
    app: backend
  ports:
    - name: http
      port: 4040
      targetPort: 4040
    - name: mcp-canvas
      port: 4041
      targetPort: 4041
    - name: mcp-orchestrator
      port: 4042
      targetPort: 4042
```

### Ingress Update

The existing ingress proxies only to the frontend. Update to route `/api/` and `/ws` to the backend Service:

```yaml
spec:
  rules:
    - host: claude-manager.localhost
      http:
        paths:
          - path: /api/
            pathType: Prefix
            backend:
              service:
                name: backend
                port:
                  number: 4040
          - path: /ws
            pathType: Prefix
            backend:
              service:
                name: backend
                port:
                  number: 4040
          - path: /
            pathType: Prefix
            backend:
              service:
                name: frontend
                port:
                  number: 80
```

This eliminates the need for the frontend nginx to proxy to `host.docker.internal`. The nginx.conf in the frontend container simplifies to just serving static files.

### Values Addition

```yaml
backend:
  enabled: true
  image:
    repository: claude-manager/backend
    tag: latest
    pullPolicy: IfNotPresent
  replicas: 1
  hostHome: /Users/ayx106492  # Resolved at deploy time
  repoDir: /Users/ayx106492/git
  managedProjectsDir: /Users/ayx106492/git/claude-managed-projects
  resources:
    requests:
      memory: "1Gi"
      cpu: "500m"
    limits:
      memory: "4Gi"
      cpu: "4000m"
```

### RBAC

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: claude-manager-backend
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
  name: cluster-admin  # Agents need broad access for deploys
  apiGroup: rbac.authorization.k8s.io
```

---

## Code Changes Required

### 1. `agent_session.py` -- OAuth token path

```python
# Before:
OAUTH_TOKEN_FILE = "/run/claude-oauth-token"

# After:
OAUTH_TOKEN_FILE = os.environ.get(
    "OAUTH_TOKEN_FILE",
    str(Path(os.environ.get("CLAUDE_DATA_DIR", "/root/.claude")) / "oauth-token")
)
```

### 2. Frontend `nginx.conf` -- remove backend proxy

When the ingress handles `/api/` and `/ws` routing, the frontend nginx.conf only needs:

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

### 3. MCP config JSON files

Update `controller_mcp_config.json` and `canvas_mcp_config.json` to use `localhost` URLs (since MCP servers are sidecars in the same pod).

### 4. `backend/Dockerfile` -- bake in CLI binaries

Add kubectl, helm, docker CLI binaries directly to the image instead of relying on bind-mounted `.host-bins/`:

```dockerfile
# Install kubectl
RUN curl -fsSL "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/arm64/kubectl" \
    -o /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl

# Install helm
RUN curl -fsSL https://get.helm.sh/helm-v3.17.0-linux-arm64.tar.gz | \
    tar xz -C /usr/local/bin --strip-components=1 linux-arm64/helm
```

### 5. Kubeconfig for in-cluster access

Add to `entrypoint.sh`:

```bash
# If running in k8s, generate a kubeconfig from the service account
if [ -f /var/run/secrets/kubernetes.io/serviceaccount/token ]; then
  KUBE_TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
  KUBE_CA=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
  kubectl config set-cluster in-cluster \
    --server=https://kubernetes.default.svc \
    --certificate-authority=$KUBE_CA
  kubectl config set-credentials sa-user --token=$KUBE_TOKEN
  kubectl config set-context default --cluster=in-cluster --user=sa-user
  kubectl config use-context default
  echo "[entrypoint] Generated in-cluster kubeconfig"
fi
```

### 6. Add health endpoint

Add `/api/health` to `main.py` for probes:

```python
@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

---

## OAuth Token Refresh Mechanism

### Host-side launchd plist

Create `~/Library/LaunchAgents/com.claude-manager.token-refresh.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-manager.token-refresh</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>
          TOKEN=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])")
          [ -n "$TOKEN" ] && echo "$TOKEN" > ~/.claude/oauth-token
        </string>
    </array>
    <key>StartInterval</key>
    <integer>1800</integer>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
```

Load: `launchctl load ~/Library/LaunchAgents/com.claude-manager.token-refresh.plist`

The token lands in `~/.claude/oauth-token`, which is hostPath-mounted into the pod at `/root/.claude/oauth-token`.

---

## Kind Cluster Changes

### Additional hostPath mounts needed in `cluster.yaml.tmpl`

Currently mounted on workers:
- `~/.claude` (read-write)
- `~/.local/share/claude/versions` (read-only)
- `~/git/claude-managed-projects` (read-write)
- `~/.ssh` (read-only)
- `~/.gitconfig` (read-only)

Need to add:
```yaml
- hostPath: ${HOME}/git/claude-manager
  containerPath: ${HOME}/git/claude-manager
  readOnly: true
- hostPath: ${HOME}/git/kind-infra
  containerPath: ${HOME}/git/kind-infra
  readOnly: true
- hostPath: ${HOME}/git/helm-platform
  containerPath: ${HOME}/git/helm-platform
  readOnly: true
```

**Important:** Adding extraMounts requires recreating the kind cluster. This is a one-time operation but causes downtime for all services. Plan accordingly.

---

## Jenkins CI/CD Changes

### Build Pipeline

Update `ci/build.Jenkinsfile`:
- Build **two** images: `claude-manager/frontend` (existing) and `claude-manager/backend` (new)
- Push both to the in-cluster registry
- Trigger deploy with both image tags

### Deploy Pipeline

Update `ci/deploy.Jenkinsfile`:
- `helm upgrade` now deploys both frontend and backend
- Pass `backend.image.tag` and `frontend.image.tag` as values

---

## Migration Steps

### Phase 1: Preparation (no downtime)

1. **Add health endpoint** to backend (`/api/health`)
2. **Update `agent_session.py`** to read OAuth token from configurable path
3. **Create launchd plist** for OAuth token refresh; load it
4. **Update Dockerfile** to bake in kubectl, helm binaries
5. **Create backend Helm templates** (Deployment, Service, ServiceAccount, RBAC)
6. **Update ingress** to route `/api/` and `/ws` to backend Service
7. **Update values.yaml** with backend configuration
8. Test all changes with docker-compose (backward compatible)

### Phase 2: Cluster Prep (brief downtime for cluster recreate)

8. **Update `cluster.yaml.tmpl`** with additional hostPath mounts
9. **Recreate kind cluster** (`kind delete cluster --name scoady && create script`)
10. **Redeploy all services** (Jenkins, registry, existing apps)
11. **Verify** frontend still works, all hostPath mounts accessible

### Phase 3: Parallel Deploy (no downtime)

12. **Build backend image** and push to registry
13. **Deploy with `backend.enabled: true`** via Helm
14. **Verify** backend pod is running, health checks pass
15. **Test WebSocket** connections from frontend to backend via ingress
16. **Test agent spawning** -- dispatch a test agent, verify it runs

### Phase 4: Cutover

17. **Update frontend nginx.conf** to remove `host.docker.internal` proxy blocks
18. **Rebuild and redeploy** frontend
19. **Verify** end-to-end flow: UI -> ingress -> backend Service -> agent spawn
20. **Stop docker-compose** backend on the host
21. **Monitor** for 24 hours

### Phase 5: Cleanup

22. Remove docker-compose.yml or mark backend service as disabled
23. Update `scripts/start.sh` to only handle OAuth refresh (or remove in favor of launchd)
24. Update documentation and CLAUDE.md
25. Remove `.host-bins/` directory (binaries baked into image)

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| OAuth token expires before refresh | Low | High (all agents fail) | 30-min refresh interval; monitor token age; alert on auth failures |
| Pod eviction under memory pressure | Medium | High (all agents die) | Generous resource requests; `Guaranteed` QoS; node affinity |
| hostPath mount missing after cluster recreate | Low | High (backend crashes) | Validation script checks mounts exist before deploy |
| WebSocket connections dropped by ingress | Low | Medium (UI loses live updates) | Nginx ingress annotations for long-lived connections; reconnect logic already in frontend |
| Kind cluster recreate causes extended downtime | Low | Medium | Script the full bootstrap; test in advance; schedule during low-usage |
| Agent file access breaks due to path mismatch | Low | High | Mount at same absolute paths as on host; test with path-heavy agents |
| Docker socket access different inside kind | Low | Medium | Test Docker commands from within the pod before cutover |
| MCP sidecar communication failure | Low | Medium | Sidecars share pod network; use `localhost`; add health checks |

---

## Open Questions

1. **Cluster recreate timing:** When can we tolerate the kind cluster being down for 10-15 minutes? All services (Jenkins, frontend, other apps) go offline during recreate.

2. **Multi-architecture images:** The Dockerfile currently targets the build platform. With Kaniko in k8s building for linux/arm64, this should work, but needs verification.

3. **Build runner:** `scripts/start.sh` also starts a `build-runner.py` on the host (port 4050). Does this need to move to k8s as well, or can it remain a host process?

4. **Database:** Is PostgreSQL currently in use? If so, where does it run? Should it move to k8s as well?

5. **Concurrent agent limit:** Should we enforce a max-agents limit via resource quotas, or continue relying on the broker's parallelism settings?

6. **Rollback plan:** If the migration fails, reverting to docker-compose is as simple as `docker compose up`. Should we keep docker-compose.yml as a permanent fallback?

7. **Claude CLI version pinning:** The Dockerfile does `npm install -g @anthropic-ai/claude-code` (latest). Should we pin to a specific version for reproducibility?

---

## Estimated Effort

| Phase | Effort | Calendar Time |
|---|---|---|
| Phase 1: Preparation | 4-6 hours | 1 day |
| Phase 2: Cluster prep | 1-2 hours | 1 day (includes redeploy) |
| Phase 3: Parallel deploy | 2-3 hours | 1 day |
| Phase 4: Cutover | 1 hour | Same day |
| Phase 5: Cleanup | 1-2 hours | Next day |
| **Total** | **9-14 hours** | **3-4 days** |
