"""Widget Catalog — store and retrieve reusable widget templates.

Templates are parameterized HTML/CSS/JS with {{placeholder}} variables
that agents fill with data via canvas_put(template="my-template", data='{...}').

Storage: ~/.claude/canvas/templates/<template_id>.json
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path.home() / ".claude" / "canvas" / "templates"


def _ensure_dir() -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def list_templates() -> list[dict[str, Any]]:
    """Return all saved templates (metadata only, no full html/css)."""
    _ensure_dir()
    templates = []
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text("utf-8"))
            templates.append({
                "id": data["id"],
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "category": data.get("category", "custom"),
                "col_span": data.get("col_span", 1),
                "row_span": data.get("row_span", 1),
                "data_schema": data.get("data_schema", {}),
                "preview_data": data.get("preview_data", {}),
                "created_at": data.get("created_at", ""),
            })
        except Exception:
            continue
    return templates


def get_template(template_id: str) -> dict[str, Any] | None:
    """Return a full template by ID (including html/css/js)."""
    _ensure_dir()
    path = TEMPLATES_DIR / f"{template_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def save_template(template: dict[str, Any]) -> dict[str, Any]:
    """Save a template. Generates ID if not provided."""
    _ensure_dir()
    if not template.get("id"):
        template["id"] = str(uuid.uuid4())[:8]
    template.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    template.setdefault("category", "custom")

    path = TEMPLATES_DIR / f"{template['id']}.json"
    path.write_text(json.dumps(template, indent=2), "utf-8")
    return template


def delete_template(template_id: str) -> bool:
    """Delete a template by ID."""
    path = TEMPLATES_DIR / f"{template_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def render_template(template_id: str, data: dict[str, Any]) -> tuple[str, str, str] | None:
    """Render a template with data, returning (html, css, js).

    Replaces {{key}} placeholders in html/css/js with values from data.
    Also handles {{#each items}}...{{/each}} for simple list iteration.
    """
    tmpl = get_template(template_id)
    if not tmpl:
        return None

    html = tmpl.get("html", "")
    css = tmpl.get("css", "")
    js = tmpl.get("js", "")

    def _substitute(text: str, ctx: dict) -> str:
        # Handle {{#each key}}...{{/each}} blocks
        def each_replace(m):
            key = m.group(1)
            body = m.group(2)
            items = ctx.get(key, [])
            if not isinstance(items, list):
                return ""
            parts = []
            for i, item in enumerate(items):
                chunk = body
                if isinstance(item, dict):
                    for k, v in item.items():
                        chunk = chunk.replace(f"{{{{{k}}}}}", str(v))
                else:
                    chunk = chunk.replace("{{.}}", str(item))
                chunk = chunk.replace("{{@index}}", str(i))
                parts.append(chunk)
            return "".join(parts)

        text = re.sub(
            r"\{\{#each\s+(\w+)\}\}(.*?)\{\{/each\}\}",
            each_replace,
            text,
            flags=re.DOTALL,
        )

        # Simple {{key}} replacement
        for k, v in ctx.items():
            if not isinstance(v, (list, dict)):
                text = text.replace(f"{{{{{k}}}}}", str(v))

        return text

    return _substitute(html, data), _substitute(css, data), _substitute(js, data)
