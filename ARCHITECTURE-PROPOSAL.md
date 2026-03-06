# Architecture Proposal: Unified Data Flow for Claude Manager

> Date: 2026-03-06
> Author: Architecture Agent (claude-opus-4-6)
> Status: PROPOSAL -- pending review

---

## 1. Executive Summary

Claude Manager has grown into an impressive agent orchestration platform with a rich canvas widget system, workflow engine, task dispatch, and real-time streaming. But as features accreted, the data flow developed multiple personality disorder. There are at least **four distinct paths** from agent subprocess to user's eyes (text streaming, milestones, canvas widgets via MCP, and task updates via tool interception). The backend maintains a hard controller/worker distinction that leaks into every layer -- from session creation to WS event routing to frontend rendering logic. The frontend's FeedController has become a 1250-line god object that manually wires six tab panels, two canvas engines, a subagent tree, and a task-agent mapping table.

The fix is not a rewrite. It is a **unification** of the data model. Every agent becomes the same object. Every piece of output flows through a single event pipeline. Every widget binds to a data source reactively. The frontend decomposes into self-contained components that subscribe to an event bus. We keep every feature that exists today -- canvas, constellation theme, GridStack, MCP tools, widget JS execution model, workflows, task dispatch -- but we eliminate the branching logic that makes each new feature require changes in five files.

This document provides the specific refactors needed, a migration path that can be executed incrementally across multiple PRs, and a risk assessment for each step.

---

## 2. Current State Analysis

### 2.1 Data Flow Diagram

```
                          ┌──────────────────────────────────────────────────┐
                          │            Agent Subprocess (claude CLI)          │
                          │  stdout: stream-json events (line-by-line)       │
                          └──────────────┬───────────────────────────────────┘
                                         │
                          ┌──────────────▼───────────────────────────────────┐
                          │         AgentSession._handle_stream_event        │
                          │  Parses: system.init, content_block_start/delta  │
                          │          content_block_stop, assistant, user,    │
                          │          message_start/stop, result              │
                          └──────────────┬───────────────────────────────────┘
                                         │
              ┌──────────────────────────┼────────────────────────────────┐
              │                          │                                │
    ┌─────────▼──────────┐   ┌──────────▼──────────┐    ┌───────────────▼────────────┐
    │ on_text_delta      │   │ on_tool_start/done  │    │ on_subagent_spawned/done   │
    │ → WS agent_stream  │   │ → WS tool_start/    │    │ → WS subagent_spawned/     │
    │                    │   │   tool_done,         │    │   subagent_done,           │
    │                    │   │   agent_milestone    │    │   subagent_tasks           │
    └─────────┬──────────┘   └──────────┬──────────┘    └───────────────┬────────────┘
              │                          │                                │
              │                          │                                │
    ┌─────────▼──────────────────────────▼────────────────────────────────▼──────────┐
    │                            AgentBroker Callbacks                                │
    │  _on_session_done also handles:                                                 │
    │    - Milestone capture (milestones_svc)                                         │
    │    - Workflow auto-continuation (workflows_svc.advance_phase)                   │
    │    - Task queue auto-dispatch (check_task_queue)                                │
    │    - Worker task completion (tasks_svc.update_task_status)                       │
    │    - DB persistence                                                             │
    └─────────┬──────────────────────────┬────────────────────────────────┬──────────┘
              │                          │                                │
    ┌─────────▼──────────┐   ┌──────────▼──────────┐    ┌───────────────▼────────────┐
    │ WSManager.broadcast│   │ WSManager.broadcast  │    │ WSManager.broadcast        │
    │ (18+ event types)  │   │                      │    │                            │
    └─────────┬──────────┘   └──────────┬──────────┘    └───────────────┬────────────┘
              │                          │                                │
    ┌─────────▼──────────────────────────▼────────────────────────────────▼──────────┐
    │                        Browser WebSocket Handler (app.js)                       │
    │  switch(msg.type) — 20+ cases, routes to:                                       │
    │    - state.agents array (manual push/filter/find)                               │
    │    - updateTileForProject / updateTileAgentStrip                                │
    │    - feed.handleEvent / feed.handleCanvasEvent / feed.handleTasksUpdated        │
    │    - feed.handleMilestonesUpdated / feed.handleWorkflowUpdated                  │
    └─────────┬──────────────────────────┬────────────────────────────────┬──────────┘
              │                          │                                │
    ┌─────────▼──────────┐   ┌──────────▼──────────┐    ┌───────────────▼────────────┐
    │ FeedController     │   │ FeedController       │    │ FeedController              │
    │ .handleEvent       │   │ .handleCanvasEvent   │    │ .handleTasksUpdated         │
    │ (13 case branches) │   │ (4 case branches)    │    │ .handleMilestonesUpdated    │
    │ → AgentSection     │   │ → CanvasEngine       │    │ .handleWorkflowUpdated      │
    │ → TasksPanel       │   │   .create/.update    │    │ → panel.updateXxx()         │
    │ → subagent map     │   │   /.remove           │    │                             │
    └────────────────────┘   └─────────────────────┘    └─────────────────────────────┘
```

