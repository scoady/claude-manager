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

from ..models import SessionPhase, WSMessageType
from .agent_session import AgentSession

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
    ) -> AgentSession:
        session_id = str(uuid.uuid4())

        session = AgentSession(
            session_id=session_id,
            project_name=project_name,
            project_path=project_path,
            model=model or self._default_model,
            is_controller=is_controller,
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
