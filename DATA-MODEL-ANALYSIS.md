# Data Model Analysis: Claude Agent Manager

> Date: 2026-03-06
> Author: Data Architecture Agent (claude-opus-4-6)
> Status: RESEARCH COMPLETE

---

## 1. Current Data Model Inventory

### 1.1 Entity Map

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          ENTITY RELATIONSHIP MAP                         │
│                                                                          │
│  ManagedProject ─────────┬──── AgentSession (1:N, in-memory)            │
│    name (PK)             │       session_id (PK)                         │
│    path                  │       project_name (FK)                       │
│    description           │       model, task, phase                      │
│    goal                  │       milestones[], output_buffer[]           │
│    config: ProjectConfig │       callbacks (9 function refs)             │
│    active_session_ids[]  │       _proc (subprocess handle)               │
│                          │                                               │
│                          ├──── Task (1:N, TASKS.md file)                 │
│                          │       index (positional PK)                   │
│                          │       text, status, indent                    │
│                          │                                               │
│                          ├──── Milestone (1:N, JSON file)                │
│                          │       id (UUID PK)                            │
│                          │       session_id (FK → AgentSession)          │
│                          │       task, summary, agent_type               │
│                          │       model, duration_seconds                 │
│                          │                                               │
│                          ├──── WidgetState (1:N, JSON file + memory)     │
│                          │       id (UUID PK)                            │
│                          │       project (FK)                            │
│                          │       title, html, css, js, tab               │
│                          │       template_id, template_data              │
│                          │       gs_x/y/w/h, no_resize, no_move         │
│                          │                                               │
│                          ├──── Workflow (0:1, JSON file)                 │
│                          │       id (UUID PK)                            │
│                          │       project_name (FK)                       │
│                          │       template_id (FK → WorkflowTemplate)     │
│                          │       team: TeamRole[]                        │
│                          │       config: WorkflowConfig                  │
│                          │       phases: WorkflowPhase[]                 │
│                          │       isolation: IsolationInfo[]              │
│                          │                                               │
│                          ├──── CronJob (0:N, JSON file)                  │
│                          │       id (UUID PK)                            │
│                          │       name, schedule, task                    │
│                          │       enabled, last_run, next_run             │
│                          │                                               │
│                          └──── Artifacts (0:N, filesystem scan)          │
│                                  path (computed)                         │
│                                  name, type, size, mtime                 │
│                                                                          │
│  WorkflowTemplate ──── RolePreset (1:N, embedded)                       │
│    id (PK)                  role, label, persona                         │
│    name, description        expertise[], is_worker                       │
│    phases: PhaseDefinition[]                                             │
│    config_schema             ConfigField (type, label, default)          │
│    isolation_strategy                                                    │
│                                                                          │
│  RolePreset (custom) ──── ~/.claude/roles.json                          │
│    role (PK), label, persona, expertise[], builtin                      │
│                                                                          │
│  SkillInfo ──── filesystem scan (~/.claude/skills/)                      │
│    name (PK), description, source, path, enabled                        │
│                                                                          │
│  Session (DB) ──── PostgreSQL (optional, fire-and-forget)               │
│    session_id (PK)                                                       │
│    project_name, task, model, status, phase                             │
│    started_at, ended_at, turn_count                                     │
│    input_tokens, output_tokens, milestones (JSONB)                      │
│                                                                          │
│  Message (DB) ──── PostgreSQL (optional, unused in reads)               │
│    uuid (PK), session_id (FK), role, content (JSONB)                    │
│    timestamp, parent_uuid, cwd                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Storage Mechanisms

| Entity | Primary Store | Format | Location |
|--------|--------------|--------|----------|
| **ManagedProject** | Filesystem scan | Directory + PROJECT.md + manager.json | `~/git/claude-managed-projects/{name}/` |
| **AgentSession** | In-memory dict | Python dataclass | `AgentBroker._sessions` |
| **Task** | Markdown file | Checkbox list (regex-parsed) | `{project}/TASKS.md` |
| **Milestone** | JSON file | Array of objects | `{project}/.claude/milestones.json` |
| **WidgetState** | JSON file + in-memory cache | Array of objects | `~/.claude/canvas/{project}.json` |
| **Workflow** | JSON file | Single object | `{project}/.claude/workflow.json` |
| **CronJob** | JSON file | Array of objects | `~/.claude/cron/{project}/jobs.json` |
| **WorkflowTemplate** | JSON files | One per template | `backend/templates/*.json` + `~/.claude/workflow-templates/*.json` |
| **RolePreset (custom)** | JSON file | Array of objects | `~/.claude/roles.json` |
| **SkillInfo** | Filesystem scan | Directory symlinks + frontmatter | `~/.claude/skills/` |
| **Session (historical)** | PostgreSQL (optional) | Relational rows | `sessions` table |
| **Message (historical)** | PostgreSQL (optional) | Relational rows | `messages` table |
| **CLI Session Log** | JSONL file (Claude CLI) | Line-delimited JSON | `~/.claude/projects/{encoded-path}/{session_id}.jsonl` |
| **Artifacts** | Filesystem scan (read-only) | Raw files | `{project}/**` |

### 1.3 Thirteen Distinct Storage Backends

The current system uses **thirteen** different storage backends simultaneously:

