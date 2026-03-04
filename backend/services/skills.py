"""Skill discovery, per-project toggles, marketplace browsing, and creation."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..models import CreateSkillRequest, SkillInfo
from .agents import CLAUDE_DIR

MANAGED_PROJECTS_DIR = Path(
    os.environ.get("MANAGED_PROJECTS_DIR", Path.home() / "git" / "claude-managed-projects")
)
SKILLS_DIR = CLAUDE_DIR / "skills"
PLUGINS_FILE = CLAUDE_DIR / "plugins" / "installed_plugins.json"
MARKETPLACES_DIR = CLAUDE_DIR / "plugins" / "marketplaces"


# ─── SKILL.md parsing ──────────────────────────────────────────────────────────


def _parse_skill_md(path: Path) -> dict[str, Any]:
    """Parse a SKILL.md file — split YAML frontmatter from body."""
    try:
        text = path.read_text("utf-8")
    except (FileNotFoundError, PermissionError):
        return {"name": path.parent.name, "description": None, "body": ""}

    # Extract YAML frontmatter between --- delimiters
    frontmatter: dict[str, Any] = {}
    body = text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if match:
        body = text[match.end():]
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key == "allowed-tools":
                    frontmatter[key] = [t.strip() for t in val.split(",")]
                else:
                    frontmatter[key] = val

    return {
        "name": frontmatter.get("name", path.parent.name),
        "description": frontmatter.get("description"),
        "body": body.strip(),
        "frontmatter": frontmatter,
    }


# ─── Discovery ─────────────────────────────────────────────────────────────────


def list_global_skills() -> list[SkillInfo]:
    """Scan ~/.claude/skills/*/SKILL.md for user-created global skills."""
    skills: list[SkillInfo] = []
    if not SKILLS_DIR.exists():
        return skills

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_file.exists():
            continue
        parsed = _parse_skill_md(skill_file)
        skills.append(SkillInfo(
            name=parsed["name"],
            description=parsed.get("description"),
            source="global",
            path=str(skill_dir),
            enabled=True,
            frontmatter=parsed.get("frontmatter", {}),
        ))
    return skills


def list_project_skills(project_name: str) -> list[SkillInfo]:
    """Scan <project>/.claude/skills/*/SKILL.md, tag as local or global (symlink)."""
    project_path = MANAGED_PROJECTS_DIR / project_name
    skills_dir = project_path / ".claude" / "skills"
    skills: list[SkillInfo] = []
    if not skills_dir.exists():
        return skills

    for skill_dir in sorted(skills_dir.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_file.exists():
            continue
        is_symlink = skill_dir.is_symlink()
        parsed = _parse_skill_md(skill_file)
        skills.append(SkillInfo(
            name=parsed["name"],
            description=parsed.get("description"),
            source="global" if is_symlink else "local",
            path=str(skill_dir),
            enabled=True,
            frontmatter=parsed.get("frontmatter", {}),
        ))
    return skills


def list_available_for_project(project_name: str) -> list[SkillInfo]:
    """All global skills with enabled=True for those present in the project."""
    project_path = MANAGED_PROJECTS_DIR / project_name
    skills_dir = project_path / ".claude" / "skills"

    # Collect names currently present in project
    enabled_names: set[str] = set()
    local_skills: list[SkillInfo] = []
    if skills_dir.exists():
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            parsed = _parse_skill_md(skill_file)
            name = parsed["name"]
            is_symlink = skill_dir.is_symlink()
            if is_symlink:
                enabled_names.add(name)
            else:
                # Local skill — always enabled, not toggleable
                local_skills.append(SkillInfo(
                    name=name,
                    description=parsed.get("description"),
                    source="local",
                    path=str(skill_dir),
                    enabled=True,
                    frontmatter=parsed.get("frontmatter", {}),
                ))

    # Global skills with enabled state
    result: list[SkillInfo] = list(local_skills)
    for skill in list_global_skills():
        result.append(SkillInfo(
            name=skill.name,
            description=skill.description,
            source="global",
            path=skill.path,
            enabled=skill.name in enabled_names,
            frontmatter=skill.frontmatter,
        ))

    return result


# ─── Per-project toggle ────────────────────────────────────────────────────────


def enable_skill_for_project(project_name: str, skill_name: str) -> None:
    """Symlink a global skill into the project's .claude/skills/."""
    source = SKILLS_DIR / skill_name
    if not source.exists():
        raise ValueError(f"Global skill '{skill_name}' not found")

    project_path = MANAGED_PROJECTS_DIR / project_name
    skills_dir = project_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    target = skills_dir / skill_name
    if target.exists() or target.is_symlink():
        # Already present
        return
    os.symlink(str(source), str(target))


