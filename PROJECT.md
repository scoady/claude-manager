# Claude Agent Manager

An agent orchestration dashboard for managing autonomous Claude Code agents across multiple projects.

## Architecture

- **Frontend**: Vite SPA (vanilla JS, no framework) served via k8s Nginx pod at `claude-manager.localhost`
- **Backend**: FastAPI in docker-compose on host (port 4040), proxied via k8s ingress
- **Agents**: `claude --print --output-format stream-json` subprocesses managed by AgentBroker
- **CI/CD**: `gpush` triggers Jenkins build (Kaniko) + deploy (Helm) to local kind cluster

## Key Directories

```
backend/           — FastAPI app, agent broker, services, models
frontend/          — Vite SPA: js/, css/, index.html
frontend/js/feed/  — FeedController, AgentSection, ToolBlock, ArtifactsPanel
frontend/js/       — app.js (global state + WS router), api.js, settings.js
frontend/css/      — app.css (design tokens), feed.css (agent UI)
mockups/           — HTML design mockups (open in browser to preview)
backend/templates/ — Workflow template JSON files
scripts/           — start.sh (OAuth extraction + docker compose)
ci/                — Jenkinsfiles for build + deploy
infrastructure/    — Helm chart for k8s frontend
```

## Design System

Vaporwave dark theme:
- Backgrounds: deep indigo (#080c14, #0e1525, #141d30)
- Accents: cyan #67e8f9, magenta #c084fc, green #4ade80, amber #fbbf24, teal #5eead4, purple #a78bfa
- Fonts: IBM Plex Mono (code/data), DM Sans (UI), Plus Jakarta Sans (titles), Instrument Serif (display)
- Style: particle effects, constellation animations, glowing neon accents, frosted glass overlays
- Layout: fluid CSS Grid, clamp() for sizing, no static pixel max-widths

## Current Focus (v1.7.0+)

**Dynamic Canvas** — A widget-based canvas where the orchestrator agent controls the dashboard layout via MCP tools. Instead of a fixed UI, agents author widgets as raw HTML/CSS/JS, creating infinitely unique visualizations. Key concepts:

1. Canvas Engine (frontend) — renders widgets in Shadow DOM, smooth CSS transitions for all position/size changes
2. Canvas API (backend) — REST + WebSocket for widget CRUD
3. MCP Server — tools for agents to create/update/remove/animate widgets on the canvas

Mockups in `mockups/v7-agent-canvas.html` demonstrate the PoC.

## Conventions

- Always create 3+ HTML mockups in `mockups/` before implementing visual changes
- Use `gpush` instead of `git push` (triggers Jenkins automatically)
- Backend startup: `docker compose down && bash scripts/start.sh -d` (NEVER raw docker compose up)
- Frontend deploy: `gpush` triggers Jenkins build + k8s deploy
