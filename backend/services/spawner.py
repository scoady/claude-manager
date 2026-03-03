"""Agent spawner — launch claude subprocesses, stream output, inject messages."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import AgentStatus

if TYPE_CHECKING:
    from ..ws_manager import WSManager

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))


@dataclass
class RunningAgent:
    project_name: str
    project_path: str
    task: str
    proc: asyncio.subprocess.Process
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str | None = None       # Parsed from first stream-json system event
    status: AgentStatus = AgentStatus.WORKING
    output_buffer: list[str] = field(default_factory=list)  # Last 20 text chunks
    pending_injection: str | None = None
    model: str | None = None
    stream_task: asyncio.Task | None = None  # Background reader task

    def last_chunk(self) -> str | None:
        return self.output_buffer[-1] if self.output_buffer else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_name": self.project_name,
            "project_path": self.project_path,
            "task": self.task,
            "status": self.status.value,
            "started_at": self.started_at,
            "last_chunk": self.last_chunk(),
            "has_pending_injection": self.pending_injection is not None,
            "model": self.model,
            "pid": self.proc.pid,
        }


# session_id → RunningAgent  (or pending_key → RunningAgent before session_id known)
_registry: dict[str, RunningAgent] = {}
# Maps proc.pid → pending agent before session_id is known
_pending: dict[int, RunningAgent] = {}


def get_running_agents() -> list[RunningAgent]:
    return list(_registry.values())


def get_running_for_project(project_name: str) -> list[RunningAgent]:
    return [a for a in _registry.values() if a.project_name == project_name]


def get_agent(session_id: str) -> RunningAgent | None:
    return _registry.get(session_id)


async def dispatch_agent(
    project_name: str,
    project_path: str,
    task: str,
    model: str | None = None,
    ws_manager: WSManager | None = None,
) -> str:
    """Spawn a new claude agent subprocess.

    Returns a temporary key (PID string) until the real session_id is parsed
    from the stream. Returns the session_id once known (callers can poll).
    """
    cmd = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "stream-json",
        "--include-partial-messages",
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(task)

    cwd = project_path if Path(project_path).is_dir() else None

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env={**os.environ},
    )

    agent = RunningAgent(
        project_name=project_name,
        project_path=project_path,
        task=task,
        proc=proc,
        model=model,
    )

    _pending[proc.pid] = agent

    # Start background streaming task
    stream_task = asyncio.create_task(
        _stream_agent(agent, ws_manager),
        name=f"stream-{proc.pid}",
    )
    agent.stream_task = stream_task

    return f"pending-{proc.pid}"  # Caller can watch for agent_spawned WS event


async def _stream_agent(agent: RunningAgent, ws_manager: WSManager | None) -> None:
    """Read stdout line-by-line, parse stream-json events, broadcast to WS."""
    assert agent.proc.stdout is not None

    try:
        while True:
            line = await agent.proc.stdout.readline()
            if not line:
                break

            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            await _handle_stream_event(agent, event, ws_manager)

    except Exception as exc:
        print(f"[spawner] stream error for {agent.project_name}: {exc}")
    finally:
        # Wait for process to finish
        try:
            await asyncio.wait_for(agent.proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            agent.proc.kill()

        agent.status = AgentStatus.IDLE

        # Promote from pending to registry if session_id now known
        if agent.session_id:
            _pending.pop(agent.proc.pid, None)
            if agent.session_id not in _registry:
                _registry[agent.session_id] = agent

        if ws_manager and agent.session_id:
            await ws_manager.broadcast(
                "agent_done",
                {
                    "session_id": agent.session_id,
                    "project_name": agent.project_name,
                    "exit_code": agent.proc.returncode,
                },
            )

        # Send pending injection if any
        if agent.pending_injection and agent.session_id:
            msg = agent.pending_injection
            agent.pending_injection = None
            asyncio.create_task(
                _send_followup(agent, msg, ws_manager),
                name=f"inject-{agent.session_id}",
            )


async def _handle_stream_event(
    agent: RunningAgent,
    event: dict[str, Any],
    ws_manager: WSManager | None,
) -> None:
    """Process one stream-json event from the claude subprocess."""
    etype = event.get("type")

    # System init — captures session_id
    if etype == "system" and event.get("subtype") == "init":
        session_id = event.get("session_id")
        if session_id and not agent.session_id:
            agent.session_id = session_id
            _pending.pop(agent.proc.pid, None)
            _registry[session_id] = agent
            if ws_manager:
                await ws_manager.broadcast(
                    "agent_spawned",
                    {
                        "session_id": session_id,
                        "project_name": agent.project_name,
                        "task": agent.task,
                        "started_at": agent.started_at,
                    },
                )
        return

    # Text delta — streaming text content
    if etype == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            chunk = delta.get("text", "")
            if chunk:
                agent.output_buffer.append(chunk)
                if len(agent.output_buffer) > 20:
                    agent.output_buffer.pop(0)
                if ws_manager and agent.session_id:
                    await ws_manager.broadcast(
                        "agent_stream",
                        {
                            "session_id": agent.session_id,
                            "project_name": agent.project_name,
                            "chunk": chunk,
                            "done": False,
                        },
                    )
        return

    # Message stop — final response complete
    if etype == "message_stop":
        if ws_manager and agent.session_id:
            await ws_manager.broadcast(
                "agent_stream",
                {
                    "session_id": agent.session_id,
                    "project_name": agent.project_name,
                    "chunk": "",
                    "done": True,
                },
            )
        return

    # Extract model from assistant message
    if etype == "message_start":
        msg = event.get("message", {})
        if msg.get("model") and not agent.model:
            agent.model = msg["model"]


async def inject_message(
    session_id: str,
    message: str,
    ws_manager: WSManager | None = None,
) -> bool:
    """Inject a message to a running or idle agent.

    If agent is WORKING: queue the message (sent after current response).
    If agent is IDLE: send immediately via --resume.
    """
    agent = _registry.get(session_id)
    if not agent:
        return False

    if agent.status == AgentStatus.WORKING:
        # Queue: will be sent when _stream_agent detects EOF
        agent.pending_injection = message
        if ws_manager:
            await ws_manager.broadcast("agent_update", agent.to_dict())
        return True

    # Idle or otherwise — send immediately
    asyncio.create_task(
        _send_followup(agent, message, ws_manager),
        name=f"inject-{session_id}",
    )
    return True


async def _send_followup(
    agent: RunningAgent,
    message: str,
    ws_manager: WSManager | None,
) -> None:
    """Spawn a follow-up claude invocation via --resume."""
    if not agent.session_id:
        return

    cmd = [
        CLAUDE_BIN,
        "--print",
        "--resume", agent.session_id,
        "--output-format", "stream-json",
        "--include-partial-messages",
        message,
    ]

    cwd = agent.project_path if Path(agent.project_path).is_dir() else None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ},
        )
        # Reuse the same agent object, update proc
        agent.proc = proc
        agent.status = AgentStatus.WORKING

        if ws_manager:
            await ws_manager.broadcast("agent_update", agent.to_dict())

        # Stream the followup
        await _stream_agent(agent, ws_manager)

    except Exception as exc:
        print(f"[spawner] followup error for {agent.session_id}: {exc}")


async def kill_agent(session_id: str) -> bool:
    """Terminate a running agent subprocess."""
    agent = _registry.get(session_id)
    if not agent:
        return False
    try:
        agent.proc.kill()
        agent.status = AgentStatus.DISCONNECTED
        if agent.stream_task and not agent.stream_task.done():
            agent.stream_task.cancel()
        _registry.pop(session_id, None)
        return True
    except Exception:
        return False


def prune_finished() -> list[str]:
    """Remove agents whose process has exited and have no pending injection.

    Returns list of pruned session_ids.
    """
    pruned = []
    for sid, agent in list(_registry.items()):
        if agent.proc.returncode is not None and agent.status != AgentStatus.WORKING:
            if not agent.pending_injection:
                pruned.append(sid)
                _registry.pop(sid, None)
    return pruned
