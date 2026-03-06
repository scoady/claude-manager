"""Canvas service — in-memory widget store with JSON persistence."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import WidgetCreate, WidgetState, WidgetUpdate
from .widget_catalog import render_template as _render_catalog_template

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

        If ``template_id`` and ``template_data`` are provided, the widget's
        html/css/js are rendered from the catalog template.  The raw
        template_id + template_data are persisted so the widget can be
        re-rendered with fresh data on subsequent updates.
        """
        store = self._project_store(project)
        now = datetime.utcnow()

        fields = data.model_dump(exclude_none=True)

        # Auto-render from template if template_id + template_data are set
        tmpl_id = fields.get("template_id")
        tmpl_data = fields.get("template_data")
        if tmpl_id and tmpl_data:
            result = _render_catalog_template(tmpl_id, tmpl_data)
            if result:
                html, css, js = result
                fields["html"] = html
                fields["css"] = css
                fields["js"] = js

        if widget_id in store:
            existing = store[widget_id]
            # On update: inherit template_id from existing if not overridden,
            # so agents only need to push new template_data
            if "template_id" not in fields and existing.template_id:
                fields["template_id"] = existing.template_id
                if "template_data" in fields and existing.template_id:
                    result = _render_catalog_template(existing.template_id, fields["template_data"])
                    if result:
                        html, css, js = result
                        fields["html"] = html
                        fields["css"] = css
                        fields["js"] = js
            updated = existing.model_copy(update={**fields, "updated_at": now})
            store[widget_id] = updated
        else:
            # Create new widget
            fields.pop("id", None)
            fields.pop("project", None)
            widget = WidgetState(
                id=widget_id,
                project=project,
                created_at=now,
                updated_at=now,
                **fields,
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

    def save_layout(self, project: str, items: list[dict]) -> None:
        """Persist GridStack layout positions (gs_x, gs_y, gs_w, gs_h) for widgets."""
        store = self._project_store(project)
        for item in items:
            wid = item.get("id")
            if wid and wid in store:
                w = store[wid]
                store[wid] = w.model_copy(update={
                    "gs_x": item.get("x"),
                    "gs_y": item.get("y"),
                    "gs_w": item.get("w", w.gs_w),
                    "gs_h": item.get("h", w.gs_h),
                })
        self._save(project)

    def get_dashboard_contract(self, project: str) -> dict[str, Any] | None:
        """Build a data contract from current widgets for dashboard data requests.

        Returns a dict with widget layouts and data field schemas that agents
        can fill with structured data responses.
        """
        widgets = self.get_widgets(project)
        if not widgets:
            return None

        contract: dict[str, Any] = {"widgets": []}
        for w in widgets:
            entry: dict[str, Any] = {
                "widget_id": w.id,
                "title": w.title,
                "col_span": w.col_span,
                "row_span": w.row_span,
            }
            if w.template_id and w.template_data:
                entry["template_id"] = w.template_id
                entry["data_fields"] = {k: type(v).__name__ for k, v in w.template_data.items()}
                entry["sample_data"] = w.template_data
            elif w.js and "{{" not in w.html:
                # Raw widget — extract data placeholders from JS if possible
                entry["type"] = "raw_widget"
                entry["description"] = f"Custom widget: {w.title}"
            contract["widgets"].append(entry)

        return contract if contract["widgets"] else None

    def clear(self, project: str) -> None:
        """Remove all widgets for a project."""
        self._widgets[project] = {}
        self._save(project)


# Module-level singleton
canvas_service = CanvasService()
