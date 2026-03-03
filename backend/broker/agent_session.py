"""
AgentSession — one continuous conversation with the Anthropic API.

Lifecycle:
  created → start(task) → _run_loop [blocked on queue] → _execute_turn [tool loop]
           → IDLE [blocked on queue] → next injection → ... → cancelled | error

Injection at any time:
  - If IDLE (blocked on queue.get): unblocks immediately, starts a new turn
  - If mid-turn (GENERATING/TOOL_EXEC): queued, delivered after current turn ends
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import anthropic

from ..models import SessionPhase
from .tool_executor import ToolExecutor
from .history_store import HistoryStore


def _format_milestone(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Build a short label from a tool call for the milestones list."""
    if tool_name in ("Read", "Write", "Edit"):
        key = tool_input.get("file_path", "")
        key = key.split("/")[-1] if key else ""
    elif tool_name == "Bash":
        key = tool_input.get("command", "")[:60].split("\n")[0]
    elif tool_name == "Grep":
        key = tool_input.get("pattern", "")[:40]
    elif tool_name == "Glob":
        key = tool_input.get("pattern", "")[:40]
    else:
        key = ""
    return f"{tool_name} · {key}" if key else tool_name


@dataclass
class AgentSession:
    session_id: str
    project_name: str
    project_path: str
    model: str
    system_prompt: str
    tool_executor: ToolExecutor
    history_store: HistoryStore

    # Runtime state
    phase: SessionPhase = SessionPhase.STARTING
    milestones: list[str] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    turn_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    last_text_chunk: str | None = None

    # Callbacks — set by AgentBroker after construction
    on_phase_change: Callable | None = field(default=None, repr=False)
    on_text_delta:   Callable | None = field(default=None, repr=False)
    on_tool_start:   Callable | None = field(default=None, repr=False)
    on_tool_done:    Callable | None = field(default=None, repr=False)
    on_turn_done:    Callable | None = field(default=None, repr=False)
    on_session_done: Callable | None = field(default=None, repr=False)

    # Internal
    _client: anthropic.AsyncAnthropic = field(init=False, repr=False)
    _injection_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=64), repr=False
    )
    _cancel_event: asyncio.Event = field(
        default_factory=asyncio.Event, repr=False
    )
    _run_task: asyncio.Task | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._client = anthropic.AsyncAnthropic()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, initial_task: str) -> asyncio.Task:
        """Enqueue the initial task and launch the run loop as a background task."""
        self._injection_queue.put_nowait({"role": "user", "content": initial_task})
        self._run_task = asyncio.create_task(
            self._run_loop(), name=f"agent-{self.session_id[:8]}"
        )
        return self._run_task

    async def inject_message(self, message: str) -> None:
        """
        Inject a user message.
        - IDLE: unblocks queue.get() immediately → new turn starts
        - Mid-turn: queued → delivered after current turn
        """
        await self._injection_queue.put({"role": "user", "content": message})

    def cancel(self) -> None:
        self._cancel_event.set()
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()

    def to_dict(self) -> dict[str, Any]:
        from ..models import AgentStatus
        # Map SessionPhase → legacy AgentStatus for frontend compatibility
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
            "status": phase_to_status.get(self.phase, AgentStatus.IDLE).value,
            "phase": self.phase.value,
            "model": self.model,
            "started_at": self.started_at,
            "turn_count": self.turn_count,
            "milestones": self.milestones[-10:],
            "last_chunk": self.last_text_chunk,
            "has_pending_injection": not self._injection_queue.empty(),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }

    # ── Run loop ──────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """
        Main loop. Blocked on the injection queue when IDLE.
        Each item dequeued → one full turn (stream + tool loop).
        """
        try:
            while not self._cancel_event.is_set():
                # Wait for a user message — this is the IDLE pause point
                try:
                    user_msg = await self._injection_queue.get()
                except asyncio.CancelledError:
                    break

                if self._cancel_event.is_set():
                    break

                self.history_store.append_user(self.session_id, user_msg)
                await self._execute_turn()
                self.turn_count += 1

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[session:{self.session_id[:8]}] unhandled error: {exc}")
            self.phase = SessionPhase.ERROR
            if self.on_phase_change:
                await self.on_phase_change(self.session_id, self.phase)

        if not self._cancel_event.is_set():
            # Natural completion (queue drained and no more tasks)
            self.phase = SessionPhase.IDLE
        else:
            self.phase = SessionPhase.CANCELLED

        if self.on_session_done:
            await self.on_session_done(self.session_id, self.phase.value)

    # ── Turn execution (agentic tool loop) ───────────────────────────────────

    async def _execute_turn(self) -> None:
        """
        One assistant turn: stream → collect tools → execute → append results → loop.
        Runs until stop_reason is "end_turn" (no more tool calls).
        """
        while True:
            messages = self.history_store.get_messages(self.session_id)

            await self._set_phase(SessionPhase.GENERATING)

            tool_calls: list[dict[str, Any]] = []
            stop_reason = "end_turn"

            # ── Stream one API call ──────────────────────────────────────────
            try:
                async with self._client.messages.stream(
                    model=self.model,
                    system=self.system_prompt,
                    messages=messages,
                    tools=self.tool_executor.tool_schemas(),
                    max_tokens=8192,
                ) as stream:
                    async for event in stream:
                        if self._cancel_event.is_set():
                            return
                        await self._handle_event(event, tool_calls)

                    final_msg = await stream.get_final_message()
                    stop_reason = final_msg.stop_reason or "end_turn"
                    if final_msg.usage:
                        self.total_input_tokens += final_msg.usage.input_tokens
                        self.total_output_tokens += final_msg.usage.output_tokens

            except anthropic.APIError as exc:
                print(f"[session:{self.session_id[:8]}] API error: {exc}")
                self.phase = SessionPhase.ERROR
                if self.on_phase_change:
                    await self.on_phase_change(self.session_id, self.phase)
                return

            # ── Append assistant response ────────────────────────────────────
            self.history_store.append_assistant(self.session_id, final_msg.content)

            # ── Broadcast turn stream-done marker ────────────────────────────
            if self.on_text_delta:
                await self.on_text_delta(self.session_id, "")  # sentinel for done=True

            if self.on_turn_done:
                await self.on_turn_done(self.session_id, self.turn_count)

            # ── Tool execution ───────────────────────────────────────────────
            if stop_reason == "tool_use" and tool_calls:
                tool_results = await self._execute_tools(tool_calls)
                self.history_store.append_tool_results(self.session_id, tool_results)
                continue  # loop: next API call with updated history

            # ── End of turn ──────────────────────────────────────────────────
            break

        await self._set_phase(SessionPhase.IDLE)

    async def _handle_event(
        self, event: Any, tool_calls: list[dict[str, Any]]
    ) -> None:
        etype = event.type

        if etype == "content_block_start":
            block = event.content_block
            if block.type == "tool_use":
                await self._set_phase(SessionPhase.TOOL_INPUT)
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": {},
                    "_buf": "",
                })
            elif block.type == "thinking":
                await self._set_phase(SessionPhase.THINKING)

        elif etype == "content_block_delta":
            delta = event.delta
            if delta.type == "text_delta" and delta.text:
                self.last_text_chunk = delta.text
                if self.on_text_delta:
                    await self.on_text_delta(self.session_id, delta.text)
            elif delta.type == "input_json_delta" and tool_calls:
                tool_calls[-1]["_buf"] += delta.partial_json

        elif etype == "content_block_stop":
            # Finalise the last tool_use block's accumulated JSON
            if tool_calls and "_buf" in tool_calls[-1]:
                buf = tool_calls[-1].pop("_buf", "")
                try:
                    tool_calls[-1]["input"] = json.loads(buf) if buf else {}
                except json.JSONDecodeError:
                    tool_calls[-1]["input"] = {}

    async def _execute_tools(
        self, tool_calls: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Execute all tool calls, emit tool_start/done events, return results."""
        await self._set_phase(SessionPhase.TOOL_EXEC)

        results = []
        for tc in tool_calls:
            # Emit tool_start
            tool_event = {
                "session_id": self.session_id,
                "tool_use_id": tc["id"],
                "tool_name": tc["name"],
                "tool_input": tc["input"],
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            milestone = _format_milestone(tc["name"], tc["input"])
            self.milestones.append(milestone)
            if len(self.milestones) > 20:
                self.milestones.pop(0)

            if self.on_tool_start:
                await self.on_tool_start(self.session_id, tool_event)

            # Execute
            try:
                output = await self.tool_executor.execute(
                    tc["name"], tc["input"], self.project_path
                )
            except Exception as exc:
                output = f"Tool error: {exc}"

            # Emit tool_done
            tool_event["tool_output"] = output
            tool_event["finished_at"] = datetime.now(timezone.utc).isoformat()
            if self.on_tool_done:
                await self.on_tool_done(self.session_id, tool_event)

            results.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": output,
            })

        return results

    async def _set_phase(self, phase: SessionPhase) -> None:
        self.phase = phase
        if self.on_phase_change:
            await self.on_phase_change(self.session_id, phase)
