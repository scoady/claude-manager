---
name: coding-style
description: Development conventions, branching strategy, CI/CD workflow, and coding standards for claude-manager and managed projects.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Coding Style & Development Workflow

Follow these conventions when working on claude-manager or any managed project.

## Skills to Load

Always have these skills available:
- `/deploy` — build, push, trigger Jenkins CI/CD, restart backend
- `/release` — create versioned releases with git tags and GitHub releases

## Branching Strategy

Work on branches, never commit directly to `main`.

| Type | Prefix | Example |
|------|--------|---------|
| New feature | `feature/` | `feature/add-dark-mode` |
| Bug fix | `fix/` | `fix/websocket-reconnect` |
| Maintenance | `chore/` | `chore/update-deps` |

```bash
git checkout -b feature/my-feature
# ... make changes ...
git push -u origin feature/my-feature
```

Merge to `main` via PR when ready, or fast-forward merge for solo work.

## Commit Messages

Use conventional commit format:

```
<type>: <short description>

[optional body with more detail]

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `style`, `perf`

Examples:
- `feat: add per-project skill toggles with symlink management`
- `fix: settings view not rendering — move inside #app flex container`
- `chore: update marked.js to v12`

Keep the subject line under 72 characters. Use the body for the "why", not the "what".

## Testing

- Write tests for new features when a test framework exists in the project
- Run existing tests before committing: check for `test`, `spec`, or `__tests__` directories
- If no test framework exists, manually verify changes work end-to-end
- For frontend changes: load the page, check the browser console for errors
- For backend changes: hit the API endpoints, check logs with `docker logs claude-manager-backend-1`

## Git Workflow

- **Always use `gpush`** instead of `git push` — it triggers Jenkins automatically
- Never force-push to `main`
- Stage files explicitly (not `git add -A`) to avoid committing secrets or binaries

## CI/CD Pipeline

Every repo follows this pattern:

```
gpush → Jenkins discovers matching build job
  → build job: Kaniko builds Docker images → pushes to in-cluster registry
  → build job triggers deploy job with IMAGE_TAG parameter
  → deploy job: helm upgrade --install → K8s namespace
```

Jenkins jobs come in pairs: `<app>-build` and `<app>-deploy`.

### Build Pipeline (`ci/build.Jenkinsfile`)

- Runs on `kaniko` agent
- Tags images with the git short SHA
- Pushes to `registry.registry.svc.cluster.local:5000/<app>/<component>:<tag>`
- Automatically triggers the deploy job

### Deploy Pipeline (`ci/deploy.Jenkinsfile`)

- Runs on `helm` agent
- Takes `IMAGE_TAG` parameter from the build job
- Runs `helm upgrade --install` with the image tag override
- Verifies rollout with `kubectl rollout status`

### Writing a New Helm Chart

For new applications, create a Helm chart under `infrastructure/helm/<app-name>/`:

```
infrastructure/helm/<app-name>/
├── Chart.yaml              # name, version, appVersion
├── values.yaml             # image registry, replicas, ingress host, resources
└── templates/
    ├── _helpers.tpl         # common labels (app, chart, managed-by)
    ├── deployment.yaml      # container spec, health probes, resource limits
    ├── service.yaml         # ClusterIP on port 80
    └── ingress.yaml         # nginx ingress with *.localhost hostname
```

Key conventions:
- Registry: `registry.registry.svc.cluster.local:5000`
- Ingress class: `nginx`, host pattern: `<app>.localhost`
- Resource requests: start small (32Mi memory, 25m CPU)
- Health probes: readiness at `/` with 5s interval, liveness at 15s
- Use `_helpers.tpl` for consistent labeling

### Writing Jenkinsfiles

For new apps, create `ci/build.Jenkinsfile` and `ci/deploy.Jenkinsfile` following the existing patterns. Key points:
- Build agent label: `kaniko`
- Deploy agent label: `helm`
- Image tag from `git rev-parse --short HEAD`
- Deploy triggered automatically from build via `build job: '<app>-deploy'`

## Backend Startup

**CRITICAL**: Always use `scripts/start.sh` to start the docker-compose backend — never raw `docker compose up`. The script extracts the OAuth token from macOS Keychain. Without it, agents fail with auth errors.

```bash
docker compose down && bash scripts/start.sh -d
```

## Code Style

- Vanilla JS with ES modules (no framework) for the frontend
- Python with FastAPI for the backend, Pydantic models for data
- CSS custom properties for theming — update `:root` tokens, not hardcoded colors
- Keep functions small and focused
- Prefer editing existing files over creating new ones
