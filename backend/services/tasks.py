"""Task management service — parse and update TASKS.md files."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .projects import MANAGED_DIR

# Regex: optional whitespace, -, space, [marker], space, text
_TASK_RE = re.compile(r"^(\s*)- \[([ x~])\] (.+)$")

_STATUS_MAP = {" ": "pending", "~": "in_progress", "x": "done"}
_MARKER_MAP = {"pending": " ", "in_progress": "~", "done": "x"}


def _tasks_path(project_name: str) -> Path:
    return MANAGED_DIR / project_name / "TASKS.md"


def get_tasks(project_name: str) -> list[dict[str, Any]]:
    """Parse TASKS.md and return task list."""
    path = _tasks_path(project_name)
    if not path.exists():
        return []

    tasks: list[dict[str, Any]] = []
    idx = 0
    for line in path.read_text("utf-8").splitlines():
        m = _TASK_RE.match(line)
        if m:
            indent = len(m.group(1)) // 2
            marker = m.group(2)
            text = m.group(3).strip()
            tasks.append({
                "index": idx,
                "text": text,
                "status": _STATUS_MAP.get(marker, "pending"),
                "indent": indent,
            })
            idx += 1
    return tasks


def add_task(project_name: str, text: str) -> list[dict[str, Any]]:
    """Append a new pending task to TASKS.md. Returns updated task list."""
    path = _tasks_path(project_name)
    if not path.exists():
        path.write_text("# Tasks\n\n", "utf-8")

    lines = path.read_text("utf-8").splitlines()
    new_line = f"- [ ] {text}"

    # Insert after the last checkbox line, or at end
    last_task_idx = -1
    for i, line in enumerate(lines):
        if _TASK_RE.match(line):
            last_task_idx = i

    insert_at = last_task_idx + 1 if last_task_idx >= 0 else len(lines)
    lines.insert(insert_at, new_line)
    path.write_text("\n".join(lines) + "\n", "utf-8")

    return get_tasks(project_name)


def update_task_status(
    project_name: str, task_index: int, status: str
) -> list[dict[str, Any]]:
    """Update a task's checkbox status. Returns updated task list."""
    if status not in _MARKER_MAP:
        raise ValueError(f"Invalid status: {status}")

    path = _tasks_path(project_name)
    if not path.exists():
        raise ValueError(f"TASKS.md not found for project {project_name}")

    lines = path.read_text("utf-8").splitlines()
    current_idx = 0
    found = False
    for i, line in enumerate(lines):
        m = _TASK_RE.match(line)
        if m:
            if current_idx == task_index:
                indent = m.group(1)
                text = m.group(3)
                lines[i] = f"{indent}- [{_MARKER_MAP[status]}] {text}"
                found = True
                break
            current_idx += 1

    if not found:
        raise ValueError(f"Task index {task_index} not found")

    path.write_text("\n".join(lines) + "\n", "utf-8")
    return get_tasks(project_name)


def delete_task(
    project_name: str, task_index: int
) -> list[dict[str, Any]]:
    """Remove a task line from TASKS.md. Returns updated task list."""
    path = _tasks_path(project_name)
    if not path.exists():
        raise ValueError(f"TASKS.md not found for project {project_name}")

    lines = path.read_text("utf-8").splitlines()
    current_idx = 0
    found = False
    for i, line in enumerate(lines):
        m = _TASK_RE.match(line)
        if m:
            if current_idx == task_index:
                lines.pop(i)
                found = True
                break
            current_idx += 1

    if not found:
        raise ValueError(f"Task index {task_index} not found")

    path.write_text("\n".join(lines) + "\n", "utf-8")
    return get_tasks(project_name)
