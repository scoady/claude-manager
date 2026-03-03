"""Agent discovery, process monitoring, and session reading."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from ..models import (
    Agent,
    AgentMessage,
    AgentStatus,
    MessageContent,
    MessageRole,
    ToolCall,
)

_CLAUDE_DATA_DIR = os.environ.get("CLAUDE_DATA_DIR", os.path.expanduser("~/.claude"))
CLAUDE_DIR = Path(_CLAUDE_DATA_DIR)
PROJECTS_DIR = CLAUDE_DIR / "projects"
TASKS_DIR = CLAUDE_DIR / "tasks"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))

# Track file read positions for efficient tailing
_file_positions: dict[str, int] = {}
_agent_cache: dict[str, Agent] = {}
_start_time = time.time()


def _cwd_to_project_dir(cwd: str) -> str:
    """Convert an absolute path to Claude's project directory encoding.

    /Users/foo/bar -> -Users-foo-bar
    """
    return cwd.replace("/", "-")


def _project_dir_to_path(dir_name: str) -> str:
    """Reverse Claude's project directory encoding.

    -Users-foo-bar -> /Users/foo/bar
    """
    return dir_name.replace("-", "/", 1)  # First - becomes leading /
    # More accurate: replace - back to / where it was a separator
    # The encoding is straightforward: all / become -
    # So we just replace - with / but must handle the leading one
    # Actually: dir_name starts with - (from /), so we re-prefix /


def _project_dir_to_path_accurate(dir_name: str) -> str:
    """Convert Claude project dir name back to absolute path."""
    # -Users-ayx106492-git-llm-daw -> /Users/ayx106492/git/llm-daw
    # Simply replace - with / everywhere since - in paths is unusual
    # but we must be careful: hyphens in project names stay as-is
    # The encoding: path.replace('/', '-') so every / becomes -
    # We can't perfectly reverse since - might be in dir names
    # Best effort: replace - with /
    return dir_name.replace("-", "/")


async def _get_process_cwd(pid: int) -> str | None:
    """Get the working directory of a process on macOS using lsof."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lsof", "-a", f"-p{pid}", "-dcwd", "-Fn",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        for line in stdout.decode().splitlines():
            if line.startswith("n"):
                return line[1:]
    except Exception:
        pass
    return None