### 2.2 Parallel Canvas/Widget Data Paths

```
Path A: MCP canvas_put (current production)
  Agent tool call → MCP server → HTTP POST /api/canvas/{project}/widgets/{id}
  → CanvasService.upsert_widget → JSON file write → WS broadcast canvas_widget_created
  → app.js → FeedController.handleCanvasEvent → CanvasEngine.create/update
  → WidgetFrame renders HTML/CSS/JS

Path B: Widget data buffer (POC 2, not merged)
  Agent tool call → MCP canvas_buffer_write → HTTP POST /api/canvas/{project}/buffer/{id}
  → widget_buffer.write → WS broadcast widget_data
  → app.js → window.__widgetData[id] = data → CustomEvent widget-data-update
  → Widget JS listens for event, updates DOM reactively

Path C: canvas_design (current production)
  Agent tool call → MCP canvas_design → HTTP POST /api/canvas/{project}/design
  → Backend spawns a design sub-agent (claude --print) to generate HTML/CSS/JS
  → Returns rendered widget → Agent calls canvas_put with result

Path D: Direct widget JSON file edits (BROKEN -- documented gotcha)
  Direct file edits to ~/.claude/canvas/*.json bypass in-memory CanvasService cache
  → Invisible until backend restart
```

### 2.3 Controller vs Worker Distinction: Where It Matters

The `is_controller` flag touches **11 distinct code paths**:

| Location | What changes | Why it exists |
|----------|-------------|---------------|
| `AgentBroker.create_session` | Controllers get MCP config | Controllers need orchestration tools |
| `AgentBroker._on_session_done` | Controllers stay in registry on idle | Persistent brain for follow-ups |
| `AgentBroker._on_session_done` | Controllers trigger workflow auto-continue | Only controllers run workflows |
| `AgentBroker._on_session_done` | Controllers trigger task queue dispatch | Only controllers manage task queue |
| `AgentBroker._on_session_done` | Workers mark their task done on idle | Workers are task-bound |
| `AgentBroker._on_session_done` | Skip milestone capture if subagent already captured | Avoid double-counting |
| `AgentSession.to_dict` | Exposes `is_controller` flag | Frontend renders differently |
| `FeedController.appendAgentSection` | Controllers auto-expand | UX: controller output is primary |
| `AgentSection._build` | Controllers get monitor bar HTML | Visual: radar sweep indicator |
| `AgentSection.updateStatusCard` | Controllers keep live stream, no summary swap | UX: controllers are persistent |
| `AgentSection.markDone` | Controllers on idle != "done" | Controllers persist across cycles |

**The core issue**: most of this logic exists because we conflated "persistent session with MCP tools" with "agent type". A controller is just an agent with (a) MCP tools enabled, (b) persistence across idle cycles, and (c) workflow awareness.

### 2.4 Complexity Hotspots

**1. `_on_session_done` in AgentBroker (lines 203-332)** -- 130 lines of nested conditionals handling milestone capture, workflow auto-continuation, task queue dispatch, worker completion, and DB persistence. Every new lifecycle hook adds another conditional branch.

**2. `FeedController.handleEvent` (lines 962-1104)** -- 13 case branches routing WS events to AgentSections, TasksPanel, subagent map, and controller monitoring. Every new event type requires a new branch here AND in app.js.

**3. `FeedController.setProject` (lines 61-158)** -- 100 lines imperatively creating 7 tab containers, 2 canvas engines, and loading initial data. Adding a new tab means touching this method plus `_bindTabEvents`.

**4. `AgentSession._handle_stream_event` (lines 371-567)** -- The subagent lifecycle tracking via `_pending_agent_tools` dict, with special interception of TodoWrite/TaskCreate events routed through `on_subagent_tasks`. This is where agent tool calls get interpreted as lifecycle events.

**5. Dual milestone systems** -- `session.milestones` (in-memory tool call labels) vs `milestones_svc.add_milestone` (persistent summaries). The WS event `agent_milestone` is emitted from `_on_tool_done` as a backward-compatibility wrapper around `tool_done`. Three things called "milestone" that serve different purposes.

### 2.5 Known Bugs and Fragilities

