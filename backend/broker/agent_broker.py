"""
AgentBroker — singleton that owns all AgentSessions.

Responsibilities:
  - Create / cancel sessions
  - Wire session callbacks → WSManager broadcasts
  - Route inject_message() to the right session
  - Provide the session registry to HTTP routes and the rules engine
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from pathlib import Path

from ..models import SessionPhase, WSMessageType
from ..services import milestones as milestones_svc
from .agent_session import AgentSession

# Default MCP config for controller agents (canvas + orchestrator tools)
_CONTROLLER_MCP_CONFIG = str(
    Path(__file__).resolve().parent.parent / "mcp" / "controller_mcp_config.json"
)

if TYPE_CHECKING:
    from ..ws_manager import WSManager
    from ..services.database import Database

_DEFAULT_MODEL = "claude-opus-4-6"


class AgentBroker:
    def __init__(
        self,
        ws_manager: "WSManager",
        default_model: str = _DEFAULT_MODEL,
        db: "Database | None" = None,
    ) -> None:
        self._ws = ws_manager
        self._default_model = default_model
        self._db = db
        self._sessions: dict[str, AgentSession] = {}

    # ── Session management ────────────────────────────────────────────────────

    def get_controller_for_project(self, project_name: str) -> AgentSession | None:
        """Return the controller session for a project, if one exists."""
        for s in self._sessions.values():
            if s.project_name == project_name and s.is_controller:
                return s
        return None

    async def create_session(
        self,
        project_name: str,
        project_path: str,
        initial_task: str,
        model: str | None = None,
        is_controller: bool = False,
        task_index: int | None = None,
        mcp_config_path: str | None = None,
    ) -> AgentSession:
        session_id = str(uuid.uuid4())

        # Controller agents get the combined MCP config (canvas + orchestrator) by default
        effective_mcp = mcp_config_path
        if effective_mcp is None and is_controller:
            effective_mcp = _CONTROLLER_MCP_CONFIG

        session = AgentSession(
            session_id=session_id,
            project_name=project_name,
            project_path=project_path,
            model=model or self._default_model,
            is_controller=is_controller,
            task_index=task_index,
            mcp_config_path=effective_mcp,
        )

        # Wire callbacks
        session.on_phase_change     = self._on_phase_change
        session.on_text_delta       = self._on_text_delta
        session.on_tool_start       = self._on_tool_start
        session.on_tool_done        = self._on_tool_done
        session.on_turn_done        = self._on_turn_done
        session.on_session_done     = self._on_session_done
        session.on_subagent_spawned = self._on_subagent_spawned
        session.on_subagent_done    = self._on_subagent_done
        session.on_subagent_tasks   = self._on_subagent_tasks

        self._sessions[session_id] = session

        # Broadcast immediately so UI shows the card
        await self._ws.broadcast(WSMessageType.AGENT_SPAWNED, {
            "session_id": session_id,
            "project_name": project_name,
            "project_path": project_path,
            "task": initial_task,
            "started_at": session.started_at,
            "model": session.model,
            "is_controller": is_controller,
            "task_index": session.task_index,
        })

        # Persist session to DB (fire-and-forget)
        if self._db:
            import asyncio
            asyncio.create_task(self._db.save_session(
                session_id=session_id,
                project_name=project_name,
                project_path=project_path,
                task=initial_task,
                model=session.model,
            ))

        session.start(initial_task)
        return session

    async def inject_message(self, session_id: str, message: str) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            return False
        await session.inject_message(message)
        await self._ws.broadcast("injection_ack", {
            "session_id": session_id,
            "phase": session.phase.value,
            "queued": session.phase != SessionPhase.IDLE,
        })
        return True

    async def cancel_session(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if not session:
            return False
        session.cancel()
        await self._ws.broadcast(WSMessageType.AGENT_DONE, {
            "session_id": session_id,
            "project_name": session.project_name,
            "reason": "cancelled",
        })
        return True

    def get_session(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def get_all_sessions(self) -> list[AgentSession]:
        return list(self._sessions.values())

    def get_sessions_for_project(self, project_name: str) -> list[AgentSession]:
        return [s for s in self._sessions.values() if s.project_name == project_name]

    # ── Callbacks → WS broadcasts ─────────────────────────────────────────────

    async def _on_phase_change(self, session_id: str, phase: SessionPhase) -> None:
        session = self._sessions.get(session_id)
        await self._ws.broadcast("session_phase", {
            "session_id": session_id,
            "phase": phase.value,
            "session": session.to_dict() if session else {},
        })

    async def _on_text_delta(self, session_id: str, chunk: str) -> None:
        done = chunk == ""  # empty sentinel = turn done marker
        await self._ws.broadcast(WSMessageType.AGENT_STREAM, {
            "session_id": session_id,
            "chunk": chunk,
            "done": done,
        })

    async def _on_tool_start(self, session_id: str, tool_event: dict[str, Any]) -> None:
        session = self._sessions.get(session_id)
        await self._ws.broadcast("tool_start", {
            "session_id": session_id,
            "tool": tool_event,
            "milestones": session.milestones[-10:] if session else [],
        })

    async def _on_tool_done(self, session_id: str, tool_event: dict[str, Any]) -> None:
        session = self._sessions.get(session_id)
        await self._ws.broadcast("tool_done", {
            "session_id": session_id,
            "tool": tool_event,
            "milestones": session.milestones[-10:] if session else [],
        })
        # Backward-compatible milestone event for existing frontend
        if session:
            milestone = f"{tool_event['tool_name']} · done"
            await self._ws.broadcast("agent_milestone", {
                "session_id": session_id,
                "project_name": session.project_name,
                "milestone": milestone,
                "milestones": session.milestones[-10:],
            })

    async def _on_turn_done(self, session_id: str, turn_count: int) -> None:
        await self._ws.broadcast("turn_done", {
            "session_id": session_id,
            "turn_count": turn_count,
        })

    async def _on_session_done(self, session_id: str, reason: str) -> None:
        session = self._sessions.get(session_id)
        # Controllers stay in registry regardless of reason (persistent brain)
        # Normal sessions stay on idle (for follow-up injections), removed otherwise
        if reason != "idle" and not (session and session.is_controller):
            self._sessions.pop(session_id, None)
        await self._ws.broadcast(WSMessageType.AGENT_DONE, {
            "session_id": session_id,
            "project_name": session.project_name if session else "",
            "reason": reason,
        })

        # Capture milestone on idle completion
        if session and reason == "idle":
            # Skip if controller already had subagent milestone captured this cycle
            should_capture = True
            if session.is_controller and session._subagent_captured_this_cycle:
                should_capture = False

            # Skip if agent's last action was asking the user a question
            if should_capture and session.milestones:
                last_ms = session.milestones[-1].lower()
                if "askuserquestion" in last_ms or "ask_user" in last_ms:
                    should_capture = False

            if should_capture:
                try:
                    summary = ""
                    for msg in reversed(session.output_buffer):
                        if msg.get("type") == "text" and msg.get("content", "").strip():
                            summary = msg["content"]
                            break
                    if summary:
                        duration = None
                        if session._cycle_start_time:
                            try:
                                start = datetime.fromisoformat(session._cycle_start_time)
                                duration = (datetime.now(timezone.utc) - start).total_seconds()
                            except Exception:
                                pass
                        agent_type = "controller" if session.is_controller else "standalone"
                        milestones_svc.add_milestone(
                            project_name=session.project_name,
                            session_id=session_id,
                            task=session.task or "",
                            summary=summary,
                            agent_type=agent_type,
                            model=session.model,
                            duration_seconds=duration,
                        )
                        all_milestones = milestones_svc.get_milestones(session.project_name)
                        await self._ws.broadcast(WSMessageType.MILESTONES_UPDATED, {
                            "project_name": session.project_name,
                            "milestones": all_milestones,
                        })
                except Exception as exc:
                    print(f"[milestone] capture error: {exc}")

            # Reset for next cycle
            session._subagent_captured_this_cycle = False

        # ── Workflow auto-continuation ─────────────────────────────────────
        workflow_injected = False
        if session and session.is_controller and reason == "idle":
            try:
                from ..services import workflows as workflows_svc
                from ..models import WorkflowStatus
                import asyncio

                wf = workflows_svc.get_workflow(session.project_name)
                if wf and wf.status == WorkflowStatus.RUNNING:
                    loop = asyncio.get_event_loop()
                    updated_wf, next_prompt = await loop.run_in_executor(
                        None, workflows_svc.advance_phase, session.project_name
                    )
                    await self._ws.broadcast(WSMessageType.WORKFLOW_UPDATED, {
                        "project_name": session.project_name,
                        "workflow": updated_wf.model_dump() if updated_wf else None,
                    })
                    if next_prompt:
                        workflow_injected = True
                        await asyncio.sleep(1.0)
                        await session.inject_message(next_prompt)
            except Exception as exc:
                print(f"[workflow] auto-continue error: {exc}")

        # ── Task queue auto-dispatch ──────────────────────────────────────
        if session and session.is_controller and reason == "idle" and not workflow_injected:
            try:
                await self.check_task_queue(session.project_name)
            except Exception as exc:
                print(f"[task-queue] auto-dispatch error: {exc}")

        # When a worker finishes: mark its task done, then dispatch next
        if session and not session.is_controller and reason == "idle":
            if session.task_index is not None:
                try:
                    from ..services import tasks as tasks_svc
                    import asyncio
                    loop = asyncio.get_event_loop()

                    # Mark task done
                    await loop.run_in_executor(
                        None, tasks_svc.update_task_status,
                        session.project_name, session.task_index, "done"
                    )
                    updated = await loop.run_in_executor(
                        None, tasks_svc.get_tasks, session.project_name
                    )
                    await self._ws.broadcast(WSMessageType.TASKS_UPDATED, {
                        "project_name": session.project_name,
                        "tasks": updated,
                    })
                    print(f"[task-queue] auto-completed task #{session.task_index}")
                except Exception as exc:
                    print(f"[task-queue] auto-complete error: {exc}")

            # Slot opened — dispatch next pending task
            try:
                await self.check_task_queue(session.project_name)
            except Exception as exc:
                print(f"[task-queue] worker-done dispatch error: {exc}")

        if self._db:
            import asyncio
            asyncio.create_task(self._db.update_session(
                session_id,
                status=reason,
                ended_at=datetime.now(timezone.utc).isoformat(),
            ))

    async def _on_subagent_spawned(
        self, session_id: str, tool_use_id: str, tool_input: dict[str, Any]
    ) -> None:
        session = self._sessions.get(session_id)
        await self._ws.broadcast("subagent_spawned", {
            "session_id": session_id,
            "project_name": session.project_name if session else "",
            "tool_use_id": tool_use_id,
            "description": tool_input.get("description", ""),
            "subagent_type": tool_input.get("subagent_type", "general-purpose"),
        })

    async def _on_subagent_done(
        self, session_id: str, tool_use_id: str, result_text: str, is_error: bool
    ) -> None:
        session = self._sessions.get(session_id)
        await self._ws.broadcast("subagent_done", {
            "session_id": session_id,
            "project_name": session.project_name if session else "",
            "tool_use_id": tool_use_id,
            "result": result_text,
            "is_error": is_error,
        })

        # Capture subagent milestone (skip errors)
        if session and result_text and not is_error:
            try:
                tool_info = session._pending_agent_tools.get(tool_use_id, {})
                task_desc = tool_info.get("tool_input", {}).get("description", "Subagent task")

                duration = None
                if session._cycle_start_time:
                    try:
                        start = datetime.fromisoformat(session._cycle_start_time)
                        duration = (datetime.now(timezone.utc) - start).total_seconds()
                    except Exception:
                        pass

                milestones_svc.add_milestone(
                    project_name=session.project_name,
                    session_id=session_id,
                    task=task_desc,
                    summary=result_text,
                    agent_type="subagent",
                    model=session.model,
                    duration_seconds=duration,
                )
                session._subagent_captured_this_cycle = True

                all_milestones = milestones_svc.get_milestones(session.project_name)
                await self._ws.broadcast(WSMessageType.MILESTONES_UPDATED, {
                    "project_name": session.project_name,
                    "milestones": all_milestones,
                })
            except Exception as exc:
                print(f"[milestone] subagent capture error: {exc}")

    async def _on_subagent_tasks(
        self, session_id: str, tool_use_id: str, todos: list[dict[str, Any]]
    ) -> None:
        session = self._sessions.get(session_id)
        await self._ws.broadcast("subagent_tasks", {
            "session_id": session_id,
            "project_name": session.project_name if session else "",
            "tool_use_id": tool_use_id,
            "todos": todos,
        })

    # ── Task queue auto-dispatch ──────────────────────────────────────────────

    async def check_task_queue(self, project_name: str) -> None:
        """Directly dispatch workers for pending tasks when capacity exists.

        Bypasses the controller's Claude API to avoid latency — the broker
        spawns worker agents directly for each pending task up to the
        parallelism limit.
        """
        import asyncio
        from ..services import tasks as tasks_svc
        from ..services import projects as projects_svc

        loop = asyncio.get_event_loop()

        tasks = await loop.run_in_executor(None, tasks_svc.get_tasks, project_name)
        pending = [t for t in tasks if t["status"] == "pending"]
        if not pending:
            return

        # Count active (non-idle, non-controller) workers
        active_workers = sum(
            1 for s in self._sessions.values()
            if s.project_name == project_name
            and not s.is_controller
            and s.phase not in (SessionPhase.IDLE, SessionPhase.CANCELLED, SessionPhase.ERROR)
        )

        project = await loop.run_in_executor(
            None, projects_svc.get_project, project_name, []
        )
        parallelism = project.config.parallelism if project else 1

        if active_workers >= parallelism:
            return

        slots = parallelism - active_workers
        to_dispatch = pending[:slots]

        for task in to_dispatch:
            task_index = task["index"]
            task_text = task["text"]

            # Mark in-progress
            await loop.run_in_executor(
                None, tasks_svc.update_task_status, project_name, task_index, "in_progress"
            )

            # Broadcast task update
            updated = await loop.run_in_executor(None, tasks_svc.get_tasks, project_name)
            await self._ws.broadcast(WSMessageType.TASKS_UPDATED, {
                "project_name": project_name,
                "tasks": updated,
            })

            # Spawn worker directly
            task_prompt = (
                f'You are a worker agent. Complete this task:\n\n'
                f'"{task_text}"\n\n'
                f'Read PROJECT.md and TASKS.md for context. '
                f'Implement the task fully — write code, run tests, verify your work. '
                f'When done, write a brief summary of what you accomplished.'
            )

            await self.create_session(
                project_name=project_name,
                project_path=project.path,
                initial_task=task_prompt,
                model=project.config.model,
                task_index=task_index,
            )

            print(f"[task-queue] auto-dispatched task #{task_index}: {task_text[:60]}")
