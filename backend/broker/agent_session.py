"""
AgentSession — one continuous conversation via a claude CLI subprocess.

Lifecycle:
  created → start(task) → _stream_proc [parsing stdout] → IDLE
           → inject_message → _send_followup [--resume] → IDLE → ... → cancelled | error

Authentication uses the CLI's own OAuth tokens (~/.claude), so the user's
Pro/Max subscription is used instead of a separate API key.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..models import SessionPhase

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
OAUTH_TOKEN_FILE = "/run/claude-oauth-token"
CLAUDE_DATA_DIR = Path(os.environ.get("CLAUDE_DATA_DIR", str(Path.home() / ".claude")))


def _get_spawn_env() -> dict[str, str]:
    """Build env for subprocess, reading a fresh OAuth token from file if available."""
    env = {**os.environ}
    try:
        token = Path(OAUTH_TOKEN_FILE).read_text().strip()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    except FileNotFoundError:
        pass
    return env


def _format_milestone(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Build a short human-readable label from a tool call."""
    if tool_name in ("Read", "Write", "Edit"):
        key = Path(tool_input.get("file_path", "")).name
    elif tool_name == "Bash":
        key = tool_input.get("command", "")[:60].split("\n")[0]
    elif tool_name == "Grep":
        key = tool_input.get("pattern", "")[:40]
    elif tool_name == "Glob":
        key = tool_input.get("pattern", "")[:40]
    elif tool_name == "WebFetch":
        url = tool_input.get("url", "")
        key = url.split("/")[-1][:40] if url else ""
    elif tool_name == "WebSearch":
        key = tool_input.get("query", "")[:40]
    elif tool_name == "Agent":
        key = tool_input.get("description", "")[:40]
    else:
        key = ""

    return f"{tool_name} · {key}" if key else tool_name