| Bug | Root Cause | Impact |
|-----|-----------|--------|
| querySelector collision on agent sections | Multiple sections query by `.agent-status-card` without scoping | Wrong section gets updated |
| Unmounted DOM nodes after project switch | FeedController clears innerHTML but sections Map still has references | Tool events route to detached DOM |
| Dead worktree CWDs | Workers spawned with CWD of a deleted git worktree | Agent subprocess fails to start |
| OAuth token expiry | Token refreshed on host by launchd but read at subprocess spawn time | Stale token causes "Not logged in" |
| readline buffer overflow | Large stream-json events exceed default 64KB buffer | Truncated JSON, parse errors, stuck sessions |
| Agent stream duplication on reconnect | `handleStateSync` + `agent_spawned` both try to create sections | Double agent cards in UI |

---

## 3. Proposed Architecture

### 3.1 Core Principle: Uniform Agent Model

**Every agent is the same data structure.** The controller/worker distinction becomes a set of **capabilities** rather than a type flag.

```python
# BEFORE (current)
class AgentSession:
    is_controller: bool = False        # hard type flag
    task_index: int | None = None      # worker-specific field

# AFTER (proposed)
class AgentSession:
    capabilities: set[str] = field(default_factory=set)
    # Possible capabilities:
    #   "mcp:canvas"      -- can use canvas tools
    #   "mcp:orchestrator" -- can use task/workflow tools
    #   "persistent"       -- stays in registry on idle
    #   "workflow"         -- triggers workflow auto-continuation
    #   "task_bound"       -- marks task_index done on completion
    task_index: int | None = None      # still here, but opt-in via capability
    mcp_config_path: str | None = None # still here, but driven by capabilities
```

**Migration**: Add `capabilities` field with a migration property that converts `is_controller=True` to `{"persistent", "mcp:orchestrator", "mcp:canvas", "workflow"}`. Deprecate `is_controller` but keep it as a computed property for backward compatibility.

The `_on_session_done` method decomposes into independent hooks keyed by capability:

```python
# BEFORE: 130 lines of nested if/elif
async def _on_session_done(self, session_id, reason):
    if session and session.is_controller and reason == "idle":
        # workflow logic...
    if session and session.is_controller and reason == "idle" and not workflow_injected:
        # task queue logic...
    if session and not session.is_controller and reason == "idle":
        # worker completion logic...

# AFTER: capability-driven hooks
_DONE_HOOKS = {
    "persistent": _hook_persist_on_idle,
    "workflow": _hook_workflow_auto_continue,
    "task_bound": _hook_mark_task_done,
}

async def _on_session_done(self, session_id, reason):
    session = self._sessions.get(session_id)
    if not session:
        return

    # Always: milestone capture, DB persist
    await self._capture_milestone(session, reason)
    await self._persist_to_db(session, reason)

    # Capability-driven hooks
    for cap, hook in self._DONE_HOOKS.items():
        if cap in session.capabilities:
            await hook(self, session, reason)

    # Always: check task queue if capacity opened up
    if reason == "idle":
        await self.check_task_queue(session.project_name)

    # Registry cleanup
    if reason != "idle" and "persistent" not in session.capabilities:
        self._sessions.pop(session_id, None)
```

### 3.2 Core Principle: Single Event Pipeline

**Every piece of data flows through a single typed event stream.** No more parallel paths for milestones, stream chunks, canvas events, and task updates.

```
┌──────────────────────────────────────────────────────────────┐
│                     Unified Event Bus                        │
│                                                              │
│  Every event has:                                            │
│    { type, project, session_id?, timestamp, data }           │
│                                                              │
│  Event types:                                                │
│    AGENT_LIFECYCLE: spawned, done, phase_change              │
│    AGENT_OUTPUT:    text_delta, tool_start, tool_done         │
│    PROJECT_DATA:    tasks_updated, milestones_updated,        │
│                     workflow_updated                          │
│    CANVAS:          widget_created, widget_updated,           │
│                     widget_removed, widget_data               │
│    SYSTEM:          stats_update, project_list, error         │
│                                                              │
│  Subscribers register by (type, project?) filter:            │
│    bus.on('AGENT_OUTPUT', projectName, handler)               │
│    bus.on('CANVAS', projectName, handler)                     │
│    bus.on('AGENT_LIFECYCLE', null, handler) // all projects   │
└──────────────────────────────────────────────────────────────┘
```

**Backend**: No changes to WSManager. The WS events already have type + data. The bus is a **frontend-only** abstraction.

**Frontend EventBus** (new file: `frontend/js/EventBus.js`):

```javascript
export class EventBus {
  constructor() {
    this._handlers = new Map(); // "type:project" -> Set<handler>
  }

  on(type, project, handler) {
    const key = project ? `${type}:${project}` : type;
    if (!this._handlers.has(key)) this._handlers.set(key, new Set());
    this._handlers.get(key).add(handler);
    // Return unsubscribe function
    return () => this._handlers.get(key)?.delete(handler);
  }

  emit(type, project, data) {
    // Fire project-specific handlers
    const specific = this._handlers.get(`${type}:${project}`);
    specific?.forEach(h => h(data));
    // Fire global handlers (no project filter)
    const global = this._handlers.get(type);
    global?.forEach(h => h(data));
  }
}

// Singleton
export const bus = new EventBus();
```

