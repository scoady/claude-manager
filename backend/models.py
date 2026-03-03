"""Pydantic models for Claude Agent Manager."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class AgentStatus(str, Enum):
    ACTIVE = "active"      # Process running, recently active
    WORKING = "working"    # Currently processing a response
    IDLE = "idle"          # Process running but quiet
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


class Agent(BaseModel):
    session_id: str
    pid: int | None = None
    project_name: str
    project_path: str
    status: AgentStatus
    last_activity: str | None = None
    message_count: int = 0
    current_task: str | None = None  # Last user message summary
    model: str | None = None
    git_branch: str | None = None
    cpu_percent: float | None = None
    mem_percent: float | None = None
    started_at: str | None = None


class SendMessageRequest(BaseModel):
    message: str


class SendMessageResponse(BaseModel):
    session_id: str
    success: bool
    response: str | None = None
    error: str | None = None


class GlobalStats(BaseModel):
    total_agents: int
    active_agents: int
    working_agents: int
    idle_agents: int
    total_messages: int
    uptime_seconds: float


class WSMessageType(str, Enum):
    AGENT_LIST = "agent_list"
    AGENT_UPDATE = "agent_update"
    NEW_MESSAGE = "new_message"
    AGENT_CONNECTED = "agent_connected"
    AGENT_DISCONNECTED = "agent_disconnected"
    STATS_UPDATE = "stats_update"
    ACTIVITY_EVENT = "activity_event"
    ERROR = "error"


class WSMessage(BaseModel):
    type: WSMessageType
    data: Any
    timestamp: str | None = None