1. Python in-memory dict (`AgentBroker._sessions`)
2. Python in-memory dict (`CanvasService._widgets`)
3. Python in-memory dict (`_task_cache` in main.py)
4. Python in-memory dict (`templates._cache`)
5. Markdown file with regex parsing (TASKS.md)
6. JSON file per project (milestones.json)
7. JSON file per project (canvas/{project}.json)
8. JSON file per project (workflow.json)
9. JSON file per project (cron/jobs.json)
10. JSON file global (roles.json)
11. JSON files per template (templates/*.json)
12. JSONL files from Claude CLI (session logs)
13. PostgreSQL (optional, write-only in practice)

---

## 2. Access Pattern Analysis

### 2.1 AgentSession

```
WRITE PATTERNS:
  - Creation:     1-10 per minute (burst on workflow execution phases)
  - Phase change: 5-50 per minute per agent (rapid during tool cycles)
  - Text delta:   100-500 per minute per agent (token streaming)
  - Tool events:  2-20 per minute per agent
  - Milestone:    1-5 per minute per agent (subset of tool events)
  - Injection:    0-1 per hour (manual user input or workflow auto-continue)

READ PATTERNS:
  - Dashboard:    Every 2s (stats computation) — reads ALL sessions
  - Project view: Every 5s (project refresh) — reads sessions per project
  - Agent card:   Continuous (WS stream consumers, no polling)
  - Messages:     On-demand (click agent → read JSONL from disk)
  - State sync:   On WS connect (sends full agent list)

LIFECYCLE:
  Created → phases cycle (starting→thinking→generating→tool_input→tool_exec→...)
  → IDLE (process exits) → optionally RESUME (inject message) → IDLE → ...
  → CANCELLED or ERROR (terminal states, removed from registry unless persistent)

REAL-TIME: Yes — phase changes and text deltas push to all WS clients
HISTORICAL: Partial — PostgreSQL saves session metadata, CLI saves JSONL
```

**Key tension:** The session is both a live process handle AND a data record. The live process has callbacks, subprocess refs, and input buffers that are inherently transient. The data record (task, milestones, turn count, model) should survive restarts but currently does not (in-memory only; PostgreSQL is write-only — never read back).

### 2.2 Task

```
WRITE PATTERNS:
  - Manual add:        0-5 per hour (user clicks "Add Task")
  - Agent writes:      5-20 per workflow phase (controller plans tasks)
  - Status change:     1-10 per hour (agent completes work, broker marks done)
  - Indirect writes:   Agent edits TASKS.md via Bash/Write tools (bypasses API)

READ PATTERNS:
  - Poll:              Every 3s for active projects (task_poll_task)
  - API:               GET /api/projects/{name}/tasks (on tab open)
  - Auto-dispatch:     check_task_queue reads tasks to find pending items
  - Agent reads:       Agent reads TASKS.md file directly (bypasses API)

LIFECYCLE:
  Created (added to TASKS.md) → pending → in_progress → done
  No archival — done tasks stay in TASKS.md forever

REAL-TIME: Polled every 3s, broadcast on change
HISTORICAL: No — TASKS.md is the only record, and done tasks accumulate
```

**Key pain point:** Tasks are stored as markdown checkboxes, addressed by positional index. This means:
- Re-ordering tasks changes all indices (breaks in-progress task references)
- No stable ID per task — `task_index` is fragile
- Concurrent writes from multiple agents risk file corruption
- No metadata (assignee, priority, dependencies, created_at) beyond the text

### 2.3 WidgetState

```
WRITE PATTERNS:
  - Creation:    1-5 per project setup (agent designs dashboard)
  - Update:      0-10 per minute (agent pushes data, user moves widgets)
  - Layout save: On user drag-drop (saves gs_x/y/w/h for all widgets)
  - Delete:      Rare (manual cleanup)

READ PATTERNS:
  - Page load:   GET /api/canvas/{project}/widgets (all widgets for project)
  - Tab switch:  GET with ?tab= filter
  - WS state sync: Full widget list sent on connect

LIFECYCLE:
  Created → updated (data/layout changes) → deleted
  No archival — widgets persist until manually removed

REAL-TIME: Yes — creation/update/delete broadcast via WS
HISTORICAL: No — only current state, no version history
```

**Key pain point:** Dual storage (in-memory cache + JSON file) with the cache as the source of truth. Direct file edits are invisible. The cache has no TTL or invalidation mechanism.

### 2.4 Workflow

```
WRITE PATTERNS:
  - Creation:       Once per project (user creates workflow)
  - Phase advance:  1 per phase completion (auto-continue after agent idle)
  - Status change:  Pause/resume (manual user action)
  - Isolation mgmt: Create/cleanup worktrees or subdirectories on phase transitions

READ PATTERNS:
  - API:           GET /api/projects/{name}/workflow (on tab open)
  - Phase prompt:  Read on phase advance (build prompt for controller)
  - WS broadcast:  After every phase change

LIFECYCLE:
  DRAFT → RUNNING → phases cycle → COMPLETE (or PAUSED → RUNNING)
  Persisted indefinitely as workflow.json

REAL-TIME: Yes — workflow_updated broadcast on every state change
HISTORICAL: Partial — completed phases have timestamps and summaries
```

### 2.5 Milestone

```
WRITE PATTERNS:
  - Append-only:  1 per agent work cycle completion (idle transition)
  - Subagent:     1 per subagent completion (controller captures)
  - Never updated after creation

READ PATTERNS:
  - API:          GET /api/projects/{name}/milestones (on tab open)
  - WS broadcast: After every milestone capture
  - Sorted:       Always returned newest-first

LIFECYCLE:
  Created → persists forever (append-only log)
  Can be manually deleted or cleared

REAL-TIME: Yes — milestones_updated broadcast
HISTORICAL: Yes — this IS the historical record (append-only)
```

**Key insight:** Milestones are the closest thing to an event log in the current system. They're append-only, timestamped, and linked to sessions. But they only capture completion events, not the full lifecycle.

### 2.6 CronJob

```
WRITE PATTERNS:
  - CRUD:        Rare (user manages scheduled tasks)
  - Mark run:    Per schedule (30s tick loop checks due jobs)
  - Recalculate: next_run updated on every read

READ PATTERNS:
  - Scheduler:   Every 30s (scan all projects for due jobs)
  - API:         GET /api/projects/{name}/cron (on tab open)

LIFECYCLE:
  Created → runs on schedule → persists indefinitely
  Can be disabled without deletion

REAL-TIME: No — no WS broadcast for cron events
HISTORICAL: Minimal — only last_run and run_count
```

### 2.7 Summary: Access Pattern Matrix

```
┌──────────────────┬────────────┬────────────┬──────────┬──────────────┬────────────┐
│ Entity           │ Write Freq │ Read Freq  │ Realtime │ Historical   │ Store Type │
├──────────────────┼────────────┼────────────┼──────────┼──────────────┼────────────┤
│ AgentSession     │ VERY HIGH  │ HIGH       │ YES      │ PARTIAL      │ Memory     │
│ Text Delta       │ EXTREME    │ EXTREME    │ YES      │ NO           │ Transient  │
│ Tool Event       │ HIGH       │ MEDIUM     │ YES      │ NO           │ Transient  │
│ Task             │ MEDIUM     │ MEDIUM     │ POLLED   │ NO           │ Markdown   │
│ Milestone        │ LOW        │ LOW        │ YES      │ YES          │ JSON file  │
│ WidgetState      │ MEDIUM     │ LOW        │ YES      │ NO           │ JSON+Mem   │
│ Workflow         │ LOW        │ LOW        │ YES      │ PARTIAL      │ JSON file  │
│ CronJob          │ LOW        │ LOW        │ NO       │ MINIMAL      │ JSON file  │
│ Project          │ RARE       │ MEDIUM     │ POLLED   │ NO           │ Filesystem │
│ Template         │ RARE       │ LOW        │ NO       │ NO           │ JSON file  │
│ Role             │ RARE       │ LOW        │ NO       │ NO           │ JSON file  │
│ Artifact         │ N/A        │ ON-DEMAND  │ NO       │ NO           │ Filesystem │
│ Session (DB)     │ LOW        │ NEVER      │ NO       │ INTENDED     │ PostgreSQL │
│ CLI Session Log  │ N/A        │ ON-DEMAND  │ NO       │ YES          │ JSONL      │
└──────────────────┴────────────┴────────────┴──────────┴──────────────┴────────────┘
```

---

## 3. Pain Points in Current Data Model

### 3.1 No Stable Task Identity

Tasks are identified by positional index in TASKS.md. When an agent rewrites the file (adding, removing, or reordering tasks), all indices shift. The `task_index` stored on `AgentSession` becomes stale. This causes:
- Worker agents completing the wrong task
- Task status updates going to the wrong line
- Race conditions when multiple agents edit TASKS.md concurrently

**Fix needed:** Tasks need a stable UUID, stored in a structured format (not parsed markdown).

### 3.2 Split Brain: In-Memory vs. Disk

Three entities have dual storage with inconsistent synchronization:

| Entity | Memory (truth) | Disk (backup) | Sync Direction |
|--------|---------------|---------------|----------------|
| AgentSession | `_sessions` dict | PostgreSQL (optional) | Memory → Disk (fire-and-forget) |
| WidgetState | `_widgets` dict | canvas JSON | Bidirectional (load on start, save on write) |
| Task cache | `_task_cache` dict | TASKS.md | Disk → Memory (poll every 3s) |

On backend restart, all agent sessions are lost. Widget state survives (JSON reload). Tasks survive (markdown file). This inconsistency means a restart kills all agents but preserves their dashboards and task lists — a confusing state.

### 3.3 PostgreSQL: Write-Only Dead End

The optional PostgreSQL layer (`database.py`) saves sessions and messages but is **never read** in any operational code path. The `list_sessions` and `get_session` methods exist but are unused. This means:
- Historical session data accumulates but is inaccessible
- Message content is saved but never retrieved
- No session history view in the UI
- Token usage metrics (input_tokens, output_tokens) are written but never read

### 3.4 Dual Milestone Systems

There are two things called "milestones" with different semantics:

1. **`session.milestones`** — In-memory list of tool call labels (e.g., "Read . main.py", "Bash . git status"). Capped at 20 items. Transient — lost on restart. Used for the real-time tool activity feed in agent cards.

2. **`milestones_svc` milestones** — Persistent JSON file. One entry per agent work cycle completion. Contains full-text summary. Used for the Milestones tab.

The WS event `agent_milestone` is emitted from `_on_tool_done` as a backward-compatibility wrapper, adding a third "milestone" concept. This naming collision creates confusion.

### 3.5 No Event History

The system generates a rich stream of events (text deltas, tool starts/stops, phase changes, agent spawns/completions) but none are persisted in a queryable format. The only historical records are:
- CLI JSONL session logs (proprietary format, not indexed)
- Milestone summaries (one per work cycle, no granularity)
- PostgreSQL session records (metadata only, never read)

There is no way to answer: "What tools did agent X use?", "How long did agent X spend in the thinking phase?", "What was the token throughput last hour?"

### 3.6 Fragile Cascade: _on_session_done

The `_on_session_done` callback in AgentBroker is a 130-line method that handles:
1. Milestone capture
2. Workflow auto-continuation
3. Task queue auto-dispatch
4. Worker task completion
5. DB persistence

This is effectively a multi-entity transaction with no atomicity guarantees. If milestone capture fails, the workflow still advances. If task status update fails, the next task still dispatches. The error handling is try/except with print statements.

### 3.7 No Cross-Entity Relationships

There is no explicit relationship between:
- A task and the agent working on it (only `task_index` on session, no reverse lookup)
- A widget and the agent that created it
- A milestone and the task it completed
- A workflow phase and the agents that ran during it

These relationships are implicit (shared project_name) or tracked only transiently (task_index on session).

---

## 4. Data Model Approaches for Agent Orchestration

### 4.1 Event Sourcing

**Concept:** Every state change is an immutable event appended to an ordered log. Current state is derived by replaying events.

**Fit for claude-manager:**

```
AgentEvent Stream:
  t=0   AgentSpawned      { session_id, project, task, model }
  t=1   PhaseChanged       { session_id, from: starting, to: thinking }
  t=2   PhaseChanged       { session_id, from: thinking, to: generating }
  t=3   TextDelta          { session_id, chunk: "Let me..." }
  t=4   TextDelta          { session_id, chunk: " analyze..." }
  t=5   PhaseChanged       { session_id, from: generating, to: tool_input }
  t=6   ToolStarted        { session_id, tool: "Read", input: {path: "main.py"} }
  t=7   ToolCompleted      { session_id, tool: "Read", duration_ms: 42 }
  t=8   SubAgentSpawned    { session_id, child_id, task: "Implement feature X" }
  t=9   SubAgentCompleted  { session_id, child_id, result: "Done..." }
  t=10  TurnCompleted      { session_id, turn: 1 }
  t=11  AgentIdle          { session_id }
  t=12  TaskCompleted      { session_id, task_index: 3 }
  t=13  MilestoneCaptured  { session_id, summary: "Implemented..." }
```

**Advantages:**
- Natural fit for the streaming architecture — events are already being emitted
- Full audit trail — can replay any agent's full lifecycle
- Time-travel debugging — reconstruct state at any point
- Metrics derivable from event stream (token throughput, tool frequency, phase durations)
- Current state = fold over events (but see disadvantages)

**Disadvantages:**
- Text deltas are extremely high-volume (100-500/min/agent) — storing all of them is expensive
- State reconstruction from full event replay is O(n) — needs snapshotting
- Not all current "state" maps cleanly to events (widget layout, for example)
- Adds complexity for simple CRUD entities (CronJob, Template, Role)

**Verdict:** Event sourcing is an excellent fit for **agent lifecycle** (the hot path) but overkill for **configuration entities** (templates, roles, cron jobs). A hybrid approach is ideal: event-source agent activity, use document store for configuration.

### 4.2 CQRS (Command Query Responsibility Segregation)

**Concept:** Separate the write model (commands that change state) from the read model (queries that return views).

**Fit for claude-manager:**

```
WRITE SIDE (Command Model):
  - AgentSession state machine (in-memory, process-bound)
  - Event log (append-only, durable)

READ SIDE (Query Models — materialized views):
  ┌─────────────────────────────────────────────────────┐
  │ Dashboard View                                       │
  │   project_name, agent_count, working_count,          │
  │   idle_count, active_tasks, recent_milestones        │
  │   Refreshed: on every agent lifecycle event          │
  └─────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────┐
  │ Agent Feed View                                      │
  │   session_id, phase, last_text, milestones[10],     │
  │   active_tool, subagent_count                        │
  │   Refreshed: on every agent output event             │
  └─────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────┐
  │ Task Board View                                      │
  │   task_id, text, status, assigned_agent,             │
  │   started_at, completed_at                           │
  │   Refreshed: on task status change events            │
  └─────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────┐
  │ Metrics View (time-series)                           │
  │   tokens_per_minute, tools_per_hour,                 │
  │   phase_duration_avg, agent_lifetime_avg             │
  │   Refreshed: on every event (aggregated)             │
  └─────────────────────────────────────────────────────┘
```

**Advantages:**
- Widget data buffer (from ARCHITECTURE-PROPOSAL.md POC 2) is literally a CQRS read-side projection
- Dashboard view can be optimized independently of the write path
- Different read views can have different refresh rates
- Naturally maps to the existing WS broadcast pattern (broadcasts are read-side updates)

**Disadvantages:**
- Adds eventual consistency complexity (read model lags behind write model)
- More moving parts — each view needs a projection function
- For the current scale (1-10 agents, 1 user), the overhead may not be justified

**Verdict:** CQRS is the right conceptual model for the system's architecture, but a full implementation with separate databases for read/write sides is premature. The **practical** version: keep the write side as events, and materialize read views in-memory (which is essentially what the current system already does, just without the formal separation).

### 4.3 Actor Model

**Concept:** Each agent session is an independent actor with its own state, communicating via message passing.

**Current mapping:**

```
┌─────────────────────────────────────────────────────────┐
│ AgentBroker (Supervisor)                                 │
│   ├── AgentSession actor (project-a, agent-1)           │
│   │     state: {phase, milestones, buffer, proc}        │
│   │     receives: Start, Inject, Cancel                  │
│   │     emits: TextDelta, ToolStart, ToolDone, Idle      │
│   ├── AgentSession actor (project-a, agent-2)           │
│   │     ...                                              │
│   └── AgentSession actor (project-b, agent-1)           │
│         ...                                              │
└─────────────────────────────────────────────────────────┘
```

**The current system is already close to an actor model.** AgentSession is essentially an actor:
- Has private state (phase, milestones, output_buffer, subprocess handle)
- Communicates via callbacks (which are message sends to the broker)
- Has a mailbox (pending_injection queue)
- Has a lifecycle (start → work → idle → inject → work → ...)

**What's missing:**
- No supervision tree (if an agent crashes, there's no automatic restart policy)
- No back-pressure (text deltas flood the WS broadcast with no throttling)
- No typed message protocol (callbacks use `dict[str, Any]` everywhere)

**Language affinity:**
- **Elixir**: Perfect fit — GenServer, Supervisor, Registry are exactly this pattern
- **Go**: Good fit — goroutines + channels per agent session
- **Rust**: Good fit — tokio tasks + mpsc channels
- **Python**: Acceptable — asyncio tasks + queues (current approach, but no supervision)
- **TypeScript**: Acceptable — similar to Python, but Worker threads add true parallelism

### 4.4 Document Store

**Concept:** Store each entity as a self-contained JSON document with embedded sub-documents.

**Fit for claude-manager:**

```json
// Project Document (root aggregate)
{
  "name": "agent-reports",
  "path": "/Users/.../agent-reports",
  "description": "Standalone reporting app",
  "config": {
    "parallelism": 2,
    "model": "claude-opus-4-6",
    "dashboard_prompt": "Show agent activity"
  },
  "workflow": {
    "id": "wf-123",
    "template_id": "software-engineering",
    "status": "running",
    "current_phase_index": 3,
    "phases": [/* embedded */],
    "team": [/* embedded */],
    "isolation": [/* embedded */]
  },
  "tasks": [
    {"id": "task-abc", "text": "Implement API", "status": "done", "assigned_agent": "sess-456"},
    {"id": "task-def", "text": "Write tests", "status": "in_progress", "assigned_agent": "sess-789"}
  ],
  "widgets": [
    {"id": "w-111", "title": "Kanban", "html": "...", "gs_x": 0, "gs_y": 0}
  ],
  "cron_jobs": [/* embedded */]
}
```

**Advantages:**
- Simple — one document per project, all related data co-located
- Natural JSON serialization (matches current JSON file approach)
- Good for read patterns that load "everything for a project"
- No joins needed

**Disadvantages:**
- Cross-project queries are expensive (e.g., "all active agents across all projects")
- Document size grows unbounded (milestones accumulate, widgets have large HTML/CSS/JS)
- Concurrent writes to the same document need conflict resolution
- Agent sessions don't fit well — they're transient + have process handles

**Verdict:** Document store is a good fit for **project configuration** (config, workflow, tasks, cron) but not for **runtime state** (agent sessions, event streams) or **large blobs** (widget HTML/CSS/JS).

### 4.5 Graph Model

**Concept:** Entities as nodes, relationships as edges.

```
(Project:agent-reports) ──HAS_WORKFLOW──> (Workflow:wf-123)
                         ──HAS_TASK──> (Task:task-abc)
                         ──HAS_WIDGET──> (Widget:w-111)

