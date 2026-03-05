"""Role service — CRUD for custom agent roles stored in ~/.claude/roles.json."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import RolePreset
from . import templates as templates_svc

_ROLES_FILE = Path("~/.claude/roles.json").expanduser()


def _read_roles() -> list[dict]:
    if not _ROLES_FILE.exists():
        return []
    try:
        return json.loads(_ROLES_FILE.read_text("utf-8"))
    except Exception:
        return []


def _write_roles(roles: list[dict]) -> None:
    _ROLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ROLES_FILE.write_text(json.dumps(roles, indent=2), "utf-8")


def list_roles() -> list[RolePreset]:
    """List all user-created custom roles."""
    return [RolePreset(**r) for r in _read_roles()]


def create_role(role: RolePreset) -> RolePreset:
    """Create a new custom role."""
    roles = _read_roles()
    # Check for duplicate role id
    if any(r["role"] == role.role for r in roles):
        raise ValueError(f"Role '{role.role}' already exists")
    roles.append(role.model_dump())
    _write_roles(roles)
    return role


def update_role(role_id: str, updates: dict) -> RolePreset:
    """Update an existing custom role."""
    roles = _read_roles()
    for i, r in enumerate(roles):
        if r["role"] == role_id:
            r.update(updates)
            # Ensure builtin stays false for custom roles
            r["builtin"] = False
            roles[i] = r
            _write_roles(roles)
            return RolePreset(**r)
    raise ValueError(f"Role '{role_id}' not found")


def delete_role(role_id: str) -> None:
    """Delete a custom role."""
    roles = _read_roles()
    filtered = [r for r in roles if r["role"] != role_id]
    if len(filtered) == len(roles):
        raise ValueError(f"Role '{role_id}' not found")
    _write_roles(filtered)


def get_all_roles(template_id: str | None = None) -> list[RolePreset]:
    """Merge template built-in roles + user custom roles."""
    result: list[RolePreset] = []

    # Template built-in roles
    if template_id:
        tpl = templates_svc.get_template(template_id)
        if tpl:
            for rp in tpl.role_presets:
                result.append(RolePreset(
                    role=rp.role,
                    label=rp.label,
                    is_worker=rp.is_worker,
                    persona=rp.persona,
                    expertise=rp.expertise,
                    builtin=True,
                ))

    # User custom roles (skip duplicates)
    existing_ids = {r.role for r in result}
    for custom in list_roles():
        if custom.role not in existing_ids:
            result.append(custom)

    return result
