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


def _format_milestone(tool: str, input_json: str) -> str:
    """Build a short human-readable label from a tool call."""
    try:
        inp = json.loads(input_json) if input_json.strip() else {}
    except json.JSONDecodeError:
        inp = {}

    if tool in ("Read",):
        key = Path(inp.get("file_path", "")).name
    elif tool in ("Write", "Edit"):
        key = Path(inp.get("file_path", "")).name
    elif tool == "Bash":
        key = inp.get("command", "")[:60].split("\n")[0]
    elif tool == "Grep":
        key = inp.get("pattern", "")[:40]
    elif tool == "Glob":
        key = inp.get("pattern", "")[:40]
    elif tool == "WebFetch":
        url = inp.get("url", "")
        key = url.split("/")[-1][:40] if url else ""
    elif tool == "WebSearch":
        key = inp.get("query", "")[:40]
    elif tool == "Agent":
        key = inp.get("description", "")[:40]
    else:
        key = ""

    return f"{tool} · {key}" if key else tool


@dataclass
class RunningAgent:
    project_name: str
    project_path: str
    task: str
    proc: asyncio.subprocess.Process
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str | None = None       # temp "pending-PID" until real session_id from stream
    status: AgentStatus = AgentStatus.WORKING
    output_buffer: list[str] = field(default_factory=list)  # Last 20 text chunks
    pending_injection: str | None = None
    model: str | None = None
    stream_task: asyncio.Task | None = None
    # Milestone tracking
    milestones: list[str] = field(default_factory=list)     # Last 10 tool calls
    current_tool: str | None = None                          # Tool currently being invoked
    _tool_input_buf: str = field(default="")                # Accumulates input_json_delta

    def last_chunk(self) -> str | None:
        return self.output_buffer[-1] if self.output_buffer else None

    def current_milestone(self) -> str | None:
        if self.current_tool:
            return f"{self.current_tool}…"
        return self.milestones[-1] if self.milestones else None

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
            "current_milestone": self.current_milestone(),
            "milestones": self.milestones,
        }


# session_id → RunningAgent  (temp "pending-PID" key until real session_id known)
_registry: dict[str, RunningAgent] = {}


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

    The agent is immediately visible in the registry with a temporary
    "pending-{pid}" session_id. The real session_id is assigned once the
    subprocess emits the system.init stream event.
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

    temp_id = f"pending-{proc.pid}"
    agent = RunningAgent(
        project_name=project_name,
        project_path=project_path,
        task=task,
        proc=proc,
        model=model,
        session_id=temp_id,
    )

    _registry[temp_id] = agent

    # Broadcast immediately so UI shows the agent card right away
    if ws_manager:
        await ws_manager.broadcast(
            "agent_spawned",
            {
                "session_id": temp_id,
                "project_name": project_name,
                "task": task,
                "started_at": agent.started_at,
            },
        )

    # Background tasks: stream stdout, drain stderr
    stream_task = asyncio.create_task(
        _stream_agent(agent, ws_manager),
        name=f"stream-{proc.pid}",
    )
    asyncio.create_task(
        _drain_stderr(proc, project_name),
        name=f"stderr-{proc.pid}",
    )
    agent.stream_task = stream_task

    return temp_id


async def _drain_stderr(proc: asyncio.subprocess.Process, label: str) -> None:
    """Read and log stderr from the subprocess."""
    if proc.stderr is None:
        return
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            print(f"[spawner:{label}] stderr: {line.decode('utf-8', errors='replace').rstrip()}")
    except Exception:
        pass


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
        try:
            await asyncio.wait_for(agent.proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            agent.proc.kill()

        agent.status = AgentStatus.IDLE

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

    # ── System init — real session_id arrives ─────────────────────────────────
    if etype == "system" and event.get("subtype") == "init":
        session_id = event.get("session_id")
        old_id = agent.session_id  # may be "pending-PID" or already real
        if session_id and session_id != old_id:
            # Rename registry entry
            _registry.pop(old_id, None)
            _registry[session_id] = agent
            agent.session_id = session_id

            # Tell frontend to update the temp ID it received earlier
            if ws_manager:
                await ws_manager.broadcast(
                    "agent_id_assigned",
                    {
                        "old_session_id": old_id,
                        "session_id": session_id,
                        "project_name": agent.project_name,
                    },
                )
        return

    # ── Tool use start — new milestone beginning ───────────────────────────────
    if etype == "content_block_start":
        block = event.get("content_block", {})
        if block.get("type") == "tool_use":
            agent.current_tool = block.get("name", "tool")
            agent._tool_input_buf = ""
        return

    # ── Input delta accumulation ───────────────────────────────────────────────
    if etype == "content_block_delta":
        delta = event.get("delta", {})
        dtype = delta.get("type")

        if dtype == "input_json_delta":
            agent._tool_input_buf += delta.get("partial_json", "")
            return

        if dtype == "text_delta":
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

    # ── Tool use complete — finalize milestone ─────────────────────────────────
    if etype == "content_block_stop":
        if agent.current_tool:
            milestone = _format_milestone(agent.current_tool, agent._tool_input_buf)
            agent.milestones.append(milestone)
            if len(agent.milestones) > 10:
                agent.milestones.pop(0)
            agent.current_tool = None
            agent._tool_input_buf = ""

            if ws_manager and agent.session_id:
                await ws_manager.broadcast(
                    "agent_milestone",
                    {
                        "session_id": agent.session_id,
                        "project_name": agent.project_name,
                        "milestone": milestone,
                        "milestones": agent.milestones,
                    },
                )
        return

    # ── Message stop ───────────────────────────────────────────────────────────
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

    # ── Extract model from first assistant message ─────────────────────────────
    if etype == "message_start":
        msg = event.get("message", {})
        if msg.get("model") and not agent.model:
            agent.model = msg["model"]


async def inject_message(
    session_id: str,
    message: str,
    ws_manager: WSManager | None = None,
) -> bool:
    """Inject a message to a running or idle agent."""
    agent = _registry.get(session_id)
    if not agent:
        return False

    if agent.status == AgentStatus.WORKING:
        agent.pending_injection = message
        if ws_manager:
            await ws_manager.broadcast("agent_update", agent.to_dict())
        return True

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
        agent.proc = proc
        agent.status = AgentStatus.WORKING

        asyncio.create_task(
            _drain_stderr(proc, agent.project_name),
            name=f"stderr-resume-{proc.pid}",
        )

        if ws_manager:
            await ws_manager.broadcast("agent_update", agent.to_dict())

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
    """Remove agents whose process has exited and have no pending injection."""
    pruned = []
    for sid, agent in list(_registry.items()):
        if agent.proc.returncode is not None and agent.status != AgentStatus.WORKING:
            if not agent.pending_injection:
                pruned.append(sid)
                _registry.pop(sid, None)
    return pruned
