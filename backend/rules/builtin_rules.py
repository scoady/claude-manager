"""Built-in operator rules."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from .operator_rule import OperatorRule

if TYPE_CHECKING:
    from ..broker.agent_broker import AgentBroker


class ProjectAutoSpawnRule(OperatorRule):
    """
    When a managed project has fewer than max_sessions active sessions, spawn one.
    Useful for "always-on" projects that should always have an agent running.
    """

    def __init__(
        self,
        rule_id: str,
        name: str,
        project_name: str,
        task: str,
        model: str | None = None,
        max_sessions: int = 1,
        **kw: Any,
    ) -> None:
        super().__init__(rule_id, name, **kw)
        self.project_name = project_name
        self.task = task
        self.model = model
        self.max_sessions = max_sessions

    async def check(self, broker: "AgentBroker", projects: list[Any]) -> bool:
        from ..models import SessionPhase
        sessions = broker.get_sessions_for_project(self.project_name)
        active = [
            s for s in sessions
            if s.phase not in (SessionPhase.CANCELLED, SessionPhase.ERROR)
        ]
        return len(active) < self.max_sessions

    async def fire(self, broker: "AgentBroker", projects: list[Any]) -> None:
        project = next((p for p in projects if p.name == self.project_name), None)
        if not project:
            return
        await broker.create_session(
            project_name=self.project_name,
            project_path=project.path,
            initial_task=self.task,
            model=self.model,
        )


class DirectoryWatchRule(OperatorRule):
    """
    When files change in a watched directory, inject a message into all active
    sessions for the associated project.
    """

    def __init__(
        self,
        rule_id: str,
        name: str,
        watch_path: str,
        project_name: str,
        message_template: str = "Files changed:\n{changed_files}",
        **kw: Any,
    ) -> None:
        super().__init__(rule_id, name, **kw)
        self.watch_path = Path(watch_path)
        self.project_name = project_name
        self.message_template = message_template
        self._last_mtime: float = 0.0
        self._changed: list[str] = []

    async def check(self, broker: "AgentBroker", projects: list[Any]) -> bool:
        import asyncio
        loop = asyncio.get_event_loop()
        self._changed = await loop.run_in_executor(None, self._scan)
        return len(self._changed) > 0

    def _scan(self) -> list[str]:
        if not self.watch_path.exists():
            return []
        changed: list[str] = []
        new_max = self._last_mtime
        for f in self.watch_path.rglob("*"):
            if f.is_file():
                mtime = f.stat().st_mtime
                if mtime > self._last_mtime:
                    changed.append(str(f))
                if mtime > new_max:
                    new_max = mtime
        self._last_mtime = new_max
        return changed

    async def fire(self, broker: "AgentBroker", projects: list[Any]) -> None:
        sessions = broker.get_sessions_for_project(self.project_name)
        if not sessions:
            return
        msg = self.message_template.format(
            changed_files="\n".join(self._changed[:20])
        )
        for session in sessions:
            await session.inject_message(msg)


class SessionHealthRule(OperatorRule):
    """Cancel sessions that have been stuck in ERROR phase."""

    def __init__(
        self,
        rule_id: str,
        name: str,
        error_timeout_seconds: float = 120.0,
        **kw: Any,
    ) -> None:
        super().__init__(rule_id, name, **kw)
        self.error_timeout = error_timeout_seconds
        self._stuck: list[str] = []

    async def check(self, broker: "AgentBroker", projects: list[Any]) -> bool:
        from ..models import SessionPhase
        self._stuck = [
            s.session_id
            for s in broker.get_all_sessions()
            if s.phase == SessionPhase.ERROR
        ]
        return len(self._stuck) > 0

    async def fire(self, broker: "AgentBroker", projects: list[Any]) -> None:
        for sid in self._stuck:
            await broker.cancel_session(sid)