**WS handler** becomes a thin router:

```javascript
// BEFORE: 60+ line switch statement in app.js
function onWSMessage(msg) {
  switch (msg.type) {
    case 'agent_spawned': { /* 15 lines of state mutation + routing */ }
    case 'agent_stream': { /* route to feed */ }
    // ... 20 more cases
  }
}

// AFTER: type-to-category mapping + bus.emit
const EVENT_CATEGORIES = {
  agent_spawned: 'AGENT_LIFECYCLE',
  agent_done: 'AGENT_LIFECYCLE',
  session_phase: 'AGENT_LIFECYCLE',
  agent_stream: 'AGENT_OUTPUT',
  tool_start: 'AGENT_OUTPUT',
  tool_done: 'AGENT_OUTPUT',
  tasks_updated: 'PROJECT_DATA',
  milestones_updated: 'PROJECT_DATA',
  workflow_updated: 'PROJECT_DATA',
  canvas_widget_created: 'CANVAS',
  canvas_widget_updated: 'CANVAS',
  canvas_widget_removed: 'CANVAS',
  widget_data: 'CANVAS',
  // ...
};

function onWSMessage(msg) {
  const category = EVENT_CATEGORIES[msg.type] || 'SYSTEM';
  const project = msg.data?.project_name || msg.data?.project || null;

  // Still update global state.agents array (thin, no rendering)
  updateAgentState(msg);

  // Emit to bus -- all rendering logic lives in subscribers
  bus.emit(category, project, { type: msg.type, ...msg.data });
}
```

### 3.3 Core Principle: Widget = Data Binding

Merge POC 1 (capability discovery) and POC 2 (data buffer) into a single unified pattern:

```
Agent first interaction with canvas:
  1. Agent calls canvas_capabilities() → gets full manifest (POC 1)
  2. Agent calls canvas_put() → creates widget with HTML/CSS/JS scaffold
  3. Agent calls canvas_data() → pushes data updates (renamed from buffer_write)

Subsequent interactions:
  - Only canvas_data() — lightweight JSON write, WS broadcast, widget reacts

Widget JS receives data reactively:
  // Widget JS (bare function body)
  const widgetId = root.dataset.widgetId;

  // Subscribe to data updates
  document.addEventListener('widget-data-update', (e) => {
    if (e.detail.widget_id !== widgetId) return;
    renderWithData(e.detail.data);
  });

  // Initial data (if any was buffered before widget mounted)
  const initial = window.__widgetData?.[widgetId];
  if (initial) renderWithData(initial.data);

  function renderWithData(data) {
    // Widget-specific rendering logic
  }
```

**MCP tool simplification**:

| Current | Proposed | Change |
|---------|----------|--------|
| `canvas_put` | `canvas_put` | Keep as-is: creates/updates full widget (HTML/CSS/JS) |
| `canvas_buffer_write` (POC 2) | `canvas_data` | Rename + merge: lightweight data push, WS broadcast |
| `canvas_capabilities` (POC 1) | `canvas_capabilities` | Keep as-is: capability manifest on first call |
| `canvas_design` | `canvas_design` | Keep as-is: AI-generated widget |
| `canvas_templates` | `canvas_templates` | Keep as-is: template catalog |
| `canvas_list` | `canvas_list` | Keep as-is |
| `canvas_remove` | `canvas_remove` | Keep as-is |

The key insight: `canvas_put` is for **structure** (what the widget looks like), `canvas_data` is for **content** (what data it shows). First call sets up the scaffold; subsequent calls only push data.

### 3.4 Core Principle: Composable Frontend

Decompose FeedController into independent components that subscribe to the event bus.