(AgentSession:sess-456) ──WORKS_ON──> (Task:task-abc)
                         ──BELONGS_TO──> (Project:agent-reports)
                         ──SPAWNED_BY──> (AgentSession:sess-000)  // parent controller

(Task:task-abc) ──DEPENDS_ON──> (Task:task-xyz)
                ──BLOCKED_BY──> (Task:task-pqr)

(Widget:w-111) ──DISPLAYS_DATA_FROM──> (AgentSession:sess-456)
               ──USES_TEMPLATE──> (WidgetTemplate:task-kanban)
```

**Advantages:**
- Makes implicit relationships explicit and traversable
- Natural for task dependency trees (TASKS.md indent hierarchy)
- Enables queries like "what agents contributed to this project" or "what tasks block this one"
- Good for the constellation visualization (agents as stars, connections as edges)

**Disadvantages:**
- Overkill for most access patterns (we rarely traverse relationships)
- Graph databases add operational complexity
- The number of entities is small (10-50 per project) — an in-memory adjacency list suffices

**Verdict:** Graph relationships should be modeled as fields on entities (parent_id, assigned_agent_id, depends_on[]) rather than a separate graph database. The constellation visualization can derive its graph from these fields at render time.

### 4.6 Time-Series

**Concept:** Store metrics as timestamped data points for trend analysis.

```
Series: agent.token_rate
  { timestamp, project, session_id, tokens_per_second: 42 }
  { timestamp, project, session_id, tokens_per_second: 38 }

