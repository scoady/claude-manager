"""Metrics collection service — time-series aggregation of agent/task/project state."""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Cost estimation constants (same as c9s Go TUI) ───────────────────────────

AVG_INPUT_TOKENS_PER_TURN = 2000
AVG_OUTPUT_TOKENS_PER_TURN = 800

PRICING_TABLE: dict[str, tuple[float, float]] = {
    # (input_per_mtok, output_per_mtok)
    "sonnet":           (3.0, 15.0),
    "claude-sonnet-4":  (3.0, 15.0),
    "claude-sonnet":    (3.0, 15.0),
    "opus":             (15.0, 75.0),
    "claude-opus-4":    (15.0, 75.0),
    "claude-opus":      (15.0, 75.0),
    "haiku":            (0.80, 4.0),
    "claude-haiku-3.5": (0.80, 4.0),
    "claude-haiku":     (0.80, 4.0),
}

DEFAULT_PRICING = (3.0, 15.0)  # assume sonnet


def _lookup_pricing(model: str) -> tuple[float, float]:
    if not model:
        return DEFAULT_PRICING
    m = model.lower()
    if m in PRICING_TABLE:
        return PRICING_TABLE[m]
    for key, p in PRICING_TABLE.items():
        if key in m:
            return p
    if "opus" in m:
        return PRICING_TABLE["opus"]
    if "haiku" in m:
        return PRICING_TABLE["haiku"]
    return DEFAULT_PRICING


def estimate_cost(model: str, turn_count: int) -> float:
    if turn_count <= 0:
        return 0.0
    inp_per_mtok, out_per_mtok = _lookup_pricing(model)
    inp_tokens = turn_count * AVG_INPUT_TOKENS_PER_TURN
    out_tokens = turn_count * AVG_OUTPUT_TOKENS_PER_TURN
    return (inp_tokens / 1_000_000) * inp_per_mtok + (out_tokens / 1_000_000) * out_per_mtok


# ── Duration parsing ─────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^(\d+)(m|h|d)$")


def parse_since(since: str) -> datetime:
    """Parse a duration string like '15m', '1h', '6h', '24h', '7d' into a datetime."""
    match = _DURATION_RE.match(since.strip())
    if not match:
        # Default to 1 hour
        return datetime.now(timezone.utc).replace(microsecond=0) - _td(hours=1)
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return datetime.now(timezone.utc) - _td(minutes=value)
    elif unit == "h":
        return datetime.now(timezone.utc) - _td(hours=value)
    elif unit == "d":
        return datetime.now(timezone.utc) - _td(days=value)
    return datetime.now(timezone.utc) - _td(hours=1)


def _td(**kwargs) -> Any:
    from datetime import timedelta
    return timedelta(**kwargs)


# ── Resolution helpers ───────────────────────────────────────────────────────

def _resolution_seconds(resolution: str) -> int:
    match = _DURATION_RE.match(resolution.strip())
    if not match:
        return 60
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return value * 60
    elif unit == "h":
        return value * 3600
    elif unit == "d":
        return value * 86400
    return 60