```
BEFORE:
  FeedController (1250 lines)
    ├── Project header + dispatch composer
    ├── Canvas tab bar management
    ├── 2x CanvasEngine (system strip + dashboard grid)
    ├── Agent section lifecycle (create, hydrate, route events)
    ├── Subagent tree management
    ├── Task-agent mapping
    ├── 6x tab containers (overview, tasks, milestones, workflow, artifacts, cron)
    ├── Widget CRUD (add, delete, clear, template picker)
    └── Skills panel

AFTER:
  FeedController (200 lines) -- thin orchestrator
    ├── ProjectHeader (standalone component)
    │     Subscribes to: nothing (static render from project object)
    │
    ├── DispatchComposer (standalone component)
    │     Subscribes to: nothing (user input only)
    │
    ├── AgentFeed (standalone component)
    │     Subscribes to: AGENT_LIFECYCLE, AGENT_OUTPUT for current project
    │     Owns: AgentSection map, subagent tree
    │
    ├── CanvasDashboard (standalone component)
    │     Subscribes to: CANVAS for current project
    │     Owns: CanvasEngine, tab bar, widget CRUD
    │
    ├── TasksTab (standalone component)
    │     Subscribes to: PROJECT_DATA(tasks_updated), AGENT_OUTPUT for task routing
    │     Owns: TasksPanel
    │
    ├── MilestonesTab (standalone component)
    │     Subscribes to: PROJECT_DATA(milestones_updated)
    │     Owns: MilestonesPanel
    │
    ├── WorkflowTab (standalone component)
    │     Subscribes to: PROJECT_DATA(workflow_updated)
    │     Owns: WorkflowPanel
    │
    ├── ArtifactsTab (standalone component)
    │     Subscribes to: nothing (loads on demand)
    │     Owns: ArtifactsPanel
    │
    └── CronTab (standalone component)
          Subscribes to: nothing (loads on demand)
          Owns: CronPanel
```

**The FeedController becomes a tab router**:

```javascript
export class FeedController {
  constructor(container) {
    this._el = container;
    this._project = null;
    this._components = new Map(); // tab name -> component
    this._unsubs = [];            // event bus unsubscribe functions
  }

  setProject(project) {
    // Tear down old subscriptions
    this._unsubs.forEach(fn => fn());
    this._unsubs = [];
    this._project = project;
    this._el.innerHTML = '';

    // Mount components
    this._mountComponent('header', new ProjectHeader(project));
    this._mountComponent('dispatch', new DispatchComposer(project));
    this._mountComponent('tabs', new TabBar(['Overview','Tasks','Milestones','Workflow','Artifacts','Cron']));
    this._mountComponent('overview', new OverviewTab(project)); // contains AgentFeed + CanvasDashboard
    this._mountComponent('tasks', new TasksTab(project));
    // ... etc

    // Each component manages its own bus subscriptions internally
    // FeedController just mounts/unmounts them on tab switch
  }

  _showTab(name) {
    for (const [key, comp] of this._components) {
      if (key === name) comp.show();
      else if (comp.isTab) comp.hide();
    }
  }
}
```

### 3.5 Event Bus Design: Full Event Catalog

```
Category: AGENT_LIFECYCLE
  agent_spawned   { session_id, project_name, task, model, capabilities, task_index }
  agent_done      { session_id, project_name, reason }
  session_phase   { session_id, phase, session }
  turn_done       { session_id, turn_count }
  agent_state_sync { agents[] }

Category: AGENT_OUTPUT
  agent_stream    { session_id, chunk, done }
  tool_start      { session_id, tool: { tool_id, tool_name, tool_input, parent_tool_use_id? } }
  tool_done       { session_id, tool: { tool_id, tool_name, output, parent_tool_use_id? } }
  subagent_spawned { session_id, tool_use_id, description, subagent_type }
  subagent_done   { session_id, tool_use_id, result, is_error }
  subagent_tasks  { session_id, tool_use_id, todos[] }

Category: PROJECT_DATA
  tasks_updated       { project_name, tasks[] }
  milestones_updated  { project_name, milestones[] }
  workflow_updated    { project_name, workflow }
  project_list        { projects[] }
  project_update      { project }

Category: CANVAS
  canvas_widget_created { project, widget }
  canvas_widget_updated { project, widget_id, patch }
  canvas_widget_removed { project, widget_id }
  widget_data           { project, widget_id, data, version }

Category: SYSTEM
  stats_update    { total_projects, working_agents, ... }
  error           { message, code }
```

### 3.6 Agent Lifecycle: Simplified

```
1. SPAWN
   POST /api/projects/{name}/dispatch
   → broker.create_session(project, task, capabilities={"mcp:canvas"})
   → AgentSession.start(task)
   → WS: agent_spawned

2. STREAM
   claude subprocess stdout → stream-json parsing
   → text_delta: WS agent_stream
   → content_block_stop (tool): WS tool_start + tool_done
   → Phase transitions: starting → thinking → generating → tool_exec → ...

3. TOOL_CALL (uniform)
   Every tool call is just tool_start + tool_done.
   If tool_name == "Agent": also emit subagent_spawned/subagent_done
   (but this is DERIVED from tool events, not a separate path)

4. IDLE
   Process exits → session phase = IDLE
   → WS: agent_done { reason: "idle" }
   → Capability hooks fire:
     "persistent": session stays in registry
     "workflow": check for next phase
     "task_bound": mark task done
   → Task queue: always check for capacity

5. RESUME
   POST /api/agents/{id}/inject
   → claude --resume <session_id> <message>
   → Same stream loop restarts

6. CANCEL
   DELETE /api/agents/{id}
   → Process killed → WS: agent_done { reason: "cancelled" }
```