Series: agent.phase_duration
  { timestamp, project, session_id, phase: "thinking", duration_ms: 2300 }
  { timestamp, project, session_id, phase: "tool_exec", duration_ms: 450 }

Series: project.active_agents
  { timestamp, project, count: 4 }
  { timestamp, project, count: 3 }

Series: system.memory_usage
  { timestamp, rss_bytes: 1073741824 }
```

**Advantages:**
- Enables dashboards with sparklines, heatmaps, gauges
- Natural for monitoring agent health and performance
- Downsampling for long-term storage (1s resolution for 1h, 1m resolution for 24h)

**Disadvantages:**
- Requires a time-series database or ring buffer implementation
- Most time-series data can be derived from events (no need to store separately)

**Verdict:** Time-series metrics should be **derived from the event stream** rather than stored as a separate data path. An in-memory ring buffer per metric (last N minutes) is sufficient for real-time dashboards. For historical metrics, aggregate from the event store.

### 4.7 Recommended Hybrid Approach

```
┌─────────────────────────────────────────────────────────────────────┐
│                    HYBRID DATA ARCHITECTURE                          │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ LAYER 1: Event Store (hot path)                              │    │
│  │   Agent lifecycle events — append-only, time-ordered         │    │
│  │   Store: SQLite WAL (local) or PostgreSQL (k8s)              │    │
│  │   Retention: 7 days full resolution, 90 days aggregated      │    │
│  │   Access: Real-time streaming + historical queries            │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ LAYER 2: Document Store (warm path)                          │    │
│  │   Project config, workflows, tasks, cron jobs, roles         │    │
│  │   Store: JSON files (current) or SQLite tables               │    │
│  │   Access: CRUD with infrequent writes                        │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ LAYER 3: Blob Store (cold path)                              │    │
│  │   Widget HTML/CSS/JS, templates, CLI session logs            │    │
│  │   Store: Filesystem (current)                                │    │
│  │   Access: On-demand reads, rare writes                       │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ LAYER 4: In-Memory (transient)                               │    │
│  │   Agent process handles, text streaming buffers,             │    │
│  │   WS connection pool, materialized views                     │    │
│  │   Store: Process memory (hashmap/dict)                       │    │
│  │   Access: Real-time only, lost on restart                    │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Proposed Data Model

### 5.1 Core Entities

#### Project

```
Project {
  name:           string        (PK, directory name)
  path:           string        (absolute filesystem path)
  description:    string?       (from PROJECT.md first line)
  goal:           string?       (full PROJECT.md content)
  parallelism:    int           (max concurrent worker agents)
  model:          string?       (default model for agents)
  mcp_config:     string?       (path to MCP config JSON)
  dashboard_prompt: string?     (user's dashboard intent)
  created_at:     timestamp
  updated_at:     timestamp
}

Storage: Filesystem scan (PROJECT.md + manager.json) — unchanged
         Optionally: SQLite `projects` table for fast queries
```

#### AgentSession

```
AgentSession {
  session_id:     uuid          (PK, manager-assigned)
  cli_session_id: string?       (Claude CLI session ID, arrives via system.init)
  project_name:   string        (FK → Project)
  project_path:   string
  task:           string        (dispatch prompt)
  model:          string        (e.g., "claude-opus-4-6")
  capabilities:   set<string>   (replaces is_controller)
                                  "persistent" | "mcp:canvas" | "mcp:orchestrator" |
                                  "workflow" | "task_bound"
  task_id:        uuid?         (FK → Task, replaces task_index)
  phase:          SessionPhase  (enum: starting|thinking|generating|tool_input|
                                       tool_exec|idle|injecting|cancelled|error)
  turn_count:     int
  started_at:     timestamp
  ended_at:       timestamp?

  // Transient (in-memory only, not persisted):
  process:        ProcessHandle
  callbacks:      CallbackSet
  pending_injection: string?
  output_buffer:  Message[]
  tool_labels:    string[20]    (ring buffer of recent tool call labels)
}

Storage: In-memory (runtime) + Event Store (lifecycle events)
         On restart: sessions are gone, but event history persists
```

#### AgentEvent (the unified event stream)

```
AgentEvent {
  event_id:       uuid          (PK)
  session_id:     uuid          (FK → AgentSession)
  project_name:   string        (FK → Project, denormalized for query efficiency)
  timestamp:      timestamp     (microsecond precision)
  event_type:     EventType     (enum)
  payload:        EventPayload  (tagged union / sum type)
}

EventType enum:
  // Lifecycle
  AGENT_SPAWNED
  AGENT_IDLE
  AGENT_CANCELLED
  AGENT_ERROR
  PHASE_CHANGED
  TURN_COMPLETED

  // Output
  TEXT_DELTA             // NOT stored long-term (stream-only)
  TOOL_STARTED
  TOOL_COMPLETED

  // Hierarchy
  SUBAGENT_SPAWNED
  SUBAGENT_COMPLETED

  // Cross-entity
  TASK_STATUS_CHANGED
  MILESTONE_CAPTURED
  WIDGET_UPDATED

EventPayload (tagged union):
  AgentSpawned     { task, model, capabilities, task_id? }
  AgentIdle        { }
  AgentCancelled   { }
  AgentError       { message }
  PhaseChanged     { from_phase, to_phase }
  TurnCompleted    { turn_number }
  ToolStarted      { tool_id, tool_name, tool_input }
  ToolCompleted    { tool_id, tool_name, duration_ms, output_preview? }
  SubAgentSpawned  { child_tool_use_id, description }
  SubAgentCompleted { child_tool_use_id, result_preview, is_error }
  TaskStatusChanged { task_id, from_status, to_status }
  MilestoneCaptured { milestone_id, summary_preview }
  WidgetUpdated    { widget_id, update_type: "data"|"layout"|"structure" }

Storage: SQLite table with indexes on (session_id, timestamp) and (project_name, timestamp)
         TEXT_DELTA events are stream-only (WS broadcast, not persisted)
         Retention: 7 days at full resolution, then aggregate to daily summaries
```

