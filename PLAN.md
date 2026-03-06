# C9s Control Center — Implementation Plan

## System Overview

Two components:
- **C9s binary** — FastAPI backend providing Claude API access, agent lifecycle, project/task CRUD, canvas/widget management, MCP servers
- **Scenes app** — Grafana plugin (React + @grafana/scenes) making requests to the C9s API

---

## Domain Model

```
Global Controller (single, persistent)
│
├── Project A
│   ├── Task 1 → Agent (worker)
│   ├── Task 2 → Agent (worker)
│   └── Canvas
│       ├── Widget (terminal)
│       ├── Widget (kanban)
│       └── Widget (constellation)
│
├── Project B
│   ├── Task 1 → Agent (worker)
│   └── Canvas
│       └── Widget (status card)
│
└── (no project context)
    └── Canvas (Mission Control global canvas)
        ├── Widget (project cards)
        └── Widget (agent activity)
```

### Construct Definitions

| Construct | Definition | Lifecycle |
|---|---|---|
| **Controller** | Single global agent. Receives all user dispatch requests. Routes tasks to projects, tracks running agents, responds to user queries. Never writes code. | Starts on boot, persists indefinitely |
| **Project** | Top-level organizational unit. A git repo with PROJECT.md, tasks, config. | User-created |
| **Task** | A unit of work within a project (line in TASKS.md). Has status: pending/in_progress/done/blocked. | Created by user or controller |
| **Agent** | A `claude` subprocess dispatched to execute a specific task in a specific project. Has capabilities defined by its MCP config. | Spawned per-task, dies on completion |
| **Canvas** | A blank container that holds widgets. Rendered via GridStack. Exists per-project and at the global level (Mission Control). | Created with project, persists |
| **Widget** | A self-contained visual component (HTML/CSS/JS + data) rendered inside a canvas. Created by agents via MCP or by users via the Widget Studio. | Dynamic — agents create/update/remove at runtime |
| **Layout Preset** | A saved arrangement of widgets — their types, grid positions, sizes. Users save and load these. | User-created, reusable across projects |
| **Widget Catalog** | The browsable collection of widget types (each with html/css/js templates + data_schema). Agents generate new types via MCP. | Grows over time as agents create new viz types |

### Vocabulary Cleanup

| Old Term | New Term | Why |
|---|---|---|
| Workflow Template | (remove from Scenes app) | Workflow orchestration stays in the main dashboard, not Grafana |
| Template (ambiguous) | **Layout Preset** (arrangement) or **Widget Type** (catalog entry) | Two different things had the same name |
| Template Library tab | **Widget Studio** | Browsing/creating widget types, not workflow templates |

---

## Architecture Changes

### 1. Global Controller (backend)

**Current:** Per-project controller spawned on project creation. Controller prompt hardcoded in `main.py:352-400`. Dispatch routing checks for project-level controller (`broker.get_controller_for_project`), falls back to standalone agent.

**New:** Single global controller started on boot. No project affiliation.

#### Changes to `backend/main.py`:

- `lifespan()`: Spawn one global controller on startup (not per-project in `create_project`)
- `create_project()`: Remove `_spawn_controller()` call. Just create the project.
- `dispatch_task()`: Always inject into the global controller. No fallback path needed.
  - Controller decides: spawn agent, answer directly, or delegate
  - If controller is busy (actively generating), queue the message (inject still works — it resumes the session)

#### Changes to `backend/services/spawner.py`:

- Add `_global_controller: RunningAgent | None` module-level ref
- `spawn_global_controller()` — starts the controller with global MCP tools
- `get_global_controller() -> RunningAgent | None`
- Remove `is_controller` from per-project spawn path
- Controller MCP config gets: `create_tasks`, `dispatch_agent`, `get_agents`, `list_tasks`, `report_complete`, `canvas_put`, `canvas_remove`, `canvas_list`, `canvas_templates`

#### Controller Prompt (simplified):

```
You are the GLOBAL CONTROLLER for the C9s agent orchestration system.

You receive user requests and route them to the right projects and agents.

Your capabilities:
- create_tasks(project, tasks) — add tasks to a project
- dispatch_agent(project, task_index) — spawn a worker agent for a task
- get_agents(project) — check running agents
- list_tasks(project) — see task status
- canvas_put(...) — update dashboard widgets
- canvas_remove(...) — remove widgets

Rules:
1. THINK before acting. Can you answer without spawning a worker?
2. Never write code yourself. You are a router and supervisor.
3. Narrate what you're doing — the user sees your output in real-time.
4. When spawning workers, be specific about the task. One task per agent.
5. Monitor agents and report results when they finish.
```

