# Claude Manager — Agent Instructions

You are working on the claude-manager application itself. This is the orchestration dashboard that manages you and other agents.

## Safety Note

You are editing the system that is running you. This is safe because:
- The frontend is served from k8s (a pre-built artifact) — your edits don't take effect until deployed
- The backend runs in a docker container — your edits don't take effect until restarted
- Deploying is fine — the running agent sessions survive a frontend redeploy, and backend restarts gracefully reconnect

## Critical Rules

1. **Use `gpush`** instead of `git push` — it triggers Jenkins CI/CD automatically
2. **Backend startup**: `docker compose down && bash scripts/start.sh -d` — NEVER raw `docker compose up` (needs OAuth token extraction from macOS Keychain)
3. **Mockups first** — for any visual/layout change, create HTML mockups in `mockups/` before touching production code
4. **No fixed pixel sizes** — use CSS Grid auto-fit, clamp(), flex 1fr for layouts
5. **Test before deploying** — if you modify backend code, at minimum check for syntax errors before restarting

## Tech Stack

- Backend: Python 3.11, FastAPI, Pydantic, asyncio
- Frontend: Vanilla JS (no framework), Vite build, CSS custom properties
- Infra: docker-compose (backend), k8s via Helm (frontend), Jenkins CI/CD

## File Map

- `backend/main.py` — all REST/WS endpoints
- `backend/broker/agent_broker.py` — agent session management
- `backend/broker/agent_session.py` — single agent lifecycle + stream parsing
- `frontend/js/app.js` — global state, WS event router
- `frontend/js/feed/FeedController.js` — main feed: tabs, dispatch, agent sections
- `frontend/js/feed/AgentSection.js` — agent card UI
- `frontend/css/app.css` — design tokens + global styles
- `frontend/css/feed.css` — feed/agent styles

## Design Tokens

```css
--bg-base: #080c14; --bg-surface: #0e1525; --bg-elevated: #141d30;
--accent-green: #4ade80; --accent-amber: #fbbf24; --accent-cyan: #67e8f9;
--accent-magenta: #c084fc; --accent-purple: #a78bfa; --accent-teal: #5eead4;
--font-mono: 'IBM Plex Mono'; --font-ui: 'DM Sans'; --font-title: 'Plus Jakarta Sans';
```

## Workflow Mode

When you receive a workflow phase injection (messages starting with `## WORKFLOW PHASE:`),
you are in autonomous team workflow mode. Follow the phase instructions precisely:

- **Quarter Planning**: Create a full backlog in TASKS.md organized by sprint.
- **Sprint Planning**: Assign tasks to team roles using @role-N tags.
- **Sprint Execution**: Delegate ALL work to subagents via the Agent tool. CRITICAL: each
  subagent must work in their assigned git worktree directory. Include the worktree path
  in every subagent prompt and tell them to `cd` there first.
- **Sprint Review**: Spawn QA subagents to review code in each worktree.
- **Sprint Retro**: Generate a sprint report with metrics and carry-over items.

Always complete the full phase before going idle. The system auto-injects the next phase.
