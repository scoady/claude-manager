# Claude Manager — Goals

## Vision

Claude Manager is a **control plane for autonomous Claude agents**. It enables a single developer to orchestrate multiple AI agents working in parallel across real codebases — dispatching tasks, watching agents work in real time, injecting course corrections, and managing the full lifecycle from a browser dashboard.

The project exists because running `claude` in a single terminal doesn't scale. When you have 5 projects and want 3 agents working on each, you need a system that spawns, streams, monitors, and controls them — not 15 terminal windows.

---

## Core Goals

### 1. Agent Orchestration
- Dispatch tasks to managed projects with configurable parallelism (1–4 concurrent agents per task)
- Spawn agents as `claude --print --output-format stream-json` subprocesses
- Track agent lifecycle: STARTING → THINKING → GENERATING → TOOL_EXEC → IDLE → DONE
- Support message injection into running or idle agents via `--resume`
- Kill agents on demand

### 2. Real-Time Visibility
- Stream agent output (text deltas, tool calls, phase transitions) to the browser via WebSocket
- Render agent output as **markdown status cards** that replace on each turn completion
- Collapsible detail sections for raw streaming text and tool block inspection
- Milestone tracking: human-readable labels for tool calls (e.g., `Read · main.py`, `Bash · git status`)

### 3. Project Management
- Bootstrap new projects from the UI with standard structure (PROJECT.md, INSTRUCTIONS.md, settings)
- Sidebar navigation with live project status indicators
- Per-project configuration: parallelism, model override, skill toggles
- Tile grid overview when no project is selected

### 4. Skills System
- Discoverable skills (global + per-project) that agents load for domain-specific knowledge
- Toggle skills on/off per project via symlink management
- Built-in skills: `coding-style`, `deploy`, `release`
- Skill creation via API and UI

### 5. Developer Workflow Integration
- Agents have git access (SSH keys mounted) for commits, branches, and push
- GitHub CLI (`gh`) available for repo creation, PR management
- CI/CD awareness: agents understand the Jenkins → Kaniko → Helm pipeline
- Convention enforcement via skills (branching strategy, commit format, testing)

### 6. Operator Automation
- Rules engine with 30-second reconciliation loop
- Built-in rules: SessionHealthRule (cancel stuck agents), ProjectAutoSpawnRule (keep N agents alive), DirectoryWatchRule (trigger on file changes)
- Extensible: custom rules via Python subclass

---

## Completed Milestones

| Date | Milestone |
|------|-----------|
| 2026-02-28 | Initial README, project bootstrap, agent spawning via SDK |
| 2026-03-01 | Switch to CLI subprocess spawning, phase tracking, narrative feed UI |
| 2026-03-01 | PostgreSQL persistence layer (optional, graceful fallback) |
| 2026-03-01 | Live kanban tiles, font improvements, frontend dev mount |
| 2026-03-02 | Skills system: discovery, per-project toggles, marketplace, creator |
| 2026-03-02 | `/deploy` and `/release` skills |
| 2026-03-03 | Settings view fix (inside #app flex container) |
| 2026-03-03 | `coding-style` skill with full dev workflow conventions |
| 2026-03-03 | Vaporwave aesthetic overhaul (neon gradients, CRT scanlines, mesh backgrounds) |
| 2026-03-03 | Dynamic agent status cards (markdown rendering, detail toggle) |
| 2026-03-04 | GitHub CLI added to container, SSH auth for agents |

---

## Upcoming Goals

### Short-term
- [ ] `gh` authentication inside containers (token passthrough from host keychain)
- [ ] Agent-to-agent coordination (orchestrator + worker pattern)
- [ ] Session replay — step through a past session and fork from any decision point
- [ ] Persistent project memory — summarized context carried across sessions

### Medium-term
- [ ] GitHub integration — agents open PRs, request reviews, respond to comments
- [ ] Event-driven triggers — cron schedules, webhooks, file watchers as rule conditions
- [ ] Cost tracking — token usage per agent, per project, per day
- [ ] Agent templates — pre-built task prompts for common workflows (code review, refactoring, testing)

### Long-term
- [ ] Multi-user support with role-based access
- [ ] Remote agent execution (not just local docker-compose)
- [ ] Plugin marketplace for community-contributed skills
- [ ] Observability: OpenTelemetry traces for full agent lifecycle

---

## Non-Goals

- **Not a chatbot UI** — this is a control plane, not a conversation interface
- **Not a code editor** — agents write code; humans review it via git
- **Not a CI/CD system** — it integrates with Jenkins/Helm but doesn't replace them
- **Not multi-tenant** — single developer, local infrastructure