@dataclass
class AgentSession:
    session_id: str           # Manager-assigned UUID (stable, used as registry key)
    project_name: str
    project_path: str
    model: str
    task: str = ""
    is_controller: bool = False

    # Runtime state
    phase: SessionPhase = SessionPhase.STARTING
    milestones: list[str] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    turn_count: int = 0
    last_text_chunk: str | None = None
    output_buffer: list[dict[str, Any]] = field(default_factory=list)

    # Callbacks — set by AgentBroker after construction
    on_phase_change: Callable | None = field(default=None, repr=False)
    on_text_delta:   Callable | None = field(default=None, repr=False)
    on_tool_start:   Callable | None = field(default=None, repr=False)
    on_tool_done:    Callable | None = field(default=None, repr=False)
    on_turn_done:    Callable | None = field(default=None, repr=False)
    on_session_done: Callable | None = field(default=None, repr=False)

    # Internal — subprocess management
    _cli_session_id: str | None = field(default=None, repr=False)
    _proc: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _stream_task: asyncio.Task | None = field(default=None, repr=False)
    _pending_injection: str | None = field(default=None, repr=False)
    _cancelled: bool = field(default=False, repr=False)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, initial_task: str) -> None:
        """Spawn the initial subprocess and begin streaming."""
        self.task = initial_task
        asyncio.create_task(
            self._spawn_and_stream(initial_task, resume=False),
            name=f"agent-{self.session_id[:8]}",
        )

    async def inject_message(self, message: str) -> None:
        """
        Inject a follow-up message.
        - If subprocess running (WORKING): queued as pending, sent after current proc exits.
        - If IDLE: immediately spawns a --resume follow-up.
        """
        if self.phase == SessionPhase.IDLE and self._cli_session_id:
            asyncio.create_task(
                self._spawn_and_stream(message, resume=True),
                name=f"inject-{self.session_id[:8]}",
            )
        else:
            self._pending_injection = message

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def to_dict(self) -> dict[str, Any]:
        from ..models import AgentStatus
        phase_to_status = {
            SessionPhase.STARTING:    AgentStatus.WORKING,
            SessionPhase.THINKING:    AgentStatus.WORKING,
            SessionPhase.GENERATING:  AgentStatus.WORKING,
            SessionPhase.TOOL_INPUT:  AgentStatus.WORKING,
            SessionPhase.TOOL_EXEC:   AgentStatus.WORKING,
            SessionPhase.IDLE:        AgentStatus.IDLE,
            SessionPhase.INJECTING:   AgentStatus.WORKING,
            SessionPhase.CANCELLED:   AgentStatus.DISCONNECTED,
            SessionPhase.ERROR:       AgentStatus.DISCONNECTED,
        }
        return {
            "session_id": self.session_id,
            "project_name": self.project_name,
            "project_path": self.project_path,
            "task": self.task,
            "status": phase_to_status.get(self.phase, AgentStatus.IDLE).value,
            "phase": self.phase.value,
            "model": self.model,
            "started_at": self.started_at,
            "turn_count": self.turn_count,
            "milestones": self.milestones[-10:],
            "last_chunk": self.last_text_chunk,
            "is_controller": self.is_controller,
            "has_pending_injection": self._pending_injection is not None,
            "pid": self._proc.pid if self._proc else None,
        }

    def get_messages(self) -> list[dict[str, Any]]:
        """Return messages — read from CLI's JSONL session file, fall back to in-memory buffer."""
        jsonl_path = self._get_jsonl_path()
        if jsonl_path:
            try:
                parsed = self._parse_jsonl(jsonl_path)
                if parsed:
                    return parsed
            except Exception:
                pass
        return list(self.output_buffer)

    def _get_jsonl_path(self) -> Path | None:
        """Compute the path to the CLI's persisted JSONL session file."""
        if not self._cli_session_id or not self.project_path:
            return None
        encoded = self.project_path.replace("/", "-").replace(" ", "-")
        jsonl_path = CLAUDE_DATA_DIR / "projects" / encoded / f"{self._cli_session_id}.jsonl"
        return jsonl_path if jsonl_path.exists() else None

    @staticmethod
    def _parse_jsonl(filepath: Path) -> list[dict[str, Any]]:
        """Parse a Claude CLI session JSONL file into frontend-compatible messages."""
        messages: list[dict[str, Any]] = []
        for line in filepath.read_text().splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message", {})
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if isinstance(content, str):
                if content.strip():
                    messages.append({"role": "assistant", "type": "text", "content": content})
                continue
            for block in content:
                btype = block.get("type")
                if btype == "text" and block.get("text", "").strip():
                    messages.append({
                        "role": "assistant",
                        "type": "text",
                        "content": block["text"],
                    })
                elif btype == "tool_use":
                    messages.append({
                        "role": "assistant",
                        "type": "tool_use",
                        "tool_name": block.get("name", "tool"),
                        "tool_id": block.get("id", ""),
                        "tool_input": block.get("input", {}),
                    })
        return messages

    # ── Subprocess management ──────────────────────────────────────────────────

    async def _spawn_and_stream(self, message: str, resume: bool) -> None:
        """Spawn a claude subprocess and stream its output."""
        cmd = [CLAUDE_BIN, "--print", "--output-format", "stream-json", "--verbose"]

        if resume and self._cli_session_id:
            cmd += ["--resume", self._cli_session_id]
        else:
            cmd += ["--model", self.model]

        cmd.append(message)

        cwd = self.project_path if Path(self.project_path).is_dir() else None

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=_get_spawn_env(),
            )
        except Exception as exc:
            print(f"[session:{self.session_id[:8]}] spawn error: {exc}")
            await self._set_phase(SessionPhase.ERROR)
            if self.on_session_done:
                await self.on_session_done(self.session_id, "error")
            return

        await self._set_phase(SessionPhase.GENERATING)

        # Drain stderr in background
        asyncio.create_task(
            self._drain_stderr(),
            name=f"stderr-{self.session_id[:8]}",
        )

        # Stream stdout
        self._stream_task = asyncio.current_task()
        try:
            await self._stream_stdout()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[session:{self.session_id[:8]}] stream error: {exc}")

        # Wait for process to finish
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._proc.kill()

        if self._cancelled:
            await self._set_phase(SessionPhase.CANCELLED)
            if self.on_session_done:
                await self.on_session_done(self.session_id, "cancelled")
            return

        self.turn_count += 1

        # Broadcast turn-done + stream-done markers
        if self.on_text_delta:
            await self.on_text_delta(self.session_id, "")  # sentinel
        if self.on_turn_done:
            await self.on_turn_done(self.session_id, self.turn_count)

        # Check for pending injection
        if self._pending_injection and self._cli_session_id:
            msg = self._pending_injection
            self._pending_injection = None
            await self._spawn_and_stream(msg, resume=True)
        else:
            await self._set_phase(SessionPhase.IDLE)
            if self.on_session_done:
                await self.on_session_done(self.session_id, "idle")

    async def _stream_stdout(self) -> None:
        """Read stdout line-by-line, parse stream-json events."""
        assert self._proc and self._proc.stdout

        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break

            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            await self._handle_stream_event(event)

    async def _drain_stderr(self) -> None:
        """Read and log stderr from the subprocess."""
        if not self._proc or not self._proc.stderr:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    print(f"[session:{self.session_id[:8]}] stderr: {text}")
        except Exception:
            pass

    # ── Stream event handling ──────────────────────────────────────────────────
    #
    # With --verbose (no --include-partial-messages), the CLI emits high-level
    # events:  system → assistant → user → assistant → ... → result
    # Each "assistant" event contains the full message with content blocks.

    async def _handle_stream_event(self, event: dict[str, Any]) -> None:
        """Process one stream-json event from the claude subprocess."""
        etype = event.get("type")

        # ── System init — CLI session_id arrives ──────────────────────────────
        if etype == "system" and event.get("subtype") == "init":
            cli_sid = event.get("session_id")
            if cli_sid:
                self._cli_session_id = cli_sid
            return

        # ── Assistant message — contains text, tool_use, thinking blocks ──────
        if etype == "assistant":
            msg = event.get("message", {})
            model = msg.get("model")
            if model and model != "<synthetic>":
                self.model = model

            content = msg.get("content", [])
            for block in content:
                btype = block.get("type", "")

                if btype == "thinking":
                    await self._set_phase(SessionPhase.THINKING)

                elif btype == "text":
                    text = block.get("text", "")
                    if text:
                        self.last_text_chunk = text
                        await self._set_phase(SessionPhase.GENERATING)
                        if self.on_text_delta:
                            await self.on_text_delta(self.session_id, text)
                        self.output_buffer.append({
                            "role": "assistant",
                            "type": "text",
                            "content": text,
                        })

                elif btype == "tool_use":
                    tool_name = block.get("name", "tool")
                    tool_input = block.get("input", {})
                    tool_id = block.get("id", "")

                    await self._set_phase(SessionPhase.TOOL_EXEC)

                    milestone = _format_milestone(tool_name, tool_input)
                    self.milestones.append(milestone)
                    if len(self.milestones) > 20:
                        self.milestones.pop(0)

                    tool_event = {
                        "session_id": self.session_id,
                        "tool_use_id": tool_id,
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                    if self.on_tool_start:
                        await self.on_tool_start(self.session_id, tool_event)
                    if self.on_tool_done:
                        tool_event["finished_at"] = datetime.now(timezone.utc).isoformat()
                        await self.on_tool_done(self.session_id, tool_event)

                    self.output_buffer.append({
                        "role": "assistant",
                        "type": "tool_use",
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "tool_input": tool_input,
                    })
            return

        # ── User message (tool results from CLI) ─────────────────────────────
        if etype == "user":
            return

        # ── Result — final summary ────────────────────────────────────────────
        if etype == "result":
            if event.get("is_error"):
                err = event.get("result", "Unknown error")
                print(f"[session:{self.session_id[:8]}] CLI error: {err}")
            return

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _set_phase(self, phase: SessionPhase) -> None:
        self.phase = phase
        if self.on_phase_change:
            await self.on_phase_change(self.session_id, phase)