#### Task

```
Task {
  id:             uuid          (PK — stable, survives reordering)
  project_name:   string        (FK → Project)
  text:           string        (task description)
  status:         TaskStatus    (enum: pending|in_progress|done|blocked|cancelled)
  priority:       int           (sort order, 0 = highest)
  indent:         int           (hierarchy depth, 0 = top-level)
  parent_id:      uuid?         (FK → Task, for subtask hierarchy)
  assigned_agent: uuid?         (FK → AgentSession)
  created_at:     timestamp
  started_at:     timestamp?
  completed_at:   timestamp?
  tags:           string[]      (e.g., ["sprint-1", "@engineer-1"])
}

Storage: SQLite table (replaces TASKS.md)
         Backward compat: export to TASKS.md format for agents to read
         Import: parse existing TASKS.md on first load, assign UUIDs
```

#### Widget

```
Widget {
  id:             uuid          (PK)
  project_name:   string        (FK → Project)
  title:          string
  tab:            string        (canvas tab, default "main")

  // Structure (set via canvas_put, infrequent updates)
  html:           text
  css:            text
  js:             text

  // Template binding (optional)
  template_id:    string?       (FK → WidgetTemplate)
  template_data:  json?         (raw data for template rendering)

  // Layout (set via drag-drop)
  gs_x:           int?
  gs_y:           int?
  gs_w:           int           (default 4)
  gs_h:           int           (default 3)
  no_resize:      bool
  no_move:        bool

  // Data buffer (set via canvas_data, frequent updates)
  data:           json?         (latest data pushed by agent)
  data_version:   int           (monotonic counter)

  created_at:     timestamp
  updated_at:     timestamp
}

Storage: SQLite table + in-memory cache
         Data buffer updates go to memory first, persisted periodically
         Structure updates (html/css/js) persisted immediately
```

#### Milestone

```
Milestone {
  id:             uuid          (PK)
  project_name:   string        (FK → Project)
  session_id:     uuid          (FK → AgentSession)
  task_id:        uuid?         (FK → Task)
  task_text:      string        (denormalized, for display after task is gone)
  summary:        text          (agent's completion summary, capped at 5KB)
  agent_type:     string        (standalone|controller|subagent)
  model:          string?
  duration_seconds: float?
  timestamp:      timestamp

  // Derived from events (optional enrichment):
  tool_count:     int?          (total tools used in this work cycle)
  files_changed:  string[]?     (files touched during this cycle)
}

Storage: SQLite table (replaces milestones.json)
         Append-only, no updates
         Indexed on (project_name, timestamp DESC)
```

#### Workflow

```
Workflow {
  id:             uuid          (PK)
  project_name:   string        (FK → Project, unique constraint)
  template_id:    string        (FK → WorkflowTemplate)
  status:         WorkflowStatus (draft|running|paused|complete)

  team:           TeamRole[]    (embedded array)
  config:         json          (template-specific configuration values)

  phases:         WorkflowPhase[] (embedded array)
  current_phase:  int           (index into phases)

  isolation:      IsolationInfo[] (embedded array)

  created_at:     timestamp
  started_at:     timestamp?
  completed_at:   timestamp?
}

// Embedded types (unchanged from current model):
TeamRole       { role, count, instructions }
WorkflowPhase  { phase_id, phase_label, iteration_number, status, started_at, completed_at, summary }
IsolationInfo  { role, instance, branch, path, status, strategy }

Storage: SQLite row with JSON columns for embedded arrays
         Or: JSON file (current approach, sufficient for 0-1 workflow per project)
```

#### CronJob

```
CronJob {
  id:             uuid          (PK)
  project_name:   string        (FK → Project)
  name:           string
  schedule:       string        (cron expression)
  task:           string        (dispatch prompt)
  enabled:        bool
  last_run:       timestamp?
  next_run:       timestamp?
  run_count:      int
  created_at:     timestamp
}

Storage: SQLite table (replaces per-project JSON files)
         Enables cross-project queries (get_all_enabled_jobs)
```

### 5.2 Storage Strategy Summary

```
┌──────────────────┬─────────────────┬──────────────────┬──────────────────────┐
│ Entity           │ Primary Store   │ Cache Layer      │ Real-time Delivery   │
├──────────────────┼─────────────────┼──────────────────┼──────────────────────┤
│ AgentSession     │ In-memory       │ N/A              │ WS broadcast         │
│ AgentEvent       │ SQLite/PG       │ Ring buffer(100) │ WS broadcast         │
│ Task             │ SQLite          │ In-memory map    │ WS on change         │
│ Widget           │ SQLite          │ In-memory map    │ WS on change         │
│ Widget data buf  │ In-memory       │ N/A              │ WS broadcast         │
│ Milestone        │ SQLite          │ None             │ WS on append         │
│ Workflow         │ JSON file       │ None             │ WS on change         │
│ CronJob          │ SQLite          │ None             │ None                 │
│ Project          │ Filesystem      │ In-memory(5s)    │ WS poll broadcast    │
│ Template         │ JSON files      │ In-memory        │ None                 │
│ Role             │ JSON file       │ None             │ None                 │
│ Artifact         │ Filesystem      │ None             │ None                 │
│ TEXT_DELTA       │ NOT STORED      │ N/A              │ WS broadcast (live)  │
└──────────────────┴─────────────────┴──────────────────┴──────────────────────┘
```

### 5.3 Unified Event Schema

```
// The canonical event that flows through the entire system.
// Generated by AgentSession, persisted to event store, broadcast via WS.

AgentEvent {
  // Header (always present)
  event_id:       string        // UUID v7 (time-sortable)
  session_id:     string        // Which agent
  project_name:   string        // Which project (denormalized)
  timestamp:      string        // ISO 8601 with microseconds
  event_type:     string        // Discriminator for payload

  // Payload (type-specific, one of):

  // --- Lifecycle events ---

  AgentSpawned {
    task:           string
    model:          string
    capabilities:   string[]
    task_id:        string?     // If task-bound
  }

  PhaseChanged {
    from_phase:     string      // SessionPhase enum value
    to_phase:       string
  }

  TurnCompleted {
    turn_number:    int
  }

  AgentIdle { }

  AgentCancelled { }

  AgentError {
    message:        string
    recoverable:    bool
  }

  // --- Output events ---

  TextDelta {
    chunk:          string      // NOT persisted to event store
  }

  ToolStarted {
    tool_id:        string
    tool_name:      string
    tool_input:     object      // Truncated to 1KB for storage
    milestone_label: string     // e.g., "Read . main.py"
  }

  ToolCompleted {
    tool_id:        string
    tool_name:      string
    duration_ms:    int
    output_preview: string?     // First 500 chars of output
  }

  // --- Hierarchy events ---

  SubAgentSpawned {
    tool_use_id:    string      // Parent tool call ID
    description:    string      // Agent tool description input
  }

  SubAgentCompleted {
    tool_use_id:    string
    result_preview: string      // First 1KB of result
    is_error:       bool
  }

  // --- Cross-entity events ---

  TaskStatusChanged {
    task_id:        string
    from_status:    string
    to_status:      string
  }

  MilestoneCaptured {
    milestone_id:   string
    summary_preview: string     // First 200 chars
  }

  WidgetUpdated {
    widget_id:      string
    update_type:    string      // "data" | "layout" | "structure"
  }
}
```

