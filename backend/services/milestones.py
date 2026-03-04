"""Milestone service — persist completed work cycle records."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .projects import MANAGED_DIR


def _milestones_path(project_name: str) -> Path:
    return MANAGED_DIR / project_name / ".claude" / "milestones.json"


def _read_milestones(project_name: str) -> list[dict[str, Any]]:
    path = _milestones_path(project_name)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, Exception):
        return []


def _write_milestones(project_name: str, milestones: list[dict[str, Any]]) -> None:
    path = _milestones_path(project_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(milestones, indent=2), "utf-8")


def get_milestones(project_name: str) -> list[dict[str, Any]]:
    """Return all milestones, newest first."""
    milestones = _read_milestones(project_name)
    milestones.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return milestones


def add_milestone(
    project_name: str,
    session_id: str,
    task: str,
    summary: str,
    agent_type: str = "standalone",
    model: str | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Append a milestone and return it."""
    milestone = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "task": task[:500],
        "summary": summary[:5000],
        "agent_type": agent_type,
        "model": model,
        "duration_seconds": duration_seconds,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    milestones = _read_milestones(project_name)
    milestones.append(milestone)
    _write_milestones(project_name, milestones)
    return milestone


def delete_milestone(project_name: str, milestone_id: str) -> list[dict[str, Any]]:
    """Remove a milestone by ID. Returns updated list (newest first)."""
    milestones = _read_milestones(project_name)
    milestones = [m for m in milestones if m.get("id") != milestone_id]
    _write_milestones(project_name, milestones)
    return get_milestones(project_name)


def clear_milestones(project_name: str) -> list[dict[str, Any]]:
    """Remove all milestones for a project."""
    _write_milestones(project_name, [])
    return []
