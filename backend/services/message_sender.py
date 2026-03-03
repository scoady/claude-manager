"""Send messages to Claude agents via the claude CLI."""
from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))
DEFAULT_TIMEOUT = 300  # 5 minutes max for a response


async def send_message(
    session_id: str,
    message: str,
    project_path: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bool, str, str]:
    """Send a message to a Claude agent session.

    Uses: claude -p --resume <session-id> <message>

    Returns:
        (success: bool, stdout: str, stderr: str)
    """
    if not message.strip():
        return False, "", "Empty message"

    cmd = [
        CLAUDE_BIN,
        "--print",
        "--resume", session_id,
        "--output-format", "stream-json",
        message,
    ]

    env = {**os.environ}

    cwd = project_path if project_path and Path(project_path).is_dir() else None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return False, "", f"Timeout after {timeout}s"

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        success = proc.returncode == 0

        return success, stdout, stderr

    except FileNotFoundError:
        return False, "", f"Claude CLI not found at {CLAUDE_BIN}"
    except Exception as exc:
        return False, "", str(exc)


async def send_message_streaming(
    session_id: str,
    message: str,
    project_path: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> asyncio.StreamReader | None:
    """Start a streaming send and return the stdout stream for live reading."""
    cmd = [
        CLAUDE_BIN,
        "--print",
        "--resume", session_id,
        "--output-format", "stream-json",
        "--include-partial-messages",
        message,
    ]

    env = {**os.environ}
    cwd = project_path if project_path and Path(project_path).is_dir() else None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        return proc
    except Exception:
        return None