### 5.4 SQLite Schema (Recommended Primary Store)

```sql
-- Event store: the core of the new data model
CREATE TABLE agent_events (
    event_id        TEXT PRIMARY KEY,       -- UUID v7
    session_id      TEXT NOT NULL,
    project_name    TEXT NOT NULL,
    timestamp       TEXT NOT NULL,          -- ISO 8601
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL,          -- JSON

    -- Indexes for common access patterns
    CONSTRAINT fk_session FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX idx_events_session     ON agent_events(session_id, timestamp);
CREATE INDEX idx_events_project     ON agent_events(project_name, timestamp);
CREATE INDEX idx_events_type        ON agent_events(event_type, timestamp);

-- Sessions: metadata for each agent (survives restart)
CREATE TABLE sessions (
    session_id      TEXT PRIMARY KEY,
    project_name    TEXT NOT NULL,
    project_path    TEXT NOT NULL,
    task            TEXT,
    model           TEXT,
    capabilities    TEXT NOT NULL DEFAULT '[]',   -- JSON array
    task_id         TEXT,                         -- FK to tasks
    status          TEXT NOT NULL DEFAULT 'starting',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    turn_count      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_sessions_project   ON sessions(project_name);

-- Tasks: structured task storage (replaces TASKS.md parsing)
CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,       -- UUID
    project_name    TEXT NOT NULL,
    text            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    priority        INTEGER NOT NULL DEFAULT 0,
    indent          INTEGER NOT NULL DEFAULT 0,
    parent_id       TEXT,                   -- Self-referencing FK
    assigned_agent  TEXT,                   -- FK to sessions
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    tags            TEXT NOT NULL DEFAULT '[]'   -- JSON array
);

CREATE INDEX idx_tasks_project      ON tasks(project_name, priority);
CREATE INDEX idx_tasks_status       ON tasks(project_name, status);

-- Widgets: canvas widgets with data buffer
CREATE TABLE widgets (
    id              TEXT PRIMARY KEY,
    project_name    TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    tab             TEXT NOT NULL DEFAULT 'main',
    html            TEXT NOT NULL DEFAULT '',
    css             TEXT NOT NULL DEFAULT '',
    js              TEXT NOT NULL DEFAULT '',
    template_id     TEXT,
    template_data   TEXT,                   -- JSON
    gs_x            INTEGER,
    gs_y            INTEGER,
    gs_w            INTEGER NOT NULL DEFAULT 4,
    gs_h            INTEGER NOT NULL DEFAULT 3,
    no_resize       INTEGER NOT NULL DEFAULT 0,
    no_move         INTEGER NOT NULL DEFAULT 0,
    data            TEXT,                   -- JSON (latest data buffer)
    data_version    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX idx_widgets_project    ON widgets(project_name, tab);

-- Milestones: append-only completion records
CREATE TABLE milestones (
    id              TEXT PRIMARY KEY,
    project_name    TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    task_id         TEXT,
    task_text       TEXT NOT NULL,
    summary         TEXT NOT NULL,
    agent_type      TEXT NOT NULL DEFAULT 'standalone',
    model           TEXT,
    duration_seconds REAL,
    tool_count      INTEGER,
    timestamp       TEXT NOT NULL
);

CREATE INDEX idx_milestones_project ON milestones(project_name, timestamp DESC);

-- Cron jobs: scheduled tasks
CREATE TABLE cron_jobs (
    id              TEXT PRIMARY KEY,
    project_name    TEXT NOT NULL,
    name            TEXT NOT NULL,
    schedule        TEXT NOT NULL,
    task            TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run        TEXT,
    next_run        TEXT,
    run_count       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX idx_cron_project       ON cron_jobs(project_name);
CREATE INDEX idx_cron_enabled       ON cron_jobs(enabled, next_run);
```

### 5.5 Migration Path

```
PHASE 1: Add Task IDs (backward compatible)
  - Add UUID generation when parsing TASKS.md (hash of text + position as seed)
  - Store task_id on AgentSession instead of task_index
  - Keep TASKS.md as source of truth, but add stable IDs in comments:
    - [ ] Implement API  <!-- id:abc-123 -->
  - Agents can still edit TASKS.md normally; IDs are preserved on parse

PHASE 2: Add Event Store (additive)
  - Create SQLite database at ~/.claude/manager.db
  - Emit AgentEvent objects alongside existing WS broadcasts
  - Persist lifecycle events (spawned, idle, cancelled, error)
  - Persist tool events (started, completed)
  - Do NOT persist text deltas (too high volume)
  - Historical queries become possible

PHASE 3: Migrate Tasks to SQLite (parallel)
  - Create tasks table, import from TASKS.md
  - API reads from SQLite, falls back to TASKS.md
  - Keep TASKS.md export for agent readability
  - Remove positional index dependency

PHASE 4: Migrate Widgets to SQLite (parallel)
  - Create widgets table, import from canvas JSON files
  - CanvasService reads from SQLite, cache in memory
  - Remove dual-store problem (single SQLite source of truth)

PHASE 5: Migrate Milestones to SQLite
  - Create milestones table, import from JSON files
  - Enrich with event-derived data (tool_count, files_changed)

PHASE 6: Migrate CronJobs to SQLite
  - Create cron_jobs table, import from JSON files
  - Enables efficient cross-project queries

PHASE 7: Capability Model
  - Add capabilities field to AgentSession
  - Map is_controller to capability set
  - Decompose _on_session_done into capability hooks
  - (Per ARCHITECTURE-PROPOSAL.md Phase 1)

PHASE 8: Unified Milestone System
  - Rename session.milestones to session.tool_labels (what they actually are)
  - Remove agent_milestone WS event (backward-compat wrapper)
  - Milestones = only the persistent completion records
```

---

## 6. Language-Specific Data Model Capabilities

### 6.1 Type System Expressiveness

The proposed data model relies heavily on **tagged unions** (AgentEvent with type-specific payloads) and **enum state machines** (SessionPhase, TaskStatus). How well does each language express these?

#### Python (current)

```python
# Tagged union via Pydantic discriminated unions
from pydantic import BaseModel, Field
from typing import Literal, Annotated, Union

class AgentSpawned(BaseModel):
    event_type: Literal["agent_spawned"] = "agent_spawned"
    task: str
    model: str
    capabilities: list[str]

class ToolStarted(BaseModel):
    event_type: Literal["tool_started"] = "tool_started"
    tool_id: str
    tool_name: str
    tool_input: dict

EventPayload = Annotated[
    Union[AgentSpawned, ToolStarted, ...],
    Field(discriminator="event_type")
]

class AgentEvent(BaseModel):
    event_id: str
    session_id: str
    timestamp: str
    payload: EventPayload
```

**Assessment:** Pydantic discriminated unions work but are verbose. No exhaustive pattern matching (match/case in 3.10+ is syntactic sugar, not enforced). Runtime type checking only. **Score: 6/10**

#### Go

```go
// Tagged union via interface + type switch
type EventPayload interface {
    eventType() string
}

type AgentSpawned struct {
    Task         string   `json:"task"`
    Model        string   `json:"model"`
    Capabilities []string `json:"capabilities"`
}
func (e AgentSpawned) eventType() string { return "agent_spawned" }

type ToolStarted struct {
    ToolID    string         `json:"tool_id"`
    ToolName  string         `json:"tool_name"`
    ToolInput map[string]any `json:"tool_input"`
}
func (e ToolStarted) eventType() string { return "tool_started" }

type AgentEvent struct {
    EventID     string       `json:"event_id"`
    SessionID   string       `json:"session_id"`
    Timestamp   time.Time    `json:"timestamp"`
    EventType   string       `json:"event_type"`
    Payload     EventPayload `json:"-"` // custom marshal
}

// Type switch for exhaustive handling
func handleEvent(e AgentEvent) {
    switch p := e.Payload.(type) {
    case AgentSpawned:
        // ...
    case ToolStarted:
        // ...
    }
}
```