def disable_skill_for_project(project_name: str, skill_name: str) -> None:
    """Remove a symlinked skill from the project. Never deletes real directories."""
    project_path = MANAGED_PROJECTS_DIR / project_name
    target = project_path / ".claude" / "skills" / skill_name

    if not target.exists() and not target.is_symlink():
        return  # Already absent

    if not target.is_symlink():
        raise ValueError(f"Skill '{skill_name}' is a local skill, not a symlink — cannot disable")

    target.unlink()


# ─── Marketplace browsing ──────────────────────────────────────────────────────


def list_marketplace_plugins() -> list[dict[str, Any]]:
    """Scan marketplace directories for available plugins."""
    plugins: list[dict[str, Any]] = []

    if not MARKETPLACES_DIR.exists():
        return plugins

    # Load installed state
    installed: dict[str, Any] = {}
    try:
        installed = json.loads(PLUGINS_FILE.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    installed_ids = set(installed.get("plugins", {}).keys())

    for mkt_dir in sorted(MARKETPLACES_DIR.iterdir()):
        if not mkt_dir.is_dir():
            continue
        mkt_name = mkt_dir.name
        plugins_dir = mkt_dir / "plugins"
        if not plugins_dir.exists():
            continue

        for plugin_dir in sorted(plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin_name = plugin_dir.name

            # Try to read plugin.json
            meta: dict[str, Any] = {}
            plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
            if plugin_json.exists():
                try:
                    meta = json.loads(plugin_json.read_text("utf-8"))
                except (json.JSONDecodeError, PermissionError):
                    pass

            # Check readme for description fallback
            description = meta.get("description")
            if not description:
                readme = plugin_dir / "README.md"
                if readme.exists():
                    try:
                        lines = readme.read_text("utf-8").splitlines()
                        # First non-heading, non-empty line
                        for line in lines:
                            stripped = line.strip()
                            if stripped and not stripped.startswith("#"):
                                description = stripped[:200]
                                break
                    except (PermissionError, UnicodeDecodeError):
                        pass

            full_id = f"{plugin_name}@{mkt_name}"
            plugins.append({
                "name": meta.get("name", plugin_name),
                "id": full_id,
                "marketplace": mkt_name,
                "description": description,
                "installed": full_id in installed_ids,
                "path": str(plugin_dir),
            })

    return plugins


# ─── Skill creation ────────────────────────────────────────────────────────────


def create_skill(req: CreateSkillRequest) -> SkillInfo:
    """Create a new SKILL.md file from the request."""
    # Determine target directory
    if req.scope == "global":
        target_dir = SKILLS_DIR / req.name
    else:
        project_path = MANAGED_PROJECTS_DIR / req.scope
        if not project_path.exists():
            raise ValueError(f"Project '{req.scope}' not found")
        target_dir = project_path / ".claude" / "skills" / req.name

    target_dir.mkdir(parents=True, exist_ok=True)
    skill_file = target_dir / "SKILL.md"

    # Build YAML frontmatter
    lines = ["---"]
    lines.append(f"name: {req.name}")
    lines.append(f"description: {req.description}")
    if req.allowed_tools:
        lines.append(f"allowed-tools: {', '.join(req.allowed_tools)}")
    lines.append("---")
    lines.append("")
    lines.append(req.content)
    lines.append("")

    skill_file.write_text("\n".join(lines), encoding="utf-8")

    source = "global" if req.scope == "global" else "local"
    return SkillInfo(
        name=req.name,
        description=req.description,
        source=source,
        path=str(target_dir),
        enabled=True,
        frontmatter={
            "name": req.name,
            "description": req.description,
            "allowed-tools": req.allowed_tools,
        },
    )