async def get_claude_processes() -> list[dict[str, Any]]:
    """Discover all running Claude Code processes."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps", "aux",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except Exception:
        return []

    processes = []
    for line in stdout.decode().splitlines():
        # Match claude processes, exclude grep/manager itself
        if "claude" not in line:
            continue
        if "grep" in line or "claude-manager" in line:
            continue
        if "python" in line and "main.py" in line:
            continue

        parts = line.split(None, 10)
        if len(parts) < 11:
            continue

        try:
            pid = int(parts[1])
        except ValueError:
            continue

        cmd = parts[10].strip()

        # Skip non-claude-code processes
        if not any(x in cmd for x in ["claude code", "/claude", ".local/bin/claude"]):
            # Also match bare 'claude' binary
            if not (cmd.startswith("claude") or "/claude " in cmd or cmd == "claude"):
                continue

        # Extract session ID from --resume flag
        session_id: str | None = None
        resume_match = re.search(r"--resume\s+([0-9a-f-]{36})", cmd)
        if resume_match:
            session_id = resume_match.group(1)

        processes.append({
            "pid": pid,
            "cpu": float(parts[2]) if parts[2] != "-" else 0.0,
            "mem": float(parts[3]) if parts[3] != "-" else 0.0,
            "cmd": cmd,
            "session_id": session_id,
            "started_at": parts[8] if len(parts) > 8 else None,
        })

    return processes


def _find_sessions_for_project(project_dir: Path) -> list[Path]:
    """Find all session JSONL files in a project directory."""
    if not project_dir.exists():
        return []
    return sorted(
        project_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _parse_content_block(block: dict[str, Any]) -> MessageContent:
    """Parse a single content block from a Claude message."""
    btype = block.get("type", "text")

    if btype == "text":
        return MessageContent(type="text", text=block.get("text", ""))

    if btype == "thinking":
        return MessageContent(type="thinking", thinking=block.get("thinking", ""))

    if btype == "tool_use":
        return MessageContent(
            type="tool_use",
            tool_call=ToolCall(
                id=block.get("id", ""),
                name=block.get("name", ""),
                input=block.get("input", {}),
            ),
        )

    if btype == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            text_parts = [
                c.get("text", "") for c in content if c.get("type") == "text"
            ]
            content = "\n".join(text_parts)
        return MessageContent(
            type="tool_result",
            tool_call=ToolCall(
                id=block.get("tool_use_id", ""),
                name="result",
                input={},
                output=str(content),
            ),
        )

    # Fallback
    return MessageContent(type=btype, text=str(block))


def parse_jsonl_line(line: str) -> dict[str, Any] | None:
    """Parse a single JSONL line, returning None on failure."""
    try:
        return json.loads(line.strip())
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_message(obj: dict[str, Any]) -> AgentMessage | None:
    """Extract an AgentMessage from a parsed JSONL object."""
    msg_type = obj.get("type")
    if msg_type not in ("user", "assistant"):
        return None

    raw_msg = obj.get("message", {})
    role_str = raw_msg.get("role", msg_type)
    try:
        role = MessageRole(role_str)
    except ValueError:
        return None

    # Parse content
    raw_content = raw_msg.get("content", "")
    if isinstance(raw_content, str):
        content = [MessageContent(type="text", text=raw_content)]
    elif isinstance(raw_content, list):
        content = [_parse_content_block(b) for b in raw_content]
    else:
        content = []

    # Skip empty or pure-thinking-only messages for brevity
    has_real_content = any(
        c.type in ("text", "tool_use", "tool_result") for c in content
    )
    if not has_real_content and role == MessageRole.ASSISTANT:
        # Only thinking blocks — include but mark
        pass

    model = raw_msg.get("model")

    return AgentMessage(
        uuid=obj.get("uuid", ""),
        parent_uuid=obj.get("parentUuid"),
        role=role,
        content=content,
        timestamp=obj.get("timestamp", ""),
        session_id=obj.get("sessionId", ""),
        cwd=obj.get("cwd"),
        git_branch=obj.get("gitBranch"),
        model=model,
    )


async def read_session_messages(session_file: Path) -> list[AgentMessage]:
    """Read all messages from a session JSONL file."""
    messages: list[AgentMessage] = []
    try:
        loop = asyncio.get_event_loop()
        content = await loop.run_in_executor(None, session_file.read_text, "utf-8")
        for line in content.splitlines():
            obj = parse_jsonl_line(line)
            if obj is None:
                continue
            msg = _extract_message(obj)
            if msg:
                messages.append(msg)
    except Exception:
        pass
    return messages


async def read_new_messages(session_file: Path) -> list[AgentMessage]:
    """Read only new messages from a session file since last read."""
    file_key = str(session_file)
    last_pos = _file_positions.get(file_key, 0)
    new_messages: list[AgentMessage] = []

    try:
        loop = asyncio.get_event_loop()
        file_size = await loop.run_in_executor(
            None, lambda: os.path.getsize(session_file)
        )
        if file_size <= last_pos:
            return []

        def _read_from(pos: int) -> str:
            with open(session_file, "r", encoding="utf-8") as f:
                f.seek(pos)
                return f.read()

        chunk = await loop.run_in_executor(None, _read_from, last_pos)
        _file_positions[file_key] = file_size

        for line in chunk.splitlines():
            obj = parse_jsonl_line(line)
            if obj is None:
                continue
            msg = _extract_message(obj)
            if msg:
                new_messages.append(msg)
    except Exception:
        pass

    return new_messages


def _project_name_from_dir(dir_name: str) -> str:
    """Extract a human-readable project name from Claude's directory encoding."""
    # -Users-ayx106492-git-llm-daw -> llm-daw
    parts = dir_name.split("-")
    # Remove empty first part, skip common path segments
    skip = {"", "Users", "home", "root"}
    meaningful = [p for p in parts if p and p not in skip]
    if len(meaningful) >= 2:
        # Take last 2 parts as "project" (e.g., "git/llm-daw")
        return "/".join(meaningful[-2:])
    return meaningful[-1] if meaningful else dir_name