def _bucket_key(ts: datetime, res_seconds: int) -> str:
    epoch = ts.timestamp()
    bucketed = int(epoch // res_seconds) * res_seconds
    return datetime.fromtimestamp(bucketed, tz=timezone.utc).isoformat()


# ── Snapshot dataclass ───────────────────────────────────────────────────────


@dataclass
class Snapshot:
    timestamp: str  # ISO-8601
    active: int = 0
    idle: int = 0
    done: int = 0
    error: int = 0
    total_turns: int = 0
    cumulative_cost: float = 0.0
    tasks_started: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    model_counts: dict[str, int] = field(default_factory=dict)
    project_agents: dict[str, int] = field(default_factory=dict)
    project_turns: dict[str, int] = field(default_factory=dict)


# ── MetricsService ───────────────────────────────────────────────────────────


class MetricsService:
    """Collects periodic snapshots and provides time-series query methods."""

    def __init__(self, maxlen: int = 2880) -> None:
        # 2880 snapshots = 24 hours at 30-second intervals
        self.snapshots: deque[Snapshot] = deque(maxlen=maxlen)
        self._total_agents_spawned: int = 0
        self._total_tasks_started: int = 0
        self._total_tasks_completed: int = 0
        self._total_tasks_failed: int = 0
        self._start_time: float = time.time()

    # ── Snapshot collection ──────────────────────────────────────────────────

    def snapshot(self, sessions: list, tasks_by_project: dict[str, list[dict]] | None = None) -> None:
        """
        Record a point-in-time snapshot.

        sessions: list of AgentSession objects (from broker.get_all_sessions())
        tasks_by_project: optional {project_name: [task_dict, ...]} for task throughput
        """
        now = datetime.now(timezone.utc).isoformat()

        active = 0
        idle = 0
        done = 0
        error = 0
        total_turns = 0
        cumulative_cost = 0.0
        model_counts: dict[str, int] = {}
        project_agents: dict[str, int] = {}
        project_turns: dict[str, int] = {}

        for s in sessions:
            phase = s.phase.value if hasattr(s.phase, "value") else str(s.phase)
            if phase in ("idle",):
                idle += 1
            elif phase in ("cancelled", "error"):
                if phase == "error":
                    error += 1
                else:
                    done += 1
            else:
                active += 1

            total_turns += s.turn_count
            cumulative_cost += estimate_cost(s.model, s.turn_count)

            model_key = s.model or "unknown"
            model_counts[model_key] = model_counts.get(model_key, 0) + 1

            project_agents[s.project_name] = project_agents.get(s.project_name, 0) + 1
            project_turns[s.project_name] = project_turns.get(s.project_name, 0) + s.turn_count

        # Task throughput — count statuses across all projects
        tasks_started = 0
        tasks_completed = 0
        tasks_failed = 0
        if tasks_by_project:
            for _proj, task_list in tasks_by_project.items():
                for t in task_list:
                    status = t.get("status", "pending")
                    if status == "in_progress":
                        tasks_started += 1
                    elif status == "done":
                        tasks_completed += 1
                    elif status == "failed":
                        tasks_failed += 1

        snap = Snapshot(
            timestamp=now,
            active=active,
            idle=idle,
            done=done,
            error=error,
            total_turns=total_turns,
            cumulative_cost=cumulative_cost,
            tasks_started=tasks_started,
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
            model_counts=model_counts,
            project_agents=project_agents,
            project_turns=project_turns,
        )
        self.snapshots.append(snap)

    def record_agent_spawned(self) -> None:
        self._total_agents_spawned += 1

    def record_task_started(self) -> None:
        self._total_tasks_started += 1

    def record_task_completed(self) -> None:
        self._total_tasks_completed += 1

    def record_task_failed(self) -> None:
        self._total_tasks_failed += 1

    # ── Query methods ────────────────────────────────────────────────────────

    def _filter_snapshots(self, since: datetime) -> list[Snapshot]:
        since_iso = since.isoformat()
        return [s for s in self.snapshots if s.timestamp >= since_iso]

    def _aggregate(
        self,
        snaps: list[Snapshot],
        resolution: str,
        extractor,
    ) -> list[dict]:
        res_sec = _resolution_seconds(resolution)
        buckets: dict[str, list] = {}
        for s in snaps:
            ts = datetime.fromisoformat(s.timestamp)
            key = _bucket_key(ts, res_sec)
            buckets.setdefault(key, []).append(s)

        result = []
        for bkey in sorted(buckets.keys()):
            group = buckets[bkey]
            result.append(extractor(bkey, group))
        return result

    def get_agent_activity(self, since: str = "1h", resolution: str = "1m") -> list[dict]:
        """Time-series: {time, active, idle, done, error} per interval."""
        snaps = self._filter_snapshots(parse_since(since))

        def extract(t: str, group: list[Snapshot]) -> dict:
            # Use the last snapshot in the bucket (most recent state)
            last = group[-1]
            return {
                "time": t,
                "active": last.active,
                "idle": last.idle,
                "done": last.done,
                "error": last.error,
            }

        return self._aggregate(snaps, resolution, extract)

    def get_cost_series(self, since: str = "1h", resolution: str = "1m") -> list[dict]:
        """Time-series: {time, cumulative_cost, incremental_cost} per interval."""
        snaps = self._filter_snapshots(parse_since(since))

        def extract(t: str, group: list[Snapshot]) -> dict:
            last = group[-1]
            first = group[0]
            return {
                "time": t,
                "cumulative": round(last.cumulative_cost, 4),
                "incremental": round(max(0, last.cumulative_cost - first.cumulative_cost), 4),
            }

        result = self._aggregate(snaps, resolution, extract)
        # Compute incremental across buckets
        if len(result) > 1:
            for i in range(len(result) - 1, 0, -1):
                result[i]["incremental"] = round(
                    max(0, result[i]["cumulative"] - result[i - 1]["cumulative"]), 4
                )
        return result

    def get_task_throughput(self, since: str = "1h", resolution: str = "1m") -> list[dict]:
        """Time-series: {time, started, completed, failed} per interval."""
        snaps = self._filter_snapshots(parse_since(since))

        def extract(t: str, group: list[Snapshot]) -> dict:
            last = group[-1]
            return {
                "time": t,
                "started": last.tasks_started,
                "completed": last.tasks_completed,
                "failed": last.tasks_failed,
            }

        return self._aggregate(snaps, resolution, extract)

    def get_model_usage(self, since: str = "24h") -> list[dict]:
        """Returns: [{model, count, total_turns, estimated_cost}]."""
        snaps = self._filter_snapshots(parse_since(since))
        if not snaps:
            return []

        # Use the latest snapshot's model counts as the current state
        last = snaps[-1]
        result = []
        for model, count in last.model_counts.items():
            # Estimate turns per model — approximate from project turns
            avg_turns_per_agent = last.total_turns / max(1, sum(last.model_counts.values()))
            est_turns = int(avg_turns_per_agent * count)
            result.append({
                "model": model,
                "count": count,
                "total_turns": est_turns,
                "estimated_cost": round(estimate_cost(model, est_turns), 4),
            })
        return sorted(result, key=lambda x: x["estimated_cost"], reverse=True)

    def get_project_activity(self, since: str = "1h", resolution: str = "5m") -> dict[str, list[dict]]:
        """Returns: {project: [{time, agent_count, turn_count}]}."""
        snaps = self._filter_snapshots(parse_since(since))

        # Collect all project names
        all_projects: set[str] = set()
        for s in snaps:
            all_projects.update(s.project_agents.keys())

        result: dict[str, list[dict]] = {}
        res_sec = _resolution_seconds(resolution)

        for project in all_projects:
            buckets: dict[str, list[Snapshot]] = {}
            for s in snaps:
                ts = datetime.fromisoformat(s.timestamp)
                key = _bucket_key(ts, res_sec)
                buckets.setdefault(key, []).append(s)

            series = []
            for bkey in sorted(buckets.keys()):
                group = buckets[bkey]
                last = group[-1]
                series.append({
                    "time": bkey,
                    "agent_count": last.project_agents.get(project, 0),
                    "turn_count": last.project_turns.get(project, 0),
                })
            result[project] = series

        return result

    def get_system_health(self, ws_connections: int = 0) -> dict:
        """Returns current system health stats."""
        uptime = time.time() - self._start_time
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        if hours > 0:
            uptime_str = f"{hours}h{minutes}m"
        else:
            uptime_str = f"{minutes}m"

        last = self.snapshots[-1] if self.snapshots else None
        return {
            "uptime": uptime_str,
            "uptime_seconds": round(uptime, 1),
            "total_agents_spawned": self._total_agents_spawned,
            "active_agents": last.active if last else 0,
            "idle_agents": last.idle if last else 0,
            "error_agents": last.error if last else 0,
            "active_ws_connections": ws_connections,
            "total_turns": last.total_turns if last else 0,
            "cumulative_cost": round(last.cumulative_cost, 4) if last else 0.0,
            "snapshot_count": len(self.snapshots),
        }

    def get_summary(self, ws_connections: int = 0) -> dict:
        """Quick summary: total agents today, total cost, uptime, etc."""
        health = self.get_system_health(ws_connections)
        last = self.snapshots[-1] if self.snapshots else None
        return {
            "uptime": health["uptime"],
            "uptime_seconds": health["uptime_seconds"],
            "total_agents_spawned": health["total_agents_spawned"],
            "active_agents": health["active_agents"],
            "idle_agents": health["idle_agents"],
            "total_turns": health["total_turns"],
            "cumulative_cost": health["cumulative_cost"],
            "active_ws_connections": health["active_ws_connections"],
            "total_tasks_started": self._total_tasks_started,
            "total_tasks_completed": self._total_tasks_completed,
            "total_tasks_failed": self._total_tasks_failed,
            "model_breakdown": last.model_counts if last else {},
            "project_agent_counts": last.project_agents if last else {},
        }


# ── Singleton ────────────────────────────────────────────────────────────────

metrics_service = MetricsService()