No controller/worker branching anywhere in this flow. The capabilities determine what happens at step 4.

### 3.7 Widget Data Model: Reactive Bindings

```
WidgetState (backend model, unchanged):
  id, project, title, html, css, js,
  template_id, template_data,
  gs_x, gs_y, gs_w, gs_h, tab,
  no_resize, no_move

WidgetDataEntry (new, from POC 2):
  widget_id, project, data: dict, version: int, updated_at

WidgetFrame (frontend, enhanced):
  - Stores widget_id in root.dataset.widgetId
  - On mount: checks window.__widgetData[widgetId] for buffered data
  - Listens to 'widget-data-update' CustomEvent for real-time updates
  - Widget JS receives (root, host, data?) where data is the latest buffered data

Agent workflow for dashboard:
  1. canvas_capabilities() → learn about templates, design tokens, CDN libs
  2. canvas_put(widget_id, title, html, css, js) → scaffold the widget
  3. canvas_data(widget_id, data) → push data (repeatable, fast, WS broadcast)
  4. Widget JS reacts to data updates automatically
```

### 3.8 MCP Simplification: Merged POC 1 + POC 2

The `controller_mcp_config.json` currently wires up both canvas and orchestrator MCP servers. Under the new model:

```json
{
  "mcpServers": {
    "canvas": {
      "type": "sse",
      "url": "http://mcp-canvas:4041/sse",
      "tools": [
        "canvas_capabilities",  // POC 1: capability manifest
        "canvas_put",           // create/update widget structure
        "canvas_data",          // POC 2: lightweight data push (renamed)
        "canvas_design",        // AI-generated widget
        "canvas_list",          // list current widgets
        "canvas_templates",     // template catalog
        "canvas_remove"         // delete widget
      ]
    },
    "orchestrator": {
      "type": "sse",
      "url": "http://mcp-orchestrator:4042/sse"
    }
  }
}
```

The `canvas_data` tool replaces `canvas_buffer_write` with a cleaner name and merges into the main canvas MCP server (no separate buffer service):

```python
@mcp.tool()
def canvas_data(project: str, widget_id: str, data: str) -> dict:
    """Push data to a widget for real-time display.

    Use this after creating a widget with canvas_put. The data is
    broadcast over WebSocket immediately -- the widget's JS code
    receives it via the 'widget-data-update' event.

    Much faster than canvas_put for frequent updates. canvas_put
    re-renders the entire widget; canvas_data only pushes new data
    to the existing widget JS.

    data: JSON string matching the widget's expected data schema.
    """
    parsed = json.loads(data) if isinstance(data, str) else data
    url = f"{CANVAS_API}/api/canvas/{project}/data/{widget_id}"
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, json={"data": parsed})
        resp.raise_for_status()
        return resp.json()
```

---

## 4. Migration Path

### Phase 1: Backend Capability Model (1 PR, low risk)

**Files changed**: `backend/broker/agent_session.py`, `backend/broker/agent_broker.py`, `backend/models.py`

1. Add `capabilities: set[str]` field to AgentSession with default `set()`.
2. Add computed property `is_controller` that returns `"persistent" in self.capabilities`.
3. In `AgentBroker.create_session`, map `is_controller=True` to capabilities `{"persistent", "mcp:orchestrator", "mcp:canvas", "workflow"}`.
4. Extract `_on_session_done` into capability hooks (private methods), called in a loop.
5. Keep `is_controller` in `to_dict()` output for frontend backward compat.

**Risk**: Low. All changes are additive. Existing behavior preserved via the migration property.

### Phase 2: Widget Data Buffer (1 PR, low risk)

**Files changed**: `backend/main.py`, `backend/mcp/canvas_server.py`, `backend/services/canvas.py`, `backend/models.py`, `frontend/js/app.js`

1. Merge POC 2 `widget_buffer` into `CanvasService` as `write_data`/`read_data` methods.
2. Add `POST /api/canvas/{project}/data/{widget_id}` endpoint.
3. Add `canvas_data` MCP tool to `canvas_server.py`.
4. Add `widget_data` WS event handling in `app.js` (from POC 2).
5. Merge POC 1 `canvas_capabilities` tool into `canvas_server.py`.
6. Inject `root.dataset.widgetId` in `WidgetFrame` so widget JS can identify itself.

**Risk**: Low. Additive endpoints. No existing behavior changed.

### Phase 3: Frontend EventBus (1 PR, medium risk)

**Files changed**: New `frontend/js/EventBus.js`, `frontend/js/app.js`