**Assessment:** Interface-based unions work but lack exhaustiveness checking. JSON serialization of interfaces requires custom marshalers. No generics for typed event streams until Go 1.18, and even then, limited. **Score: 5/10**

#### Rust

```rust
use serde::{Serialize, Deserialize};
use chrono::{DateTime, Utc};

#[derive(Serialize, Deserialize, Debug)]
#[serde(tag = "event_type", content = "payload")]
enum EventPayload {
    #[serde(rename = "agent_spawned")]
    AgentSpawned {
        task: String,
        model: String,
        capabilities: Vec<String>,
    },
    #[serde(rename = "tool_started")]
    ToolStarted {
        tool_id: String,
        tool_name: String,
        tool_input: serde_json::Value,
    },
    // Compiler enforces: every match must handle all variants
}

#[derive(Serialize, Deserialize, Debug)]
struct AgentEvent {
    event_id: String,
    session_id: String,
    timestamp: DateTime<Utc>,
    #[serde(flatten)]
    payload: EventPayload,
}

// Exhaustive pattern matching — compiler error if a variant is missing
fn handle_event(event: &AgentEvent) {
    match &event.payload {
        EventPayload::AgentSpawned { task, model, capabilities } => { /* ... */ }
        EventPayload::ToolStarted { tool_id, tool_name, tool_input } => { /* ... */ }
    }
}
```

**Assessment:** Rust enums with `serde` are the gold standard for tagged unions. Compiler-enforced exhaustive matching prevents missing event handlers. `serde` handles JSON/MessagePack/CBOR with zero-cost abstractions. Ownership model prevents data races in shared state. **Score: 10/10**

#### TypeScript

```typescript
// Discriminated unions are first-class
interface AgentSpawned {
    event_type: "agent_spawned";
    task: string;
    model: string;
    capabilities: string[];
}

interface ToolStarted {
    event_type: "tool_started";
    tool_id: string;
    tool_name: string;
    tool_input: Record<string, unknown>;
}

type EventPayload = AgentSpawned | ToolStarted;

interface AgentEvent {
    event_id: string;
    session_id: string;
    timestamp: string;
    payload: EventPayload;
}

// Exhaustive check via never type
function handleEvent(event: AgentEvent): void {
    const payload = event.payload;
    switch (payload.event_type) {
        case "agent_spawned":
            // TypeScript narrows type here
            break;
        case "tool_started":
            break;
        default:
            const _exhaustive: never = payload; // Compiler error if case missed
    }
}
```

**Assessment:** TypeScript discriminated unions are excellent — lightweight, compiler-enforced narrowing, works naturally with JSON. Zod adds runtime validation. **Score: 9/10**

#### Elixir

```elixir
defmodule AgentEvent do
  @type payload ::
    {:agent_spawned, %{task: String.t(), model: String.t(), capabilities: [String.t()]}}
    | {:tool_started, %{tool_id: String.t(), tool_name: String.t(), tool_input: map()}}

  @type t :: %__MODULE__{
    event_id: String.t(),
    session_id: String.t(),
    timestamp: DateTime.t(),
    payload: payload()
  }

  defstruct [:event_id, :session_id, :timestamp, :payload]
end

# Pattern matching is Elixir's superpower
def handle_event(%AgentEvent{payload: {:agent_spawned, %{task: task, model: model}}}) do
  # ...
end

def handle_event(%AgentEvent{payload: {:tool_started, %{tool_name: name}}}) do
  # ...
end
```

**Assessment:** Elixir's pattern matching is natural for event handling. Tagged tuples are idiomatic. Ecto schemas provide database mapping. No compile-time exhaustiveness checking (runtime MatchError instead). Jason for JSON is fast. **Score: 8/10**

### 6.2 Serialization Capabilities

| Language | JSON | Protobuf | MessagePack | Custom Binary |
|----------|------|----------|-------------|---------------|
| **Python** | Pydantic `.model_dump_json()` — good, 10-50 MB/s | protobuf/betterproto — adequate | msgpack-python — adequate | struct — manual |
| **Go** | `encoding/json` — good, 50-100 MB/s; sonic for perf | protoc-gen-go — excellent | msgpack — good | `encoding/binary` — good |
| **Rust** | serde_json — excellent, 200+ MB/s | prost — excellent | rmp-serde — excellent | serde with custom format — trivial |
| **TypeScript** | `JSON.parse/stringify` — good, native | protobufjs — adequate | @msgpack/msgpack — good | DataView — manual |
| **Elixir** | Jason — good, 50-80 MB/s | protobuf-elixir — adequate | Msgpax — good | binary pattern matching — excellent |

**Key insight for our use case:** The hot path is JSON serialization of WS messages (hundreds per second per agent). Rust's serde_json is 4-10x faster than Python's json/Pydantic. For the TEXT_DELTA event path (highest volume), this matters.

### 6.3 Concurrency Primitives for Data Models

| Concern | Python | Go | Rust | TypeScript | Elixir |
|---------|--------|-----|------|------------|--------|
| **Agent session isolation** | asyncio.Task (cooperative) | goroutine (preemptive) | tokio::task (cooperative) | Worker thread | GenServer process |
| **Shared state (session registry)** | dict + no lock needed (GIL) | sync.RWMutex + map | Arc<RwLock<HashMap>> | Map (single-threaded) | ETS table or Agent |
| **Event broadcast** | loop over WS list | fan-out via channels | tokio::broadcast channel | EventEmitter | Phoenix.PubSub |
| **Stream processing** | async for + yield | channel + select | Stream trait + combinators | AsyncIterator | GenStage pipeline |
| **Back-pressure** | None (current) | Buffered channels | Bounded channels | None | GenStage demand |

**Key insight:** The current Python system has **no back-pressure** on the text delta path. A fast agent can flood the WS broadcast faster than clients consume. Go channels and Rust bounded channels naturally provide back-pressure. Elixir's GenStage is purpose-built for this.

### 6.4 Database Drivers and ORMs

| Language | SQLite | PostgreSQL | Redis | Event Store Libs |
|----------|--------|------------|-------|-----------------|
| **Python** | aiosqlite — good | asyncpg — excellent | aioredis — good | None mature |
| **Go** | mattn/go-sqlite3 — excellent | pgx — excellent | go-redis — excellent | EventStore client — basic |
| **Rust** | rusqlite/sqlx — excellent | sqlx/tokio-postgres — excellent | redis-rs — excellent | None mature (build with sqlx) |
| **TypeScript** | better-sqlite3 — excellent | pg/postgres.js — good | ioredis — excellent | None mature |
| **Elixir** | Ecto.SQLite3 — good | Ecto — excellent (best ORM) | Redix — good | EventStore client — mature |

**Key insight:** For the proposed SQLite-based event store, **Rust's sqlx** and **Go's go-sqlite3** offer the best performance. Elixir's Ecto provides the best developer experience for schema management. Python's aiosqlite adds async overhead over the already-slow sqlite3 module.

### 6.5 Stream Processing

The agent event stream needs to be:
1. Generated (parse subprocess stdout)
2. Persisted (write to event store)
3. Broadcast (push to WS clients)
4. Aggregated (compute metrics)

```
Subprocess stdout
    │
    ▼
Parse stream-json ──────────────────────────────┐
    │                                            │
    ├── Persist to event store ──► SQLite        │
    │                                            │
    ├── Broadcast to WS ──► WS clients           │
    │                                            │
    └── Aggregate metrics ──► In-memory ring buf │
                                                 │
                               All three consumers
                               must not block the
                               parse loop
```

