"""Canvas service — in-memory widget store with JSON persistence."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import WidgetCreate, WidgetState, WidgetUpdate

# Storage dir: ~/.claude/canvas/
_CANVAS_DIR = Path.home() / ".claude" / "canvas"


def _ensure_dir() -> None:
    _CANVAS_DIR.mkdir(parents=True, exist_ok=True)


def _project_file(project: str) -> Path:
    return _CANVAS_DIR / f"{project}.json"


def _serialize(widgets: dict[str, WidgetState]) -> list[dict[str, Any]]:
    return [w.model_dump(mode="json") for w in widgets.values()]


def _load_project(project: str) -> dict[str, WidgetState]:
    path = _project_file(project)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text("utf-8"))
        result: dict[str, WidgetState] = {}
        for item in raw:
            w = WidgetState(**item)
            result[w.id] = w
        return result
    except Exception as exc:
        print(f"[canvas] failed to load {path}: {exc}")
        return {}


class CanvasService:
    """In-memory canvas widget store, persisted to ~/.claude/canvas/<project>.json."""

    def __init__(self) -> None:
        _ensure_dir()
        # project -> {widget_id -> WidgetState}
        self._widgets: dict[str, dict[str, WidgetState]] = {}
        self._load_all()

    # ── private ──────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Load all existing project JSON files on startup."""
        _ensure_dir()
        for json_file in _CANVAS_DIR.glob("*.json"):
            project = json_file.stem
            self._widgets[project] = _load_project(project)

    def _save(self, project: str) -> None:
        """Persist a project's widgets to disk."""
        _ensure_dir()
        widgets = self._widgets.get(project, {})
        try:
            _project_file(project).write_text(
                json.dumps(_serialize(widgets), indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[canvas] failed to save project '{project}': {exc}")

    def _project_store(self, project: str) -> dict[str, WidgetState]:
        if project not in self._widgets:
            self._widgets[project] = {}
        return self._widgets[project]

    # ── public API ───────────────────────────────────────────────────────────

    def get_widgets(self, project: str) -> list[WidgetState]:
        """Return all widgets for a project, sorted by (grid_row, grid_col)."""
        store = self._project_store(project)
        return sorted(store.values(), key=lambda w: (w.grid_row, w.grid_col))

    def upsert_widget(
        self,
        project: str,
        widget_id: str,
        data: WidgetCreate | WidgetUpdate,
    ) -> WidgetState:
        """Create or update a widget.

        The provided ``widget_id`` is always used as the widget's ID — if
        a caller (e.g. the MCP canvas_put tool) supplies an ID it is preserved
        rather than replaced with a new UUID.  This allows the MCP tool to issue
        deterministic IDs so that repeated calls to canvas_put with the same
        widget_id reliably update the existing widget.
        """
        store = self._project_store(project)
        now = datetime.utcnow()

        if widget_id in store:
            existing = store[widget_id]
            # Apply only the non-None fields from data
            update_fields = data.model_dump(exclude_none=True)
            updated = existing.model_copy(update={**update_fields, "updated_at": now})
            store[widget_id] = updated
        else:
            # Create new widget — use the caller-supplied widget_id as the id.
            create_fields = data.model_dump(exclude_none=True)
            widget = WidgetState(
                id=widget_id,
                project=project,
                created_at=now,
                updated_at=now,
                **create_fields,
            )
            store[widget_id] = widget

        self._save(project)
        return store[widget_id]

    def delete_widget(self, project: str, widget_id: str) -> bool:
        """Delete a widget by ID. Returns True if it existed."""
        store = self._project_store(project)
        if widget_id not in store:
            return False
        del store[widget_id]
        self._save(project)
        return True

    def replace_scene(self, project: str, widgets: list[WidgetCreate]) -> list[WidgetState]:
        """Atomically replace all widgets for a project with a new set."""
        now = datetime.utcnow()
        new_store: dict[str, WidgetState] = {}
        for item in widgets:
            widget_id = str(uuid.uuid4())
            new_store[widget_id] = WidgetState(
                id=widget_id,
                project=project,
                created_at=now,
                updated_at=now,
                **item.model_dump(),
            )
        self._widgets[project] = new_store
        self._save(project)
        return list(new_store.values())

    def clear(self, project: str) -> None:
        """Remove all widgets for a project."""
        self._widgets[project] = {}
        self._save(project)


# Module-level singleton
canvas_service = CanvasService()