1. Create `EventBus` class with `on(type, project, handler)` and `emit(type, project, data)`.
2. In `app.js`, add `bus.emit()` calls alongside existing direct routing.
3. Do NOT remove existing routing yet -- both paths run in parallel.
4. Verify no regressions with both paths active.

**Risk**: Medium. Dual event paths could cause double-renders if subscribers overlap. Mitigate by having EventBus subscribers do nothing initially -- only activate in Phase 4.

### Phase 4: FeedController Decomposition (2-3 PRs, high risk)

**Files changed**: `frontend/js/feed/FeedController.js`, new component files

This is the largest change. Break it into sub-steps:

**4a**: Extract `CanvasDashboard` component.
- Move all canvas tab bar logic, dashboard widget grid, system strip, widget CRUD into `CanvasDashboard.js`.
- FeedController creates `CanvasDashboard` and passes it the project.
- CanvasDashboard subscribes to `CANVAS` events on the bus.

**4b**: Extract `AgentFeed` component.
- Move AgentSection management, subagent map, section mounting/hydration into `AgentFeed.js`.
- AgentFeed subscribes to `AGENT_LIFECYCLE` and `AGENT_OUTPUT` on the bus.
- FeedController stops receiving `handleEvent` calls -- `app.js` routes to bus only.

**4c**: Convert remaining tabs to bus subscribers.
- TasksTab subscribes to `PROJECT_DATA` for `tasks_updated`.
- MilestonesTab subscribes to `PROJECT_DATA` for `milestones_updated`.
- WorkflowTab subscribes to `PROJECT_DATA` for `workflow_updated`.

**4d**: Slim down FeedController to tab router only.
- Remove `handleEvent`, `handleCanvasEvent`, `handleTasksUpdated`, `handleMilestonesUpdated`, `handleWorkflowUpdated`.
- Remove direct EventBus-bypassing calls from `app.js`.

**Risk**: High. FeedController is load-bearing. Each sub-step must be deployable independently with full regression testing. The key risk is DOM ordering -- components need to mount in the right order within `#feed`.

### Phase 5: Cleanup (1 PR, low risk)

1. Remove deprecated `is_controller` parameter from `create_session` (use `capabilities` only).
2. Remove backward-compat `agent_milestone` WS event (unused by frontend after Phase 4b).
3. Remove dual event paths from Phase 3 (EventBus is now the only path).
4. Update CLAUDE.md memory files.

---

## 5. What We Keep

**Everything that works stays.** Specifically:

| Feature | Status |
|---------|--------|
| Canvas widget system (GridStack, WidgetFrame, `new Function` execution) | **Keep as-is** |
| MCP tool architecture (canvas_server, orchestrator_server, FastMCP) | **Keep, add tools** |
| Widget JS execution model (`new Function('root','host', code)`) | **Keep as-is** |
| Constellation theme, void aesthetic, particle effects | **Keep as-is** |
| Widget catalog + template system | **Keep as-is** |
| Workflow engine (phases, templates, roles, auto-continuation) | **Keep as-is** |
| Task queue auto-dispatch with parallelism limits | **Keep as-is** |
| Agent stream parsing (stream-json, partial messages) | **Keep as-is** |
| AgentSection UI (phase timeline, tool blocks, markdown rendering) | **Keep as-is** |
| Docker-compose topology (backend, mcp-canvas, mcp-orchestrator) | **Keep as-is** |
| OAuth token extraction via `scripts/start.sh` | **Keep as-is** |
| `gpush` workflow and Jenkins CI/CD | **Keep as-is** |
| Skills system (global, per-project toggles) | **Keep as-is** |
| Artifacts panel (file tree, preview, git status) | **Keep as-is** |
| Cron panel | **Keep as-is** |
| Context menus on widgets (copy, paste, save as template) | **Keep as-is** |

---

## 6. What Changes

| Change | What | Why |
|--------|------|-----|
| Agent model | `is_controller: bool` becomes `capabilities: set[str]` | Eliminates type branching in 11 code paths |
| Session done handler | Monolithic method becomes capability-driven hook chain | Each concern is isolated and testable |
| MCP tools | Add `canvas_capabilities` + `canvas_data` to canvas server | Merge POC 1 + POC 2 into production |
| WS routing (frontend) | Direct method calls become EventBus pub/sub | Components decouple from FeedController |
| FeedController | 1250-line god object becomes 200-line tab router | Each tab/feature is a self-contained component |
| Canvas dashboard | Embedded in FeedController becomes `CanvasDashboard` component | Can be reused, tested independently |
| Agent feed | Embedded in FeedController becomes `AgentFeed` component | Owns its own section map and subagent tree |
| Widget data flow | canvas_put only → canvas_put (structure) + canvas_data (content) | Separates scaffold from data, enables reactive widgets |

---

## 7. Risk Assessment

### High Risk

