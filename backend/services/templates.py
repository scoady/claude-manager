"""Template service — discovery, loading, validation, and prompt rendering."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import WorkflowTemplate

# Built-in templates ship with the app
_BUILTIN_DIR = Path(__file__).parent.parent / "templates"

# User-created custom templates
_CUSTOM_DIR = Path("~/.claude/workflow-templates").expanduser()

_cache: dict[str, WorkflowTemplate] = {}
_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    _cache.clear()
    for directory in (_BUILTIN_DIR, _CUSTOM_DIR):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text("utf-8"))
                tpl = WorkflowTemplate(**data)
                # Tag template-defined roles as built-in
                for rp in tpl.role_presets:
                    rp.builtin = True
                _cache[tpl.id] = tpl
            except Exception as exc:
                print(f"[templates] failed to load {path}: {exc}")


def reload() -> None:
    """Force reload templates from disk."""
    global _loaded
    _loaded = False
    _ensure_loaded()


def list_templates() -> list[WorkflowTemplate]:
    _ensure_loaded()
    return list(_cache.values())


def get_template(template_id: str) -> WorkflowTemplate | None:
    _ensure_loaded()
    return _cache.get(template_id)


def create_custom_template(template: WorkflowTemplate) -> None:
    _CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    path = _CUSTOM_DIR / f"{template.id}.json"
    path.write_text(template.model_dump_json(indent=2), "utf-8")
    _cache[template.id] = template


def delete_custom_template(template_id: str) -> bool:
    path = _CUSTOM_DIR / f"{template_id}.json"
    if path.exists():
        path.unlink()
        _cache.pop(template_id, None)
        return True
    return False


def render_prompt(template_text: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} and {{config.field}} placeholders."""
    def _replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        # First: try the full dotted key as-is (e.g. "config.total_iterations")
        if key in variables:
            return str(variables[key])
        # Second: try nested dict traversal (e.g. variables["config"]["total_iterations"])
        parts = key.split(".")
        val = variables
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part, "")
            else:
                return match.group(0)  # leave unchanged
        return str(val) if val != "" else match.group(0)

    return re.sub(r"\{\{(\s*[\w.]+\s*)\}\}", _replacer, template_text)
