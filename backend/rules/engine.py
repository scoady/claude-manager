"""
RulesEngine — the operator reconciliation loop.

Each tick (default 30s):
  1. Load current project list
  2. For each enabled, cooled-down rule: call check()
  3. If check() → True: call fire(), record cooldown, broadcast rule_fired
"""
from __future__ import annotations

import asyncio
import traceback
from typing import TYPE_CHECKING

from .operator_rule import OperatorRule

if TYPE_CHECKING:
    from ..broker.agent_broker import AgentBroker
    from ..ws_manager import WSManager


class RulesEngine:
    def __init__(
        self,
        broker: "AgentBroker",
        ws_manager: "WSManager",
        tick_interval: float = 30.0,
    ) -> None:
        self._broker = broker
        self._ws = ws_manager
        self._tick_interval = tick_interval
        self._rules: dict[str, OperatorRule] = {}
        self._task: asyncio.Task | None = None

    def register(self, rule: OperatorRule) -> None:
        self._rules[rule.rule_id] = rule

    def unregister(self, rule_id: str) -> None:
        self._rules.pop(rule_id, None)

    def get_rules(self) -> list[OperatorRule]:
        return list(self._rules.values())

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(
            self._reconcile_loop(), name="rules-engine"
        )
        return self._task

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick_interval)
            await self._tick()

    async def _tick(self) -> None:
        from ..services import projects as projects_svc

        try:
            loop = asyncio.get_event_loop()
            projects = await loop.run_in_executor(None, projects_svc.list_projects)

            for rule in list(self._rules.values()):
                if not rule.enabled or not rule.is_cooled_down():
                    continue
                try:
                    if await rule.check(self._broker, projects):
                        await rule.fire(self._broker, projects)
                        rule.record_fired()
                        await self._ws.broadcast("rule_fired", rule.to_dict())
                except Exception as exc:
                    print(f"[rules] rule '{rule.name}' error: {exc}")
                    traceback.print_exc()

        except Exception as exc:
            print(f"[rules] tick error: {exc}")
