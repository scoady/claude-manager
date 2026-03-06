# Tasks

# TASKS.md — Claude Manager v1.7.0

## Completed (v1.6.0)

---

## Sprint 1 — Canvas Foundation ✅

---

## Sprint 2 — Agent Integration + Constellation ✅

## Context

claude-manager is an agent orchestration platform that:
- Manages autonomous Claude Code agents across multiple projects
- Has a real-time WebSocket-driven dashboard showing agent activity
- Features a canvas widget system (GridStack) where agents create custom widgets
- Uses a "void aesthetic" — deep dark backgrounds (#030509), neon accents (cyan, amber, purple), constellation/space themes
- Supports MCP tools for agents to push widgets, data, and visualizations
- Has workflow phases (planning → sprints → review → retro)
- Manages projects with tasks, milestones, artifacts, and agent streams

## Research Areas

Search the web and explore these areas for inspiration:

### 1. Agent/AI Orchestration UIs
- How do other AI agent platforms visualize multi-agent coordination?
- Search for: "AI agent dashboard UI", "multi-agent orchestration interface", "LLM agent monitoring"
- Look at: CrewAI, AutoGen Studio, LangGraph Studio, Rivet, Flowise, Dify
- What patterns do they use that we could adopt or improve on?

### 2. Developer Tool Dashboards
- Vercel, Railway, Render — deployment dashboards
- Linear, Plane — project management with beautiful UI
- Grafana, Datadog — observability dashboards
- What makes these feel premium vs generic?

### 3. Creative/Generative Interfaces
- Figma, Framer — creative tool UIs
- TouchDesigner, Cables.gl — node-based visual programming
- Shadertoy, Dwitter — creative coding galleries
- How do creative tools present complex systems beautifully?

### 4. Sci-Fi/Futuristic UI Concepts
- Search for: "FUI design", "fantasy user interface", "sci-fi dashboard concept"
- Look at: Behance, Dribbble for sci-fi UI concepts
- Movie/game UIs: Iron Man JARVIS, Minority Report, Cyberpunk 2077, Westworld
- What visual language makes interfaces feel futuristic?

### 5. Real-Time Data Visualization
- Bloomberg Terminal aesthetic
- Air traffic control interfaces
- Stock trading platforms (dark themes)
- NASA mission control displays
- What patterns handle high-density real-time data elegantly?

### 6. Novel Interaction Patterns
- Command palettes (Raycast, Alfred, Spotlight)
- Spatial interfaces (Apple Vision Pro, Figma canvas)
- Zoomable UIs (Prezi, Miro, infinite canvas)
- Ambient/peripheral displays
- How could we make agent orchestration feel more spatial/immersive?

### 7. Dashboard Widget Design
- What are the most creative dashboard widgets youve seen?
- Widgets that tell stories, not just show numbers
- Widgets with personality and delight
- Micro-interactions that make data feel alive

## Output

Write your findings to INSPIRATION.md in the project root. Organize as:

1. **Executive Summary** — top 10 design ideas we should steal/adapt
2. **Agent Orchestration UIs** — what others do, what we could do better
3. **Visual Language** — color, typography, animation patterns worth adopting
4. **Interaction Patterns** — novel ways to interact with agent systems
5. **Widget Ideas** — 30+ specific widget concepts with descriptions
6. **Layout & Navigation** — how to organize an agent dashboard spatially
7. **Delight & Polish** — micro-interactions, easter eggs, ambient effects
8. **Technical References** — URLs, screenshots descriptions, specific examples

Be opinionated — dont just list things, recommend what would make claude-manager feel like nothing else out there. We want this to feel like a next-gen mission control for AI agents.

This document is for a human operator who needs to understand, maintain, troubleshoot, and extend the claude-manager system. It should be the single source of truth for operations.

## Research Phase

Thoroughly explore the entire codebase and infrastructure. Read every file that matters. Understand the full system before writing anything.

Key areas to research:
1. **Project structure** — every directory, what lives where, key files
2. **Backend** — FastAPI app, routers, services, models, MCP servers, agent broker
3. **Frontend** — Vite SPA structure, JS modules, CSS, canvas engine, widget system
4. **Infrastructure** — kind cluster (~/git/kind-infra), helm charts (~/git/helm-platform), Docker Compose backend
5. **CI/CD** — Jenkins pipeline, build/deploy jobs, gpush workflow
6. **Agent system** — how agents are spawned, managed, streamed; controller mode, workflows, phases
7. **Canvas/Widget system** — widget templates, canvas API, GridStack, MCP canvas server
8. **Configuration** — CLAUDE.md hierarchy, settings.json, .claude directories, managed project structure
9. **Startup/restart procedures** — scripts/start.sh, OAuth token extraction, Docker restart recovery
10. **Common failure modes** — registry /etc/hosts loss after Docker restart, Jenkins OOM, PVC issues
11. **APIs** — all REST endpoints, WebSocket events, MCP tool schemas
12. **Managed projects** — how they are created, structured, dispatched to
13. **Templates and workflows** — workflow template system, role manager, phase execution
14. **Skills and plugins** — how skills are installed, toggled, discovered
15. **Secrets and auth** — OAuth flow, SSH keys, Jenkins credentials, GitHub tokens

## Document Structure

Organize OPERATOR.md with these sections:

### System Overview
- Architecture diagram (ASCII art)
- Component inventory with URLs and ports
- Technology stack summary

### Quick Reference
- Start/stop/restart commands
- Health check commands
- Common URLs and endpoints
- Emergency recovery procedures

### Infrastructure
- Kind cluster details (nodes, namespaces, services)
- Helm charts and what they deploy
- Ingress routing rules
- Registry setup
- DNS/hosts configuration

### Backend
- FastAPI app structure
- All API endpoints with request/response examples
- WebSocket event catalog
- MCP server details
- Agent broker internals

### Frontend
- Module map
- Canvas engine and widget lifecycle
- Widget Studio and template system
- CSS design tokens and theming

### Agent System
- Agent lifecycle (spawn → stream → done)
- Controller mode vs direct mode
- Workflow phases and injection
- Parallelism and model configuration

### CI/CD Pipeline
- Jenkins job structure
- Build and deploy flow
- How to trigger builds
- How to add a new app to the pipeline

### Managed Projects
- Directory structure conventions
- PROJECT.md, TASKS.md, .claude/ directory
- How to create, configure, and manage projects

### Configuration Reference
- All config files and what they control
- Environment variables
- Settings hierarchy

### Troubleshooting
- Known failure modes and fixes
- Log locations
- Diagnostic commands
- Recovery procedures

### File Map
- Complete annotated file tree of the repository

## Quality Bar

- Every path should be absolute and copy-pasteable
- Every command should be runnable as-is
- Include actual port numbers, hostnames, namespace names
- Cross-reference related sections
- This should be good enough that a new operator could take over the system cold

Write the file to the project root as OPERATOR.md. Take your time — thoroughness matters more than speed.