**FeedController decomposition (Phase 4)** -- This is the riskiest change because FeedController is deeply coupled to the DOM structure of `#feed`. The tab bar, dispatch composer, skills panel, and agent container all share a parent element with specific CSS layout rules. Extracting components requires careful attention to DOM ordering and CSS specificity.

**Mitigation**: Each sub-step (4a-4d) is a separate PR. Deploy each one, run the full app for a day, then proceed. If any step breaks, revert just that PR.

### Medium Risk

**EventBus dual-path period (Phase 3-4)** -- During the transition, events flow through both the old direct-call path and the new EventBus. If a component subscribes to the bus AND still receives direct calls, it will process events twice.

**Mitigation**: Phase 3 adds EventBus.emit() without subscribers. Phase 4 adds subscribers AND removes direct calls in the same PR (per component).

**Agent capability migration** -- Existing running agents won't have capabilities set. When the backend restarts, all in-memory sessions are lost anyway (subprocess cleanup), so this is actually fine -- new agents get capabilities from day one.

### Low Risk

**Widget data buffer (Phase 2)** -- Purely additive. New endpoints, new MCP tool, new WS event type. Nothing existing changes.

**canvas_capabilities (Phase 2)** -- Purely additive. Agents can call it or not.

**Cleanup (Phase 5)** -- By this point everything uses the new paths. Removing old code is straightforward.

### Risks That Are NOT Addressed

This proposal does NOT fix:
- **OAuth token expiry** -- still requires launchd + file mount. A proper fix would be a token refresh endpoint that the backend calls proactively.
- **readline buffer overflow** -- already mitigated by 1MB limit in `create_subprocess_exec`, but a streaming JSON parser would be more robust.
- **Dead worktree CWDs** -- needs worktree lifecycle management (cleanup after workflow phase ends).
- **Backend persistence** -- sessions are still in-memory. A Redis or SQLite session store would survive restarts.

---

## Appendix A: File-Level Change Map

```
PHASE 1 (Backend Capabilities):
  M backend/models.py                  -- add AgentCapability enum
  M backend/broker/agent_session.py    -- add capabilities field
  M backend/broker/agent_broker.py     -- capability-driven hooks

PHASE 2 (Widget Data Buffer):
  M backend/main.py                    -- add /api/canvas/{p}/data/{id} endpoint
  M backend/mcp/canvas_server.py       -- add canvas_data + canvas_capabilities tools
  M backend/services/canvas.py         -- add write_data/read_data methods
  M backend/models.py                  -- add WSMessageType.WIDGET_DATA
  M frontend/js/app.js                 -- add widget_data WS handler
  M frontend/js/canvas/WidgetFrame.js  -- inject widgetId, dispatch data events

PHASE 3 (EventBus):
  A frontend/js/EventBus.js            -- new EventBus class
  M frontend/js/app.js                 -- add bus.emit() calls

PHASE 4a (CanvasDashboard extraction):
  A frontend/js/feed/CanvasDashboard.js
  M frontend/js/feed/FeedController.js -- delegate canvas to CanvasDashboard

PHASE 4b (AgentFeed extraction):
  A frontend/js/feed/AgentFeed.js
  M frontend/js/feed/FeedController.js -- delegate agents to AgentFeed
  M frontend/js/app.js                 -- remove feed.handleEvent, use bus

PHASE 4c (Tab subscribers):
  M frontend/js/feed/TasksPanel.js     -- subscribe to bus
  M frontend/js/feed/MilestonesPanel.js -- subscribe to bus
  M frontend/js/feed/WorkflowPanel.js  -- subscribe to bus

PHASE 4d (Slim FeedController):
  M frontend/js/feed/FeedController.js -- remove all handle* methods
  M frontend/js/app.js                 -- remove all feed.handle* calls

PHASE 5 (Cleanup):
  M backend/broker/agent_broker.py     -- remove is_controller parameter
  M backend/broker/agent_session.py    -- remove is_controller field
  M backend/models.py                  -- remove backward-compat
  M frontend/js/app.js                 -- remove dual event paths
```

## Appendix B: Glossary

| Term | Definition |
|------|-----------|
| **Capability** | A string tag on an agent session that enables specific lifecycle hooks (e.g., "persistent", "workflow", "task_bound") |
| **EventBus** | Frontend pub/sub system that decouples WS event producers from UI component consumers |
| **Widget scaffold** | The HTML/CSS/JS structure of a widget, created by `canvas_put` |
| **Widget data** | The dynamic content pushed to a widget via `canvas_data`, received by widget JS via CustomEvent |
| **Capability hook** | A function registered for a capability string, called at specific lifecycle points (e.g., session done) |
| **Tab component** | A self-contained frontend module that manages its own DOM subtree and event subscriptions |

---

*End of proposal. Ready for review and incremental execution.*