### 2. Scenes App — Screen Hierarchy

```
Scenes App (Grafana plugin)
├── Mission Control (home)              — Global canvas with project cards, agent activity, stats
├── Project Browser                     — Card grid of all projects, "New Project" button
│   └── Project Detail (click project)  — Canvas landing (terminal + kanban), tasks, agents
├── Widget Studio                       — Browse widget catalog, generate new types, preview
├── Layout Studio                       — Arrange widgets into presets, save/load
└── Settings                            — (future) Global config, model defaults
```

No react-router — use `useState` tab switching (proven to work with Grafana's SystemJS loader).

---

## Screen Designs

### Screen 1: Mission Control (Home)

The global overview. A canvas that can hold widgets — but ships with sensible defaults.

**Default widgets:**
- **Project Cards** — compact card per project showing name, active agent count, task completion ratio, last activity timestamp. Click → navigate to Project Detail.
- **Agent Activity** — live feed of agent milestones across all projects. Scrolling ticker.
- **Stats Bar** — working agents, idle agents, total projects, uptime. Inline at top.
- **Quick Dispatch** — text input + project selector + "Go" button. Sends to global controller.

**Key interaction:** The Quick Dispatch bar is always visible. Type a task, pick a project, hit enter. Controller handles the rest. This is the primary way users interact.

### Screen 2: Project Browser

Card grid. Each card shows:
- Project name (bold)
- Description (1-line truncated)
- Agent activity indicator (pulsing dot if agents working)
- Task ratio badge: "3/7 done"
- Last activity: "2m ago"

**Actions:**
- Click card → Project Detail
- "+ New Project" card (always last) → modal with name, description, model picker

No filters, no search (until there are 20+ projects). Keep it clean.

### Screen 3: Project Detail (the control panel feel)

**This is the money screen.** When you land here, you should feel like you're sitting at a mission control console for that project.

**Layout:** Full canvas — GridStack grid pre-loaded with:

```
┌─────────────────────────┬──────────────────────────┐
│                         │                          │
│   Task Kanban           │   Agent Constellation    │
│   (5 cols, 6 rows)      │   (7 cols, 6 rows)       │
│                         │                          │
├─────────────────────────┴──────────────────────────┤
│                                                     │
│   Terminal (12 cols, 5 rows, locked to bottom)       │
│   Tabs: one per active agent, live streaming output  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

This IS the canvas. Agents can modify it at runtime via MCP. The user can drag/resize widgets. The layout auto-saves.

**Terminal widget behavior:**
- Tab per active agent (named by star name if available, otherwise session_id prefix)
- Live WebSocket streaming of agent output
- Input field at bottom: type a message → inject into the focused agent
- Agent status indicator (working/idle/done) per tab

**Task Kanban widget behavior:**
- Columns: Pending | In Progress | Done | Blocked
- Single-line compact items with task title
- Click task → expand inline with agent assignment, dispatch button
- Drag between columns to change status (future)

**Agent Constellation widget behavior:**
- Visual star map of agents for this project
- Click agent star → switches terminal tab to that agent
- Status encoded as star color/brightness (working=bright cyan, idle=amber, done=dim)

**Top bar (not a widget — fixed chrome):**
- Project name + description
- "Dispatch Task" button → opens inline text input, types task, enter to send to controller
- Back arrow → Project Browser

**Critical UX decisions:**
- No separate "Tasks tab", "Agents tab", "Canvas tab" — it's ALL the canvas. The kanban IS the task view. The constellation IS the agent view. The terminal IS the communication channel.
- The dispatch bar replaces any separate dispatch modal. It's always one click away.
- Layout presets let users swap between arrangements (e.g. "Dev Mode" with big terminal, "Overview" with big kanban)

### Screen 4: Widget Studio

Browse the widget catalog. Create new widget types.

**Layout:**
- Top: "Generate Widget" bar — describe what you want in natural language, hit generate. Spawns a design subagent that creates the widget type and adds it to the catalog.
- Below: Card grid of all widget types from `/api/widget-catalog`
  - Each card: name, category badge, description, thumbnail preview (rendered from `preview_data`)
  - Click card → detail panel slides in: full-size preview, data schema, "Add to Canvas" button (pick which project), "Copy MCP snippet", "Delete"

**This is the existing Widget Studio from the main dashboard (`initTemplateCatalog` in app.js), rebuilt as a Scenes React component.**

### Screen 5: Layout Studio

Manage layout presets.

**Layout:**
- Left panel: List of saved presets (name, widget count, thumbnail)
- Right panel: Preview of selected preset — shows widget grid positions as colored rectangles with widget type labels
- Actions: "Save Current Layout" (captures current project's canvas arrangement), "Load Preset" (applies to selected project), "Delete Preset", "Rename"

**Data model for Layout Preset:**
```json
{
  "id": "dev-mode",
  "name": "Dev Mode",
  "description": "Big terminal, small kanban",
  "widgets": [
    { "widget_type": "task-kanban", "col": 0, "row": 0, "w": 4, "h": 6 },
    { "widget_type": "claude-terminal", "col": 4, "row": 0, "w": 8, "h": 11 }
  ]
}
```

**New API endpoints needed:**
- `GET /api/layout-presets` — list all presets
- `POST /api/layout-presets` — save new preset
- `PUT /api/layout-presets/{id}` — update
- `DELETE /api/layout-presets/{id}` — delete
- `POST /api/canvas/{project}/apply-preset/{preset_id}` — apply preset to a project's canvas

---

## Implementation Phases

### Phase 1: Global Controller (backend only)

**Goal:** Replace per-project controllers with a single global controller.

1. Add `_global_controller` to `spawner.py`, `spawn_global_controller()`, `get_global_controller()`
2. Update `lifespan()` in `main.py` to spawn global controller on boot
3. Remove controller spawn from `create_project()`
4. Update `dispatch_task()` to always route through global controller
5. Simplify controller prompt — remove per-project context, make it a pure router
6. Test: create project, dispatch task, verify controller delegates to worker

**Risk:** Controller prompt engineering. The global controller needs to handle multi-project context without getting confused. Keep the prompt minimal.

### Phase 2: Project Detail Canvas (Scenes app)

**Goal:** The "control panel feel" — land on a project and see terminal + kanban + constellation.

1. Create `ProjectDetailPanel.tsx` — full canvas with GridStack (load from CDN like the main dashboard)
2. Render terminal widget (WebSocket streaming, agent tabs, message inject)
3. Render task kanban widget (fetch from `/api/projects/{name}/tasks`, column layout)
4. Render agent constellation widget (fetch from `/api/agents`, filter by project, SVG/canvas animation)
5. Top bar with project name, dispatch input, back button
6. Wire up tab switching: Project Browser → click card → Project Detail

**This is the largest phase.** The terminal alone is complex (WebSocket multiplexing, tab management, auto-scroll). Recommend splitting into sub-agents:
- Agent A: Terminal widget (WebSocket + tabs + inject)
- Agent B: Task kanban widget (CRUD + column layout)
- Agent C: Constellation widget (SVG animation + click-to-focus)
- Agent D: ProjectDetailPanel shell (GridStack, top bar, dispatch, routing)

### Phase 3: Project Browser + Mission Control (Scenes app)

**Goal:** Landing page and project navigation.

1. Create `MissionControlPanel.tsx` — global canvas with project cards, agent activity feed, stats, quick dispatch
2. Create `ProjectBrowserPanel.tsx` — card grid with live agent indicators, "New Project" modal
3. Update `App.tsx` tabs: Mission Control | Projects | (Project Detail) | Widget Studio | Layout Studio
4. Project Detail is entered by clicking a project card (not a top-level tab)

### Phase 4: Widget Studio (Scenes app)

**Goal:** Browse and create widget types from within Grafana.

1. Create `WidgetStudioPanel.tsx` — fetch from `/api/widget-catalog`, card grid with previews
2. "Generate Widget" bar that POSTs to `/api/widget-catalog/generate`
3. Detail panel with full preview, schema view, "Add to Canvas" action
4. Port the existing `initTemplateCatalog` logic from `app.js` to React

### Phase 5: Layout Presets (backend + Scenes app)

**Goal:** Save/load widget arrangements.

1. Backend: `backend/services/layout_presets.py` — CRUD for preset JSON files in `~/.claude/canvas/presets/`
2. API endpoints: GET/POST/PUT/DELETE `/api/layout-presets`, POST `/api/canvas/{project}/apply-preset/{id}`
3. Create `LayoutStudioPanel.tsx` — preset list + preview + actions
4. "Save Layout" button on Project Detail top bar

### Phase 6: Polish + Integration

1. Unified theme across all panels (use `theme.ts` consistently)
2. Smooth transitions between screens (tab switch animations)
3. WebSocket events update all panels in real-time (agent spawned → constellation adds star, task completed → kanban moves card)
4. Loading states, error states, empty states — all with the space aesthetic
5. Keyboard shortcuts: Cmd+K for quick dispatch, Cmd+1-5 for tab switching

---

## UX Critique / Things to Get Right

**Don't make the user think about the system's internals.**
- "Dispatch Agent" is implementation language. The button should say "Run Task" or just have a text input that says "What do you want done?"
- The user shouldn't need to know about controllers, agents, sessions, MCP. They type a task, pick a project, and watch it happen.
- Agent session IDs are meaningless. Show star names, task descriptions, or "Agent 1/2/3".

**The terminal is the primary interaction channel.**
- If someone is watching an agent work, they're in the terminal. The inject input should be prominent — not hidden below a scroll.
- Consider: terminal at the TOP, kanban below. The thing you interact with most should be most accessible.
- Or: terminal takes 60% height, kanban + constellation share the remaining 40%.

**Project Detail should feel complete on first load.**
- No "loading..." skeleton screens that make you wait. Fetch canvas widgets, tasks, and agents in parallel. Render what you have immediately.
- If a project has no canvas widgets yet (new project), show the default layout preset automatically. Don't show an empty grid.

**Kill unnecessary clicks.**
- Don't require "select project" then "select agent" then "view output". Click project card → you're IN IT, terminal is streaming, kanban is showing tasks.
- Don't put actions behind modals. Dispatch is an inline input. Kill agent is a button on its tab. Status is always visible.

**The constellation is eye candy AND functional.**
- Click star → terminal switches to that agent's tab
- Star brightness = activity (streaming = bright, idle = dim)
- Connection lines between agents working on related tasks
- Shooting star animation when an agent completes

---

## Files to Create/Modify

### New Files (Scenes app)
- `src/components/MissionControlPanel.tsx`
- `src/components/ProjectBrowserPanel.tsx`
- `src/components/ProjectDetailPanel.tsx` (the big one)
- `src/components/WidgetStudioPanel.tsx`
- `src/components/LayoutStudioPanel.tsx`
- `src/scenes/missionControlScene.ts`
- `src/scenes/projectBrowserScene.ts`
- `src/scenes/projectDetailScene.ts`
- `src/scenes/widgetStudioScene.ts`
- `src/scenes/layoutStudioScene.ts`

### Modified Files (Scenes app)
- `src/components/App.tsx` — new tab definitions, project detail navigation state
- `src/services/api.ts` — add widget catalog, layout preset, canvas endpoints
- `src/services/websocket.ts` — (already good, no changes)
- `src/types.ts` — add WidgetType, LayoutPreset, CanvasWidget types

### New Files (Backend)
- `backend/services/layout_presets.py`

### Modified Files (Backend)
- `backend/main.py` — global controller, layout preset routes
- `backend/services/spawner.py` — global controller spawn/tracking
- `backend/mcp/controller_mcp_config.json` — update tools for global context

### Delete
- `src/components/TemplateBrowserPanel.tsx` — replaced by WidgetStudioPanel
- `src/scenes/templateScene.ts` — replaced by widgetStudioScene

---

## Open Questions

1. **Should Mission Control also be a canvas?** If yes, it gets the same widget system as Project Detail. The global controller could manage Mission Control widgets. This is elegant but adds complexity — the controller needs to know about "the global canvas" vs project canvases.

2. **Project Detail routing:** Since we can't use react-router-dom, clicking a project card needs to set state in App.tsx that shows ProjectDetailPanel with the project name. This means App.tsx manages `selectedProject` state and conditionally renders ProjectDetail instead of ProjectBrowser.

3. **GridStack in Grafana:** The main dashboard loads GridStack from a script tag. In the Grafana plugin, we'd need to load it from CDN via dynamic script injection or bundle it. It's ~40KB gzipped. Bundling is safer but increases plugin size.