| Language | Parse Loop | Fan-Out | Back-Pressure |
|----------|-----------|---------|---------------|
| **Python** | `readline()` + `json.loads()` — adequate | `asyncio.gather()` — no back-pressure | Manual (queue + consumer task) |
| **Go** | `bufio.Scanner` + `json.Unmarshal` — fast | Multiple goroutines reading from channels | Channel buffer size |
| **Rust** | `BufRead::lines()` + `serde_json` — fastest | `tokio::broadcast` or `tokio::sync::mpsc` | Bounded channel capacity |
| **TypeScript** | `readline` + `JSON.parse` — adequate | EventEmitter or RxJS | RxJS `bufferTime` / `throttle` |
| **Elixir** | `Port` + `Jason.decode` — adequate | GenStage pipeline with demand-based flow | Built-in (GenStage demand model) |

---

## 7. Recommendations

### 7.1 Immediate Wins (No Language Change Needed)

1. **Add stable Task IDs** — Generate UUIDs for tasks, embed in TASKS.md comments. Replace `task_index` with `task_id` throughout.

2. **SQLite event store** — Create `~/.claude/manager.db` with the `agent_events` table. Emit events alongside WS broadcasts. Enables historical queries immediately.

3. **Unify milestone naming** — Rename `session.milestones` to `session.tool_labels`. Keep `milestones_svc` for persistent completion records. Remove `agent_milestone` WS event.

4. **Read from PostgreSQL** — The DB layer already has `list_sessions` and `get_session`. Wire them to an API endpoint for session history.

### 7.2 Medium-Term (Python Refactors)

5. **Capability model** — Replace `is_controller` with `capabilities: set[str]`. Decompose `_on_session_done` into hooks.

6. **Migrate tasks to SQLite** — Structured storage with stable IDs, status indexes, and cross-project queries.

7. **Migrate widgets to SQLite** — Eliminate the dual in-memory + JSON file problem.

### 7.3 Long-Term (Language Migration Considerations)

8. **Event-sourced agent lifecycle** — Full event stream for every agent, enabling replay, metrics, and audit.

9. **Back-pressure on streaming** — Bounded channels between parse loop and broadcast/persist consumers.

10. **Typed event pipeline** — Tagged unions with exhaustive pattern matching for event handlers.

---

## 8. Implications for Language Choice

This section maps the data model requirements identified above to concrete language capabilities. It is intended to be cross-referenced by the LANGUAGE-ANALYSIS.md document.

### 8.1 Critical Data Model Requirements

| Requirement | Weight | Description |
|-------------|--------|-------------|
| **R1: Tagged unions for events** | HIGH | The event schema has 15+ variants. Every handler must be exhaustive. |
| **R2: High-throughput JSON serialization** | HIGH | TEXT_DELTA events at 100-500/min/agent, WS broadcast to N clients. |
| **R3: Concurrent session management** | HIGH | 1-10 agent sessions, each with subprocess I/O, parse loop, and broadcast. |
| **R4: SQLite integration** | MEDIUM | Event store and structured entities. Needs async-friendly driver. |
| **R5: Subprocess management** | HIGH | Spawn, stream stdout/stderr, detect exit, kill. Cross-platform. |
| **R6: WebSocket server** | HIGH | Long-lived connections, broadcast to multiple clients, reconnection handling. |
| **R7: Back-pressure** | MEDIUM | Prevent fast agents from overwhelming slow WS clients. |
| **R8: Process supervision** | MEDIUM | Restart failed agents, manage lifecycle, prevent zombie processes. |
| **R9: Schema migration** | LOW | Evolve the data model over time without data loss. |
| **R10: Developer velocity** | MEDIUM | How quickly can the team iterate on model changes? |

### 8.2 Language Scores Against Data Model Requirements

```
┌──────────────┬──────┬──────┬──────┬──────┬──────┐
│ Requirement  │  Py  │  Go  │ Rust │  TS  │  Ex  │
├──────────────┼──────┼──────┼──────┼──────┼──────┤
│ R1: Unions   │  6   │  5   │ 10   │  9   │  8   │
│ R2: JSON     │  5   │  7   │ 10   │  6   │  7   │
│ R3: Concurr  │  6   │  9   │  9   │  5   │ 10   │
│ R4: SQLite   │  6   │  8   │  9   │  8   │  7   │
│ R5: Subproc  │  8   │  8   │  7   │  6   │  7   │
│ R6: WS       │  7   │  8   │  8   │  9   │  9   │
│ R7: Backpres │  4   │  9   │  9   │  5   │ 10   │
│ R8: Supervis │  4   │  6   │  6   │  4   │ 10   │
│ R9: Migrat   │  7   │  6   │  7   │  7   │  9   │
│ R10: Veloc   │  9   │  7   │  5   │  8   │  7   │
├──────────────┼──────┼──────┼──────┼──────┼──────┤
│ WEIGHTED AVG │  6.1 │  7.3 │  8.0 │  6.7 │  8.4 │
│ (R1-R3 2x)   │      │      │      │      │      │
└──────────────┴──────┴──────┴──────┴──────┴──────┘

Weights: R1, R2, R3 counted at 2x; R7, R8 at 1.5x; others at 1x.
```

### 8.3 Language-Specific Data Model Recommendations

**If staying with Python:**
- Use Pydantic discriminated unions for event types
- Use aiosqlite for the event store
- Accept the lack of exhaustive matching (use a `default` handler that logs unknown events)
- Accept the lack of back-pressure (add manual queue-based throttling)
- The existing codebase can adopt the new data model incrementally

**If migrating to Rust:**
- serde enums give the best event type safety
- sqlx provides compile-time SQL verification
- tokio::broadcast provides natural fan-out with back-pressure
- The ownership model prevents data races in the session registry
- Highest effort migration, but the most robust data model implementation

**If migrating to Go:**
- Use interface-based event handling (accept the lack of exhaustiveness)
- pgx or go-sqlite3 for storage
- Channels provide natural back-pressure and fan-out
- goroutines map 1:1 to agent sessions
- Moderate migration effort, good operational simplicity

**If migrating to Elixir:**
- GenServer per agent session is the perfect actor model
- GenStage pipeline for event processing with demand-based back-pressure
- Supervisor trees for agent lifecycle management
- Ecto for schema management and migrations
- Phoenix.PubSub for WS broadcast
- Best overall fit for the data model requirements, moderate migration effort

**If migrating to TypeScript:**
- Discriminated unions are excellent for the event schema
- better-sqlite3 for synchronous SQLite access (no async overhead)
- SharedArrayBuffer could enable multi-threaded event processing
- Shared language with frontend enables shared type definitions
- Weakest concurrency model for the backend workload

### 8.4 Data Model as Migration Forcing Function

The data model analysis reveals that **the current Python implementation's biggest weaknesses are in areas where a language migration would help most:**

1. **No exhaustive event handling** — Adding a new event type requires manually updating every handler. Rust and TypeScript catch missing handlers at compile time.

2. **No back-pressure** — Python's asyncio has no built-in bounded channel. Go and Rust have this natively. Elixir's GenStage is purpose-built.

3. **No supervision** — Python has no process supervision tree. When an agent crashes, there's no automatic restart or cleanup. Elixir has OTP supervision built in.

4. **JSON serialization bottleneck** — At 10 agents with 500 text deltas/min each, the system processes ~5000 JSON serializations/min for WS broadcast alone. Python is the slowest option here. Rust's serde_json is 10x faster.

Conversely, the data model analysis also reveals areas where Python's strengths shine:

1. **Developer velocity** — Pydantic model changes are trivial. Adding a field to the event schema is a one-line change.

2. **Subprocess management** — `asyncio.create_subprocess_exec` is mature and well-tested.

3. **Incremental migration** — The new data model can be adopted field-by-field, table-by-table, without a big-bang rewrite.

**Bottom line:** If the system stays at 1-10 agents, Python is adequate. If the goal is 50+ concurrent agents with rich event history and metrics, a language with better concurrency and type safety (Rust or Elixir) would pay dividends on the data model alone.

---

*End of analysis. This document should be cross-referenced with LANGUAGE-ANALYSIS.md for the complete technology evaluation.*
