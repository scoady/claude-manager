---
name: archupdate
description: Read the codebase and update the architecture documentation with current file map, data flows, and system topology.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Update Architecture Documentation

Analyze the current state of the claude-manager project and update the architecture reference at `~/.claude/projects/-Users-ayx106492-git-claude-manager/memory/architecture.md`.

## Steps

### 1. Scan the codebase

Read the current architecture file, then scan for changes:

```bash
# Backend services
ls backend/services/
ls backend/broker/
ls backend/rules/

# Frontend modules
ls frontend/js/
ls frontend/js/feed/
ls frontend/css/

# Infrastructure
ls infrastructure/helm/claude-manager/templates/
ls ci/
ls scripts/

# Skills
ls .claude/skills/*/SKILL.md

# Docker
cat docker-compose.yml
cat backend/Dockerfile
cat backend/entrypoint.sh
```

Also read key files to understand current features:
```
backend/main.py         — endpoints, lifespan hooks
backend/models.py       — data models
frontend/index.html     — SPA shell, CDN dependencies
frontend/js/app.js      — state shape, WS events
```

### 2. Identify what changed

Compare the current architecture.md against the actual codebase:
- New files or modules added
- Files removed or renamed
- New endpoints or WebSocket events
- Changes to data flow (agent lifecycle, status cards, etc.)
- Changes to CSS design tokens or UI structure
- Changes to Docker setup (new packages, volume mounts, env vars)
- New or removed skills

### 3. Update architecture.md

Use the Edit tool to update `~/.claude/projects/-Users-ayx106492-git-claude-manager/memory/architecture.md` in place. Preserve the existing section structure:

```markdown
# Claude Manager — Architecture & Data Flow Reference

## System Topology
## Key Files (Backend, Frontend, Skills, Infrastructure tables)
## Data Flow: Agent Lifecycle
## Agent Section UI Structure
## WebSocket Event Types
## Frontend State
## UI Layout Structure
## CSS Design Tokens
## Docker Container Setup
## Skills System
## Build & Deploy
```

Guidelines:
- Keep the file map tables accurate — add new files, remove deleted ones
- Update the data flow diagram if the agent lifecycle changed
- Update CSS tokens if the color scheme changed
- Update Docker section if Dockerfile or compose changed
- Keep descriptions concise (one-line per file in tables)

### 4. Verify

- Every file listed in the architecture doc should actually exist
- Every backend service, frontend module, and skill should be accounted for
- WebSocket event types should match the WSMessageType enum in models.py
- Design tokens should match the current :root block in app.css

### Output

Report what changed:
- Files added/removed from the file map
- Sections updated
- New data flows or events documented
