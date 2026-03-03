"""Pydantic models for Claude Agent Manager."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class AgentStatus(str, Enum):
    WORKING = "working"        # Currently processing a response
    ACTIVE = "active"          # Recently active
    IDLE = "idle"              # Process running but quiet
    DISCONNECTED = "disconnected"  # Process not found


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


class ManagedProject(BaseModel):
    name: str
    path: str
    description: str | None = None   # First non-heading line of PROJECT.md
    goal: str | None = None          # Full PROJECT.md content
    config: ProjectConfig = ProjectConfig()
    active_session_ids: list[str] = []


class BootstrapProjectRequest(BaseModel):
    name: str
    description: str


class DispatchRequest(BaseModel):
    task: str
    model: str | None = None


class InjectRequest(BaseModel):
    message: str


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


class WSMessageType(str, Enum):
    PROJECT_LIST = "project_list"       # Full project list
    PROJECT_UPDATE = "project_update"   # Single project changed
    AGENT_SPAWNED = "agent_spawned"     # New agent started
    AGENT_DONE = "agent_done"           # Agent finished
    AGENT_STREAM = "agent_stream"       # Streaming chunk from subprocess
    AGENT_UPDATE = "agent_update"       # Agent status changed
    STATS_UPDATE = "stats_update"       # Global stats
    ERROR = "error"


class WSMessage(BaseModel):
    type: WSMessageType
    data: Any
    timestamp: str | None = None