def _get_current_task(messages: list[AgentMessage]) -> str | None:
    """Get a summary of the current task from recent messages."""
    # Find the last user message
    for msg in reversed(messages):
        if msg.role == MessageRole.USER:
            for content in msg.content:
                if content.type == "text" and content.text:
                    text = content.text.strip()
                    if len(text) > 80:
                        return text[:77] + "..."
                    return text
    return None


async def discover_agents() -> list[Agent]:
    """Discover all Claude agents: running processes and recent sessions."""
    agents: dict[str, Agent] = {}

    # Get running processes
    processes = await get_claude_processes()

    # Concurrently resolve CWDs for processes without explicit session IDs
    cwd_tasks = []
    for p in processes:
        if p["session_id"] is None:
            cwd_tasks.append(_get_process_cwd(p["pid"]))
        else:
            cwd_tasks.append(asyncio.coroutine(lambda: None)())

    cwds = await asyncio.gather(*cwd_tasks, return_exceptions=True)

    pid_to_cwd: dict[int, str | None] = {}
    for p, cwd in zip(processes, cwds):
        pid_to_cwd[p["pid"]] = None if isinstance(cwd, Exception) else cwd

    # Build process map: session_id -> process info (if known)
    session_to_proc: dict[str, dict] = {}
    for p in processes:
        if p["session_id"]:
            session_to_proc[p["session_id"]] = p

    # Walk all project directories
    if PROJECTS_DIR.exists():
        for project_dir in PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue

            project_dir_name = project_dir.name
            project_path = _project_dir_to_path_accurate(project_dir_name)
            project_name = _project_name_from_dir(project_dir_name)

            session_files = _find_sessions_for_project(project_dir)

            for session_file in session_files:
                session_id = session_file.stem

                # Read messages to understand what's happening
                messages = await read_session_messages(session_file)
                if not messages:
                    continue

                # Initialize file position tracking
                file_key = str(session_file)
                if file_key not in _file_positions:
                    try:
                        _file_positions[file_key] = os.path.getsize(session_file)
                    except Exception:
                        pass

                last_msg = messages[-1]
                proc = session_to_proc.get(session_id)

                # Determine status
                mtime = session_file.stat().st_mtime
                age_seconds = time.time() - mtime

                if proc:
                    if age_seconds < 5:
                        status = AgentStatus.WORKING
                    elif age_seconds < 30:
                        status = AgentStatus.ACTIVE
                    else:
                        status = AgentStatus.IDLE
                else:
                    status = AgentStatus.DISCONNECTED

                # Get model from last assistant message
                model = None
                git_branch = None
                for msg in reversed(messages):
                    if msg.model and not model:
                        model = msg.model
                    if msg.git_branch and not git_branch:
                        git_branch = msg.git_branch
                    if model and git_branch:
                        break

                agent = Agent(
                    session_id=session_id,
                    pid=proc["pid"] if proc else None,
                    project_name=project_name,
                    project_path=project_path,
                    status=status,
                    last_activity=last_msg.timestamp,
                    message_count=len(messages),
                    current_task=_get_current_task(messages),
                    model=model,
                    git_branch=git_branch,
                    cpu_percent=proc["cpu"] if proc else None,
                    mem_percent=proc["mem"] if proc else None,
                    started_at=proc["started_at"] if proc else None,
                )
                agents[session_id] = agent

    return sorted(
        agents.values(),
        key=lambda a: (a.status != AgentStatus.WORKING, a.status != AgentStatus.ACTIVE, a.last_activity or ""),
        reverse=False,
    )


async def poll_for_updates(
    known_sessions: dict[str, int],
) -> list[tuple[str, list[AgentMessage]]]:
    """Poll session files for new messages. Returns list of (session_id, new_messages)."""
    updates = []

    if not PROJECTS_DIR.exists():
        return updates

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for session_file in project_dir.glob("*.jsonl"):
            session_id = session_file.stem
            new_msgs = await read_new_messages(session_file)
            if new_msgs:
                updates.append((session_id, new_msgs))

    return updates
