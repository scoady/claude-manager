"""OperatorRule — abstract base class for all operator rules."""
from __future__ import annotations

import abc
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..broker.agent_broker import AgentBroker


class OperatorRule(abc.ABC):
    """
    A rule has a condition (check) and an action (fire).

    The RulesEngine calls check() on each reconciliation tick.
    If check() returns True and the rule is cooled down, fire() is called.
    """

    def __init__(
        self,
        rule_id: str,
        name: str,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.rule_id = rule_id
        self.name = name
        self.cooldown_seconds = cooldown_seconds
        self.last_fired_at: datetime | None = None
        self.fire_count: int = 0
        self.enabled: bool = True

    @abc.abstractmethod
    async def check(self, broker: "AgentBroker", projects: list[Any]) -> bool:
        """Return True if this rule's condition is satisfied."""
        ...

    @abc.abstractmethod
    async def fire(self, broker: "AgentBroker", projects: list[Any]) -> None:
        """Execute the rule's action."""
        ...

    def is_cooled_down(self) -> bool:
        if self.last_fired_at is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.last_fired_at).total_seconds()
        return elapsed >= self.cooldown_seconds

    def record_fired(self) -> None:
        self.last_fired_at = datetime.now(timezone.utc)
        self.fire_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "cooldown_seconds": self.cooldown_seconds,
            "last_fired_at": self.last_fired_at.isoformat() if self.last_fired_at else None,
            "fire_count": self.fire_count,
            "enabled": self.enabled,
        }
