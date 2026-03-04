"""Managed project service — scan, bootstrap, and configure projects."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..models import ManagedProject, ProjectConfig

MANAGED_DIR = Path(
    os.environ.get("MANAGED_PROJECTS_DIR", "~/git/claude-managed-projects")
).expanduser()

_PROJECT_MD_TEMPLATE = """\
# {name}

{description}
"""

_TASKS_MD_TEMPLATE = """\
# Tasks

<!-- Agent: populate this checklist before starting work -->

- [ ] (tasks will appear here)
"""

_INSTRUCTIONS_TEMPLATE = """\
# Agent Instructions

You are an autonomous agent working on **{name}**.
Your work is streamed live to the manager dashboard — your text output appears on the agent card in real time.

## Workflow

1. Read `PROJECT.md` to fully understand the goal.
2. Create (or update) `TASKS.md` with a concrete checklist before doing any work.
3. Work through tasks one at a time. Keep working until all tasks are complete.
4. When ALL tasks are done, write a final summary and a clear "✅ All tasks complete" line.

## Status updates — IMPORTANT

Write a short status line before and after each task so the dashboard stays current:

  → Starting: <task name>
  ✓ Done: <task name>
  ⚠ Blocked: <reason>
  ✅ All tasks complete — <brief summary of what was accomplished>

These lines appear live on your agent card. Keep them short and clear.
The final "✅ All tasks complete" line is critical — it signals to the dashboard that you are done.

## TASKS.md format

  - [ ] task not started
  - [~] task in progress
  - [x] task complete

Update checkboxes as you go so anyone can see overall progress at a glance.

## Git & Deployment

Git is configured and SSH keys are available. You can push/pull to GitHub remotes.
- Git user: configured globally (inherited from host)
- SSH keys: mounted at `~/.ssh/` (ed25519 for GitHub, RSA for other hosts)
- To create a new GitHub repo: `gh repo create <org>/<name> --private --source=. --push`
- To add a remote: `git remote add origin git@github.com:<org>/<name>.git`
- Always commit your work and push when you reach a milestone or finish.

If the project doesn't have a remote yet, create one under the `scoady` GitHub org.

## Controller Mode

You are the **controller agent** for this project. You persist across all tasks.

When you receive a task injection:
1. Read TASKS.md to see the current state
2. Use the **Agent** tool to delegate work to a subagent
3. Give the subagent a clear, specific prompt including relevant context
4. Monitor the subagent's results
5. Update TASKS.md checkboxes when tasks are complete
6. Report a brief summary and wait for the next injection

Always use the Agent tool for execution work — you are the coordinator, not the worker.
"""

_SETTINGS_TEMPLATE = {
    "permissions": {
        "allow": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        "deny": [],
    }
}

_MANAGER_CONFIG_DEFAULT = {"parallelism": 1, "model": None}


def _read_project_md(project_dir: Path) -> tuple[str | None, str | None]:
    """Return (description, full_content) from PROJECT.md."""
    md_path = project_dir / "PROJECT.md"
    if not md_path.exists():
        return None, None
    try:
        content = md_path.read_text("utf-8")
    except Exception:
        return None, None

    # Extract first non-heading, non-empty line as short description
    description: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            description = stripped[:120]
            break

    return description, content


def _read_manager_config(project_dir: Path) -> ProjectConfig:
    config_path = project_dir / ".claude" / "manager.json"
    try:
        data = json.loads(config_path.read_text("utf-8"))
        return ProjectConfig(
            parallelism=int(data.get("parallelism", 1)),
            model=data.get("model") or None,
        )
    except Exception:
        return ProjectConfig()


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def list_projects(active_session_ids: dict[str, list[str]] | None = None) -> list[ManagedProject]:
    """List all managed projects. active_session_ids maps project_name → [session_id, ...]."""
    if not MANAGED_DIR.exists():
        return []

    projects: list[ManagedProject] = []
    for entry in sorted(MANAGED_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        description, goal = _read_project_md(entry)
        config = _read_manager_config(entry)
        sessions = (active_session_ids or {}).get(entry.name, [])
        projects.append(
            ManagedProject(
                name=entry.name,
                path=str(entry),
                description=description,
                goal=goal,
                config=config,
                active_session_ids=sessions,
            )
        )
    return projects


def get_project(name: str, active_session_ids: list[str] | None = None) -> ManagedProject | None:
    project_dir = MANAGED_DIR / name
    if not project_dir.exists():
        return None
    description, goal = _read_project_md(project_dir)
    config = _read_manager_config(project_dir)
    return ManagedProject(
        name=name,
        path=str(project_dir),
        description=description,
        goal=goal,
        config=config,
        active_session_ids=active_session_ids or [],
    )


def bootstrap_project(name: str, description: str) -> ManagedProject:
    """Create a new managed project directory with template files and git init."""
    project_dir = MANAGED_DIR / name
    if project_dir.exists():
        raise ValueError(f"Project '{name}' already exists")

    project_dir.mkdir(parents=True)
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir()

    (project_dir / "PROJECT.md").write_text(
        _PROJECT_MD_TEMPLATE.format(name=name, description=description),
        "utf-8",
    )

    (project_dir / "TASKS.md").write_text(_TASKS_MD_TEMPLATE, "utf-8")

    (claude_dir / "INSTRUCTIONS.md").write_text(
        _INSTRUCTIONS_TEMPLATE.format(name=name),
        "utf-8",
    )

    (claude_dir / "settings.local.json").write_text(
        json.dumps(_SETTINGS_TEMPLATE, indent=2),
        "utf-8",
    )

    (claude_dir / "manager.json").write_text(
        json.dumps(_MANAGER_CONFIG_DEFAULT, indent=2),
        "utf-8",
    )

    (project_dir / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.env\n.DS_Store\nnode_modules/\n",
        "utf-8",
    )

    try:
        subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"bootstrap: initialize {name}"],
            cwd=project_dir,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    short_desc = description[:120] if description else None
    return ManagedProject(
        name=name,
        path=str(project_dir),
        description=short_desc,
        goal=description,
        config=ProjectConfig(),
        active_session_ids=[],
    )


def update_project_config(name: str, config: ProjectConfig) -> None:
    """Write .claude/manager.json for a project."""
    project_dir = MANAGED_DIR / name
    if not project_dir.exists():
        raise ValueError(f"Project '{name}' not found")
    config_path = project_dir / ".claude" / "manager.json"
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(
        json.dumps({"parallelism": config.parallelism, "model": config.model}, indent=2),
        "utf-8",
    )
