"""Pydantic models for Claude Agent Manager."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    WORKING = "working"
    ACTIVE = "active"
    IDLE = "idle"
    DISCONNECTED = "disconnected"


class SessionPhase(str, Enum):
    STARTING    = "starting"
    THINKING    = "thinking"
    GENERATING  = "generating"
    TOOL_INPUT  = "tool_input"
    TOOL_EXEC   = "tool_exec"
    IDLE        = "idle"
    INJECTING   = "injecting"
    CANCELLED   = "cancelled"
    ERROR       = "error"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ToolCall(BaseModel):
    id: str
    name: str
    input: dict[str, Any]
    output: str | None = None


class MessageContent(BaseModel):
    type: str  # "text" | "tool_use" | "tool_result" | "thinking"
    text: str | None = None
    tool_call: ToolCall | None = None
    thinking: str | None = None


class AgentMessage(BaseModel):
    uuid: str
    parent_uuid: str | None
    role: MessageRole
    content: list[MessageContent]
    timestamp: str
    session_id: str
    cwd: str | None = None
    git_branch: str | None = None
    model: str | None = None


# ─── Managed Project models ────────────────────────────────────────────────────


class ProjectConfig(BaseModel):
    parallelism: int = 1
    model: str | None = None
    mcp_config: str | None = None  # Path to MCP config JSON file
    dashboard_prompt: str | None = None  # User's dashboard intent description


class ManagedProject(BaseModel):
    name: str
    path: str
    description: str | None = None   # First non-heading line of PROJECT.md
    goal: str | None = None          # Full PROJECT.md content
    config: ProjectConfig = ProjectConfig()
    active_session_ids: list[str] = []


class SkillInfo(BaseModel):
    name: str
    description: str | None = None
    source: str  # "global", "local", "plugin"
    path: str
    enabled: bool = False
    frontmatter: dict[str, Any] = {}


class CreateSkillRequest(BaseModel):
    name: str
    description: str
    content: str
    allowed_tools: list[str] = []
    scope: str = "global"  # "global" or a project name


class BootstrapProjectRequest(BaseModel):
    name: str
    description: str
    model: str | None = None


class DispatchRequest(BaseModel):
    task: str
    model: str | None = None


class InjectRequest(BaseModel):
    message: str


class AddTaskRequest(BaseModel):
    text: str


class UpdateTaskRequest(BaseModel):
    status: str  # "pending" | "in_progress" | "done"


class PlanTaskRequest(BaseModel):
    text: str
    model: str | None = None


# ─── Workflow Template models ────────────────────────────────────────────────


class RolePreset(BaseModel):
    role: str
    label: str
    is_worker: bool = True
    persona: str = ""
    expertise: list[str] = []
    builtin: bool = False


class ConfigField(BaseModel):
    type: str           # "number" | "string" | "boolean" | "select"
    label: str
    default: Any = None
    min: int | None = None
    max: int | None = None
    options: list[str] | None = None


class PhaseDefinition(BaseModel):
    id: str
    label: str          # supports {{iteration_number}} interpolation
    repeats: bool = False
    creates_isolation: bool = False
    cleanup_isolation: bool = False
    prompt: str


class WorkflowTemplate(BaseModel):
    id: str
    name: str
    description: str
    category: str = "general"
    icon: str = "default"
    version: int = 1
    role_presets: list[RolePreset] = []
    default_team: list["TeamRole"] = []
    isolation_strategy: str = "none"   # "git_worktree" | "subdirectory" | "none"
    config_schema: dict[str, ConfigField] = {}
    phases: list[PhaseDefinition] = []
    instructions_overlay: str = ""


# ─── Workflow models ─────────────────────────────────────────────────────────

# Kept for migration compatibility — new code uses phase_id/phase_label
class WorkflowPhaseType(str, Enum):
    QUARTER_PLANNING  = "quarter_planning"
    SPRINT_PLANNING   = "sprint_planning"
    SPRINT_EXECUTION  = "sprint_execution"
    SPRINT_REVIEW     = "sprint_review"
    SPRINT_RETRO      = "sprint_retrospective"
    COMPLETE          = "complete"


class WorkflowStatus(str, Enum):
    DRAFT    = "draft"
    RUNNING  = "running"
    PAUSED   = "paused"
    COMPLETE = "complete"


class TeamRole(BaseModel):
    role: str
    count: int = 1
    instructions: str = ""


class WorkflowPhase(BaseModel):
    phase_id: str = ""
    phase_label: str = ""
    iteration_number: int | None = None
    status: str = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    summary: str | None = None
    # Legacy fields — used during migration from old workflow.json
    phase_type: WorkflowPhaseType | None = None
    sprint_number: int | None = None


class WorktreeInfo(BaseModel):
    role: str
    instance: int
    branch: str
    path: str
    status: str = "active"


class IsolationInfo(BaseModel):
    """Tracks an isolation workspace (worktree or subdirectory)."""
    role: str
    instance: int
    branch: str = ""
    path: str
    status: str = "active"
    strategy: str = "git_worktree"  # "git_worktree" | "subdirectory"


class WorkflowConfig(BaseModel):
    auto_continue: bool = True
    values: dict[str, Any] = {}
    # Legacy fields — auto-populated during migration
    total_sprints: int | None = None
    sprint_duration_hint: str | None = None
    merge_strategy: str | None = None


class Workflow(BaseModel):
    id: str
    project_name: str
    template_id: str = "software-engineering"
    team: list[TeamRole]
    config: WorkflowConfig = WorkflowConfig()
    status: WorkflowStatus = WorkflowStatus.DRAFT
    phases: list[WorkflowPhase] = []
    current_phase_index: int = 0
    worktrees: list[WorktreeInfo] = []
    isolation: list[IsolationInfo] = []
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class CreateWorkflowRequest(BaseModel):
    template_id: str = "software-engineering"
    team: list[TeamRole]
    config: WorkflowConfig = WorkflowConfig()


class WorkflowActionRequest(BaseModel):
    action: str


# ─── Agent (runtime) ──────────────────────────────────────────────────────────


class Agent(BaseModel):
    session_id: str
    pid: int | None = None
    project_name: str
    project_path: str
    status: AgentStatus
    task: str | None = None           # The dispatch prompt
    last_chunk: str | None = None     # Most recent streaming output line
    message_count: int = 0
    model: str | None = None
    git_branch: str | None = None
    cpu_percent: float | None = None
    mem_percent: float | None = None
    started_at: str | None = None
    has_pending_injection: bool = False


# ─── Legacy send/response (kept for inject endpoint) ──────────────────────────


class SendMessageRequest(BaseModel):
    message: str


class SendMessageResponse(BaseModel):
    session_id: str
    success: bool
    response: str | None = None
    error: str | None = None


# ─── Stats ────────────────────────────────────────────────────────────────────


class GlobalStats(BaseModel):
    total_projects: int
    total_agents: int
    working_agents: int
    idle_agents: int
    uptime_seconds: float


# ─── WebSocket ────────────────────────────────────────────────────────────────


# ─── Canvas models ────────────────────────────────────────────────────────────


class WidgetState(BaseModel):
    id: str
    project: str
    title: str = ""
    html: str = ""
    css: str = ""
    js: str = ""
    tab: str = "main"  # canvas tab this widget belongs to
    template_id: str | None = None  # widget catalog template used to render
    template_data: dict | None = None  # raw data passed to the template
    grid_col: int = 1
    grid_row: int = 1
    col_span: int = 1
    row_span: int = 1
    # GridStack layout fields (x, y, w, h in grid units)
    gs_x: int | None = None
    gs_y: int | None = None
    gs_w: int = 4
    gs_h: int = 3
    no_resize: bool = False
    no_move: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WidgetCreate(BaseModel):
    id: str | None = None  # optional caller-supplied ID (used by MCP canvas_put)
    title: str = ""
    html: str = ""
    css: str = ""
    js: str = ""
    tab: str = "main"  # canvas tab this widget belongs to
    template_id: str | None = None
    template_data: dict | None = None
    grid_col: int = 1
    grid_row: int = 1
    col_span: int = 1
    row_span: int = 1
    gs_x: int | None = None
    gs_y: int | None = None
    gs_w: int = 4
    gs_h: int = 3
    no_resize: bool = False
    no_move: bool = False


class WidgetUpdate(BaseModel):
    title: str | None = None
    html: str | None = None
    css: str | None = None
    js: str | None = None
    tab: str | None = None  # canvas tab this widget belongs to
    template_id: str | None = None
    template_data: dict | None = None
    grid_col: int | None = None
    grid_row: int | None = None
    col_span: int | None = None
    row_span: int | None = None
    gs_x: int | None = None
    gs_y: int | None = None
    gs_w: int | None = None
    gs_h: int | None = None
    no_resize: bool | None = None
    no_move: bool | None = None


class WSMessageType(str, Enum):
    PROJECT_LIST    = "project_list"
    PROJECT_UPDATE  = "project_update"
    AGENT_SPAWNED   = "agent_spawned"
    AGENT_DONE      = "agent_done"
    AGENT_STREAM    = "agent_stream"
    AGENT_UPDATE    = "agent_update"
    AGENT_MILESTONE = "agent_milestone"
    SESSION_PHASE   = "session_phase"
    TOOL_START      = "tool_start"
    TOOL_DONE       = "tool_done"
    TURN_DONE       = "turn_done"
    INJECTION_ACK   = "injection_ack"
    RULE_FIRED      = "rule_fired"
    STATS_UPDATE    = "stats_update"
    TASKS_UPDATED       = "tasks_updated"
    MILESTONES_UPDATED  = "milestones_updated"
    WORKFLOW_UPDATED    = "workflow_updated"
    ERROR               = "error"
    # Canvas events
    AGENT_STATE_SYNC      = "agent_state_sync"
    CANVAS_WIDGET_CREATED = "canvas_widget_created"
    CANVAS_WIDGET_UPDATED = "canvas_widget_updated"
    CANVAS_WIDGET_REMOVED = "canvas_widget_removed"
    CANVAS_SCENE_REPLACED = "canvas_scene_replaced"
    CANVAS_CLEARED        = "canvas_cleared"


class WSMessage(BaseModel):
    type: WSMessageType
    data: Any
    timestamp: str | None = None
