"""Widget Data Buffer — fast in-memory data pipeline for real-time widget updates.

Agents POST lightweight data payloads here; the buffer stores them and triggers
WebSocket broadcasts so widgets update in real-time without heavy MCP round-trips.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class WidgetBuffer:
    """In-memory buffer: {project: {widget_id: {data, timestamp, version}}}."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, dict[str, Any]]] = {}

    def write(self, project: str, widget_id: str, data: dict) -> dict[str, Any]:
        """Write data into the buffer. Returns the stored entry with version."""
        proj = self._store.setdefault(project, {})
        existing = proj.get(widget_id)
        version = (existing["version"] + 1) if existing else 1
        entry = {
            "data": data,
            "version": version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        proj[widget_id] = entry
        return entry

    def read(self, project: str, widget_id: str) -> dict[str, Any] | None:
        """Read latest buffered data for a single widget."""
        return self._store.get(project, {}).get(widget_id)

    def read_all(self, project: str) -> dict[str, dict[str, Any]]:
        """Read all buffered data for a project."""
        return dict(self._store.get(project, {}))


# Module-level singleton
widget_buffer = WidgetBuffer()
