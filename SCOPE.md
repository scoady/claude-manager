# Claude Manager — Project Scope

## What This Is

Claude Manager is a **web-based agent orchestration dashboard** for managing AI-driven software development projects. It lets you define projects, dispatch tasks to Claude agents, watch them work in real time, and inject follow-up messages — all from a browser UI.

It is **not** a conversation history viewer. It is a control plane for running Claude agents autonomously against real codebases.

---

## Core Concepts

### Managed Projects
Projects live in `~/git/claude-managed-projects/` on the host machine. Each subdirectory is a git repository representing one managed project. Every project is bootstrapped with a standard structure:

```
my-project/
├── PROJECT.md              # Goals, scope, constraints for this project
├── .claude/
│   ├── settings.local.json # Tool permissions (allow/deny lists)
│   ├── INSTRUCTIONS.md     # System context injected into every agent session
│   └── manager.json        # Parallelism (max concurrent agents), model override
└── (actual project source files)
```

### Task Dispatch
From the UI, you type a task prompt and dispatch it to a project. The backend spawns one or more `claude` subprocesses (per the project's `parallelism` setting) running non-interactively with streaming output:

```bash
claude -p "your task" --output-format stream-json --include-partial-messages
```

The session ID is parsed from the first `system.init` stream event and tracked in memory.

### Live Streaming
Agent output streams to the browser in real time via WebSocket (`agent_stream` events). Each agent card in the workbench shows a live preview of the last output chunk. Clicking an agent opens a detail panel with the full conversation stream.

### Message Injection
While an agent is working, you can inject a follow-up message. If the agent is mid-response, the message is queued and sent automatically via `--resume <session_id>` as soon as the current invocation finishes. If the agent is idle, it's sent immediately.

### Parallelism
Each project has a configurable parallelism setting (1–4). Dispatching a task with parallelism=3 spawns 3 independent claude subprocesses all working on the same prompt in the project's directory.

---

## Architecture

### Infrastructure
- Deployed to a local **kind** Kubernetes cluster (`kind-techmart`)
- Build pipeline: **Jenkins** (Kaniko) → in-cluster registry (`registry.registry.svc.cluster.local:5000`) → Helm → K8s
- Namespace: `claude-manager`
- Jenkins jobs: `claude-manager-build` (ci/build.Jenkinsfile), `claude-manager-deploy` (ci/deploy.Jenkinsfile)

### Backend (`backend/`)
Python FastAPI on port 4040.

| Service | Purpose |
|---------|---------|
| `services/projects.py` | Scan `MANAGED_PROJECTS_DIR`, read `PROJECT.md`, bootstrap new projects |
| `services/spawner.py` | Spawn/kill claude subprocesses, stream stdout, manage injection queue |
| `services/agents.py` | Read `~/.claude/projects/*.jsonl` for conversation history |
| `services/settings.py` | Global `~/.claude/settings.json` + plugins (read/write) |
| `ws_manager.py` | Broadcast WebSocket events to all connected browser clients |

### Frontend (`frontend/`)
Vite + vanilla JS, served by nginx on port 80, proxies `/api/` and `/ws` to the backend.

| File | Purpose |
|------|---------|
| `js/app.js` | App state, WS event handlers, dispatch/inject/kill orchestration |
| `js/projects.js` | Project nav, tile grid, workbench, agent mini-cards |
| `js/utils.js` | Shared: `escapeHtml`, `toast`, formatters |
| `js/api.js` | REST client for all backend endpoints |
| `js/ws.js` | Auto-reconnecting WebSocket client |
| `js/settings.js` | Global settings + plugins settings panel |

### K8s Requirements (critical)
- Backend pod needs `hostPID: true` to see host processes
- Three hostPath mounts required:
  - `~/.claude` → `/home/claude/.claude` (session files + settings)
  - `~/.local/bin/claude` → `/usr/local/bin/claude` (the CLI binary)
  - `~/git/claude-managed-projects` → `/home/claude/managed-projects` (managed projects)
- **Single replica only** — filesystem access is node-local

### Environment Variables
| Var | Default | Purpose |
|-----|---------|---------|
| `CLAUDE_DATA_DIR` | `~/.claude` | Path to claude data directory |
| `CLAUDE_BIN` | `~/.local/bin/claude` | Path to claude CLI binary |
| `MANAGED_PROJECTS_DIR` | `~/git/claude-managed-projects` | Managed projects root |

---

## WebSocket Events

| Event | Direction | Payload |
|-------|-----------|---------|
| `project_list` | server→client | Full list of managed projects (on connect + every 5s) |
| `agent_spawned` | server→client | `{session_id, project_name, task, started_at}` |
| `agent_stream` | server→client | `{session_id, project_name, chunk, done}` |
| `agent_done` | server→client | `{session_id, project_name, exit_code}` |
| `agent_update` | server→client | Updated agent object (status change, pending injection) |
| `stats_update` | server→client | Global stats (projects, agents, working count, uptime) |

---

## UI Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ Header: logo · stats bar (projects / working / agents / uptime) · nav│
├──────────────┬──────────────────────────────────┬───────────────────┤
│ Project Nav  │ Project Workbench                 │ Agent Detail      │
│              │                                   │ (hidden until     │
│ · project-a  │ [Project Name]  parallelism: 1    │  agent clicked)   │
│ · project-b● │                                   │                   │
│ · project-c  │ ┌──────────┐  ┌──────────┐        │ ● working         │
│              │ │ Agent 1  │  │ Agent 2  │        │ project-b         │
│ [+] new      │ │ working● │  │ idle     │        │ "refactor auth…"  │
│              │ │ task…    │  │ task…    │        │                   │
│              │ └──────────┘  └──────────┘        │ [conversation     │
│              │                                   │  history here]    │
│              │ ┌─────────────────────────────┐   │                   │
│              │ │ Give this project a task…   │   │ ┌───────────────┐ │
│              │ │                        [→]  │   │ │ inject msg…[→]│ │
│              │ └─────────────────────────────┘   │ └───────────────┘ │
└──────────────┴──────────────────────────────────┴───────────────────┘
```

Empty state (no project selected) shows a tile grid of all managed projects.

---

## Current Status / Known Issues

- Agent output streaming works via `--output-format stream-json --include-partial-messages`
- Session ID is discovered from the `{"type":"system","subtype":"init","session_id":"..."}` event in the stream
- The `~/git/claude-managed-projects/` directory must exist on the host **before** deploying (the `DirectoryOrCreate` hostPath type handles this automatically in K8s)
- Git is called via `subprocess` during bootstrap — failure is non-fatal (directory still created)
- Agents are tracked in-memory only — restarting the backend pod loses track of running agents (their sessions still exist in `~/.claude/projects/` though)
