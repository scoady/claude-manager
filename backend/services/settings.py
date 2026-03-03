"""Read and write Claude settings at global and per-project scopes."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..services.agents import CLAUDE_DIR, PROJECTS_DIR

SETTINGS_FILE       = CLAUDE_DIR / "settings.json"
PLUGINS_FILE        = CLAUDE_DIR / "plugins" / "installed_plugins.json"
POLICY_FILE         = CLAUDE_DIR / "policy-limits.json"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _project_dir_to_path(dir_name: str) -> str:
    return dir_name.replace("-", "/")


def _project_name(dir_name: str) -> str:
    parts = [p for p in dir_name.split("-") if p and p not in {"Users", "home"}]
    return "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else dir_name)


# ─── Global settings ──────────────────────────────────────────────────────────

def get_global_settings() -> dict[str, Any]:
    return _read_json(SETTINGS_FILE)


def set_global_settings(data: dict[str, Any]) -> None:
    _write_json(SETTINGS_FILE, data)


def get_policy_limits() -> dict[str, Any]:
    return _read_json(POLICY_FILE)


# ─── Plugins ──────────────────────────────────────────────────────────────────

def get_plugins() -> list[dict[str, Any]]:
    """Return installed plugins merged with their enabled state from settings."""
    installed = _read_json(PLUGINS_FILE)
    settings  = get_global_settings()
    enabled_map: dict[str, bool] = settings.get("enabledPlugins", {})

    plugins = []
    for plugin_id, installs in installed.get("plugins", {}).items():
        if not installs:
            continue
        latest = installs[-1]
        plugins.append({
            "id":           plugin_id,
            "version":      latest.get("version"),
            "scope":        latest.get("scope"),
            "installed_at": latest.get("installedAt"),
            "enabled":      enabled_map.get(plugin_id, False),
        })
    return plugins


def set_plugin_enabled(plugin_id: str, enabled: bool) -> None:
    """Toggle a plugin on or off in the global settings."""
    settings = get_global_settings()
    enabled_map = settings.setdefault("enabledPlugins", {})
    if enabled:
        enabled_map[plugin_id] = True
    else:
        enabled_map.pop(plugin_id, None)
    set_global_settings(settings)


# ─── Per-project settings ─────────────────────────────────────────────────────

def _find_project_settings_file(project_path: str) -> Path | None:
    """Locate the settings file for a given absolute project path."""
    p = Path(project_path)
    for name in ("settings.local.json", "settings.json"):
        candidate = p / ".claude" / name
        if candidate.exists():
            return candidate
    return None


def list_project_settings() -> list[dict[str, Any]]:
    """Return settings for every known project directory."""
    projects = []
    if not PROJECTS_DIR.exists():
        return projects

    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        dir_name = project_dir.name
        project_path = _project_dir_to_path(dir_name)

        settings_file = _find_project_settings_file(project_path)
        settings_data = _read_json(settings_file) if settings_file else {}

        projects.append({
            "key":          dir_name,
            "name":         _project_name(dir_name),
            "path":         project_path,
            "settings":     settings_data,
            "settings_file": str(settings_file) if settings_file else None,
        })
    return projects


def get_project_settings(project_key: str) -> dict[str, Any]:
    project_path = _project_dir_to_path(project_key)
    settings_file = _find_project_settings_file(project_path)
    return _read_json(settings_file) if settings_file else {}


def set_project_settings(project_key: str, data: dict[str, Any]) -> None:
    """Write project settings to .claude/settings.local.json."""
    project_path = _project_dir_to_path(project_key)
    # Prefer to update the existing file if it exists, otherwise create local
    settings_file = _find_project_settings_file(project_path) or (
        Path(project_path) / ".claude" / "settings.local.json"
    )
    _write_json(settings_file, data)


def add_permission(project_key: str, permission: str, kind: str = "allow") -> dict[str, Any]:
    """Add an allow or deny permission entry to a project's settings."""
    settings = get_project_settings(project_key)
    perms = settings.setdefault("permissions", {})
    lst = perms.setdefault(kind, [])
    if permission not in lst:
        lst.append(permission)
    set_project_settings(project_key, settings)
    return settings


def remove_permission(project_key: str, permission: str, kind: str = "allow") -> dict[str, Any]:
    """Remove a permission entry from a project's settings."""
    settings = get_project_settings(project_key)
    lst = settings.get("permissions", {}).get(kind, [])
    settings["permissions"][kind] = [p for p in lst if p != permission]
    set_project_settings(project_key, settings)
    return settings
