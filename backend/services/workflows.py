"""Workflow service — template-driven multi-phase autonomous execution."""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..models import (
    CreateWorkflowRequest,
    IsolationInfo,
    TeamRole,
    Workflow,
    WorkflowConfig,
    WorkflowPhase,
    WorkflowPhaseType,
    WorkflowStatus,
    WorktreeInfo,
)
from . import templates as templates_svc
from .projects import MANAGED_DIR, SUBAGENT_REPORT_INSTRUCTION


# ─── Persistence ─────────────────────────────────────────────────────────────


def _workflow_path(project_name: str) -> Path:
    return MANAGED_DIR / project_name / ".claude" / "workflow.json"


def _read_workflow(project_name: str) -> Workflow | None:
    path = _workflow_path(project_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
        wf = _migrate_workflow(data)
        return wf
    except Exception:
        return None


def _migrate_workflow(data: dict) -> Workflow:
    """Migrate old workflow.json files to template-driven format."""
    # Add template_id if missing
    if "template_id" not in data:
        data["template_id"] = "software-engineering"

    # Migrate WorkflowConfig: old format had top-level fields
    config = data.get("config", {})
    if "values" not in config:
        values = {}
        if "total_sprints" in config:
            values["total_iterations"] = config.pop("total_sprints")
        if "sprint_duration_hint" in config:
            values["iteration_duration_hint"] = config.pop("sprint_duration_hint")
        if "merge_strategy" in config:
            values["merge_strategy"] = config.pop("merge_strategy")
        if values:
            config["values"] = values
        data["config"] = config

    # Migrate phases: old format used phase_type/sprint_number
    for phase in data.get("phases", []):
        if "phase_id" not in phase or not phase.get("phase_id"):
            pt = phase.get("phase_type", "")
            phase_map = {
                "quarter_planning": "planning",
                "sprint_planning": "iteration_planning",
                "sprint_execution": "iteration_execution",
                "sprint_review": "iteration_review",
                "sprint_retrospective": "iteration_retro",
                "complete": "complete",
            }
            phase["phase_id"] = phase_map.get(pt, pt)
            sn = phase.get("sprint_number")
            if sn:
                phase["iteration_number"] = sn
                label_map = {
                    "planning": "Quarter Planning",
                    "iteration_planning": f"Sprint {sn} Planning",
                    "iteration_execution": f"Sprint {sn} Execution",
                    "iteration_review": f"Sprint {sn} Review",
                    "iteration_retro": f"Sprint {sn} Retro",
                    "complete": "Complete",
                }
            else:
                label_map = {
                    "planning": "Quarter Planning",
                    "complete": "Complete",
                }
            phase["phase_label"] = label_map.get(phase["phase_id"], phase["phase_id"])

    # Migrate worktrees → isolation
    if "worktrees" in data and "isolation" not in data:
        data["isolation"] = [
            {
                "role": wt["role"],
                "instance": wt["instance"],
                "branch": wt.get("branch", ""),
                "path": wt["path"],
                "status": wt.get("status", "active"),
                "strategy": "git_worktree",
            }
            for wt in data["worktrees"]
        ]

    return Workflow(**data)


def _write_workflow(project_name: str, wf: Workflow) -> None:
    path = _workflow_path(project_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wf.model_dump_json(indent=2), "utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── CRUD ────────────────────────────────────────────────────────────────────


def get_workflow(project_name: str) -> Workflow | None:
    return _read_workflow(project_name)


def create_workflow(project_name: str, req: CreateWorkflowRequest) -> Workflow:
    existing = _read_workflow(project_name)
    if existing and existing.status == WorkflowStatus.RUNNING:
        raise ValueError("A workflow is already running for this project")

    template = templates_svc.get_template(req.template_id)
    if not template:
        raise ValueError(f"Template '{req.template_id}' not found")

    # Merge config defaults from template
    config_values = {}
    for key, field in template.config_schema.items():
        config_values[key] = field.default
    # Override with user-provided values
    config_values.update(req.config.values)

    total_iterations = int(config_values.get("total_iterations", 1))
    phases = _generate_phases(template, total_iterations)

    config = WorkflowConfig(
        auto_continue=config_values.pop("auto_continue", req.config.auto_continue),
        values=config_values,
    )

    wf = Workflow(
        id=str(uuid.uuid4()),
        project_name=project_name,
        template_id=req.template_id,
        team=req.team,
        config=config,
        status=WorkflowStatus.DRAFT,
        phases=phases,
        current_phase_index=0,
        worktrees=[],
        isolation=[],
        created_at=_now(),
    )
    _write_workflow(project_name, wf)
    return wf


def _generate_phases(template, total_iterations: int) -> list[WorkflowPhase]:
    """Build the full phase list from a template definition."""
    phases: list[WorkflowPhase] = []
    for phase_def in template.phases:
        if phase_def.repeats:
            for i in range(1, total_iterations + 1):
                label = templates_svc.render_prompt(
                    phase_def.label, {"iteration_number": str(i)}
                )
                phases.append(WorkflowPhase(
                    phase_id=phase_def.id,
                    phase_label=label,
                    iteration_number=i,
                ))
        else:
            phases.append(WorkflowPhase(
                phase_id=phase_def.id,
                phase_label=phase_def.label,
            ))
    return phases


# ─── Lifecycle ───────────────────────────────────────────────────────────────


def start_workflow(project_name: str) -> tuple[Workflow, str]:
    wf = _read_workflow(project_name)
    if not wf:
        raise ValueError("No workflow found")
    if wf.status not in (WorkflowStatus.DRAFT, WorkflowStatus.PAUSED):
        raise ValueError(f"Cannot start workflow in {wf.status} status")

    wf.status = WorkflowStatus.RUNNING
    wf.started_at = wf.started_at or _now()

    phase = wf.phases[wf.current_phase_index]
    phase.status = "active"
    phase.started_at = _now()

    prompt = _build_phase_prompt(wf, phase)
    _write_workflow(project_name, wf)
    return wf, prompt


def advance_phase(project_name: str) -> tuple[Workflow | None, str | None]:
    wf = _read_workflow(project_name)
    if not wf or wf.status != WorkflowStatus.RUNNING:
        return wf, None

    template = templates_svc.get_template(wf.template_id)
    current = wf.phases[wf.current_phase_index]
    current.status = "complete"
    current.completed_at = _now()

    # Get phase definition to check cleanup_isolation
    phase_def = _get_phase_def(template, current.phase_id) if template else None

    # Cleanup isolation if the phase definition says so
    if phase_def and phase_def.cleanup_isolation:
        if template and template.isolation_strategy == "git_worktree":
            if wf.worktrees:
                _merge_and_cleanup_worktrees(project_name, wf)
                wf.worktrees = []
            if wf.isolation:
                iso_wts = [i for i in wf.isolation if i.strategy == "git_worktree"]
                if iso_wts:
                    _merge_and_cleanup_isolation_worktrees(project_name, wf, iso_wts)
                wf.isolation = [i for i in wf.isolation if i.strategy != "git_worktree"]
        elif template and template.isolation_strategy == "subdirectory":
            if wf.isolation:
                _cleanup_subdirectories(project_name, wf.isolation)
                wf.isolation = []
    elif not phase_def:
        # Legacy fallback: cleanup worktrees on sprint_review
        if current.phase_type == WorkflowPhaseType.SPRINT_REVIEW and wf.worktrees:
            _merge_and_cleanup_worktrees(project_name, wf)
            wf.worktrees = []

    # Advance index
    wf.current_phase_index += 1

    # Check if all phases done
    if wf.current_phase_index >= len(wf.phases):
        wf.status = WorkflowStatus.COMPLETE
        wf.completed_at = _now()
        _write_workflow(project_name, wf)
        return wf, None

    # Activate next phase
    next_phase = wf.phases[wf.current_phase_index]
    next_phase.status = "active"
    next_phase.started_at = _now()

    # Create isolation when entering an execution phase
    next_def = _get_phase_def(template, next_phase.phase_id) if template else None
    if next_def and next_def.creates_isolation and template:
        wf.isolation = _create_isolation(
            project_name, wf.team, template, next_phase.iteration_number
        )
        # Keep legacy worktrees in sync for git_worktree strategy
        if template.isolation_strategy == "git_worktree":
            wf.worktrees = [
                WorktreeInfo(
                    role=iso.role, instance=iso.instance,
                    branch=iso.branch, path=iso.path, status=iso.status,
                )
                for iso in wf.isolation
            ]
    elif not next_def:
        # Legacy fallback
        if next_phase.phase_type == WorkflowPhaseType.SPRINT_EXECUTION:
            wf.worktrees = _create_worktrees(
                project_name, wf.team,
                next_phase.sprint_number or next_phase.iteration_number,
            )

    prompt = _build_phase_prompt(wf, next_phase)

    if not wf.config.auto_continue:
        wf.status = WorkflowStatus.PAUSED
        _write_workflow(project_name, wf)
        return wf, None

    _write_workflow(project_name, wf)
    return wf, prompt


def pause_workflow(project_name: str) -> Workflow:
    wf = _read_workflow(project_name)
    if not wf or wf.status != WorkflowStatus.RUNNING:
        raise ValueError("No running workflow to pause")
    wf.status = WorkflowStatus.PAUSED
    _write_workflow(project_name, wf)
    return wf


def resume_workflow(project_name: str) -> tuple[Workflow, str | None]:
    wf = _read_workflow(project_name)
    if not wf or wf.status != WorkflowStatus.PAUSED:
        raise ValueError("No paused workflow to resume")
    wf.status = WorkflowStatus.RUNNING
    phase = wf.phases[wf.current_phase_index]
    prompt = _build_phase_prompt(wf, phase) if phase.status == "active" else None
    _write_workflow(project_name, wf)
    return wf, prompt


def delete_workflow(project_name: str) -> None:
    wf = _read_workflow(project_name)
    if wf:
        if wf.worktrees:
            _cleanup_worktrees(project_name, wf.worktrees)
        if wf.isolation:
            template = templates_svc.get_template(wf.template_id)
            if template and template.isolation_strategy == "subdirectory":
                _cleanup_subdirectories(project_name, wf.isolation)
            elif template and template.isolation_strategy == "git_worktree":
                wt_infos = [
                    WorktreeInfo(
                        role=i.role, instance=i.instance,
                        branch=i.branch, path=i.path, status=i.status,
                    )
                    for i in wf.isolation
                ]
                _cleanup_worktrees(project_name, wt_infos)
    path = _workflow_path(project_name)
    if path.exists():
        path.unlink()


# ─── Phase Definition Lookup ────────────────────────────────────────────────


def _get_phase_def(template, phase_id: str):
    """Find the PhaseDefinition in a template by phase_id."""
    if not template:
        return None
    for pd in template.phases:
        if pd.id == phase_id:
            return pd
    return None


# ─── Isolation Management ──────────────────────────────────────────────────


def _create_isolation(
    project_name: str,
    team: list[TeamRole],
    template,
    iteration_number: int | None,
) -> list[IsolationInfo]:
    """Dispatch isolation creation based on template strategy."""
    strategy = template.isolation_strategy
    if strategy == "git_worktree":
        return _create_worktree_isolation(project_name, team, template, iteration_number)
    elif strategy == "subdirectory":
        return _create_subdirectory_isolation(project_name, team, template, iteration_number)
    return []


def _get_worker_roles(team: list[TeamRole], template) -> list[TeamRole]:
    """Filter team to only worker roles as defined by the template."""
    if template and template.role_presets:
        worker_roles = {rp.role for rp in template.role_presets if rp.is_worker}
        return [r for r in team if r.role in worker_roles]
    # Fallback: legacy hardcoded list
    return [r for r in team if r.role in ("engineer", "devops", "qa", "designer")]


# ─── Subdirectory Isolation ─────────────────────────────────────────────────


def _workspaces_dir(project_name: str) -> Path:
    return MANAGED_DIR / project_name / ".workspaces"


def _create_subdirectory_isolation(
    project_name: str,
    team: list[TeamRole],
    template,
    iteration_number: int | None,
) -> list[IsolationInfo]:
    ws_base = _workspaces_dir(project_name)
    ws_base.mkdir(parents=True, exist_ok=True)

    # Ensure .workspaces/ is gitignored
    project_dir = MANAGED_DIR / project_name
    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text("utf-8")
        if ".workspaces/" not in content:
            gitignore.write_text(content.rstrip() + "\n.workspaces/\n", "utf-8")
    else:
        gitignore.write_text(".workspaces/\n", "utf-8")

    isolation: list[IsolationInfo] = []
    working_roles = _get_worker_roles(team, template)

    for role_def in working_roles:
        for i in range(1, role_def.count + 1):
            name = f"{role_def.role}-{i}"
            ws_path = ws_base / name

            # Clean existing workspace
            if ws_path.exists():
                shutil.rmtree(ws_path, ignore_errors=True)

            ws_path.mkdir(parents=True, exist_ok=True)

            # Create a README for context
            readme = (
                f"# Workspace: {name}\n\n"
                f"Iteration: {iteration_number or 'N/A'}\n"
                f"Role: {role_def.role}\n\n"
                f"Place your work files in this directory.\n"
            )
            (ws_path / "README.md").write_text(readme, "utf-8")

            isolation.append(IsolationInfo(
                role=role_def.role,
                instance=i,
                path=str(ws_path),
                status="active",
                strategy="subdirectory",
            ))

    return isolation


def _cleanup_subdirectories(project_name: str, isolation: list[IsolationInfo]) -> None:
    for iso in isolation:
        if iso.strategy != "subdirectory":
            continue
        path = Path(iso.path)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        iso.status = "cleaned"


# ─── Git Worktree Isolation ─────────────────────────────────────────────────


def _worktrees_dir(project_name: str) -> Path:
    return MANAGED_DIR / project_name / ".worktrees"


def _create_worktree_isolation(
    project_name: str,
    team: list[TeamRole],
    template,
    iteration_number: int | None,
) -> list[IsolationInfo]:
    wt_list = _create_worktrees(project_name, team, iteration_number, template)
    return [
        IsolationInfo(
            role=wt.role, instance=wt.instance,
            branch=wt.branch, path=wt.path,
            status=wt.status, strategy="git_worktree",
        )
        for wt in wt_list
    ]


def _create_worktrees(
    project_name: str, team: list[TeamRole],
    sprint_number: int | None, template=None,
) -> list[WorktreeInfo]:
    project_dir = MANAGED_DIR / project_name
    wt_base = _worktrees_dir(project_name)
    wt_base.mkdir(parents=True, exist_ok=True)

    # Ensure .worktrees/ is gitignored
    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text("utf-8")
        if ".worktrees/" not in content:
            gitignore.write_text(content.rstrip() + "\n.worktrees/\n", "utf-8")
    else:
        gitignore.write_text(".worktrees/\n", "utf-8")

    worktrees: list[WorktreeInfo] = []
    working_roles = _get_worker_roles(team, template)

    for role_def in working_roles:
        for i in range(1, role_def.count + 1):
            name = f"{role_def.role}-{i}"
            branch = f"workflow/sprint-{sprint_number}/{name}"
            wt_path = wt_base / name

            # Remove stale worktree if exists
            if wt_path.exists():
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt_path)],
                    cwd=project_dir, capture_output=True,
                )
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=project_dir, capture_output=True,
                )

            result = subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(wt_path)],
                cwd=project_dir, capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"[worktree] create error for {name}: {result.stderr}")
                continue

            worktrees.append(WorktreeInfo(
                role=role_def.role,
                instance=i,
                branch=branch,
                path=str(wt_path),
                status="active",
            ))

    return worktrees


def _merge_and_cleanup_worktrees(project_name: str, wf: Workflow) -> None:
    project_dir = MANAGED_DIR / project_name
    merge_strategy = wf.config.values.get(
        "merge_strategy",
        wf.config.merge_strategy or "squash",
    )

    for wt in wf.worktrees:
        if wt.status != "active":
            continue
        try:
            if merge_strategy == "squash":
                subprocess.run(
                    ["git", "merge", "--squash", wt.branch],
                    cwd=project_dir, capture_output=True, text=True, check=True,
                )
                subprocess.run(
                    ["git", "commit", "--allow-empty", "-m",
                     f"workflow: merge {wt.role}-{wt.instance} sprint work"],
                    cwd=project_dir, capture_output=True, text=True,
                )
            else:
                subprocess.run(
                    ["git", "merge", wt.branch,
                     "-m", f"workflow: merge {wt.role}-{wt.instance} sprint work"],
                    cwd=project_dir, capture_output=True, text=True, check=True,
                )
            wt.status = "merged"
        except subprocess.CalledProcessError as exc:
            print(f"[worktree] merge conflict for {wt.branch}: {exc.stderr}")
            wt.status = "conflict"
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=project_dir, capture_output=True,
            )

    _cleanup_worktrees(project_name, wf.worktrees)


def _merge_and_cleanup_isolation_worktrees(
    project_name: str, wf: Workflow, iso_wts: list[IsolationInfo],
) -> None:
    """Merge and cleanup IsolationInfo entries that are git worktrees."""
    project_dir = MANAGED_DIR / project_name
    merge_strategy = wf.config.values.get(
        "merge_strategy",
        wf.config.merge_strategy or "squash",
    )

    for iso in iso_wts:
        if iso.status != "active" or not iso.branch:
            continue
        try:
            if merge_strategy == "squash":
                subprocess.run(
                    ["git", "merge", "--squash", iso.branch],
                    cwd=project_dir, capture_output=True, text=True, check=True,
                )
                subprocess.run(
                    ["git", "commit", "--allow-empty", "-m",
                     f"workflow: merge {iso.role}-{iso.instance} work"],
                    cwd=project_dir, capture_output=True, text=True,
                )
            else:
                subprocess.run(
                    ["git", "merge", iso.branch,
                     "-m", f"workflow: merge {iso.role}-{iso.instance} work"],
                    cwd=project_dir, capture_output=True, text=True, check=True,
                )
            iso.status = "merged"
        except subprocess.CalledProcessError as exc:
            print(f"[worktree] merge conflict for {iso.branch}: {exc.stderr}")
            iso.status = "conflict"
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=project_dir, capture_output=True,
            )

    for iso in iso_wts:
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", iso.path],
                cwd=project_dir, capture_output=True,
            )
            subprocess.run(
                ["git", "branch", "-D", iso.branch],
                cwd=project_dir, capture_output=True,
            )
            iso.status = "cleaned"
        except Exception as exc:
            print(f"[worktree] cleanup error for {iso.branch}: {exc}")


def _cleanup_worktrees(project_name: str, worktrees: list[WorktreeInfo]) -> None:
    project_dir = MANAGED_DIR / project_name
    for wt in worktrees:
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", wt.path],
                cwd=project_dir, capture_output=True,
            )
            subprocess.run(
                ["git", "branch", "-D", wt.branch],
                cwd=project_dir, capture_output=True,
            )
            wt.status = "cleaned"
        except Exception as exc:
            print(f"[worktree] cleanup error for {wt.branch}: {exc}")


# ─── Phase Prompt Generation ────────────────────────────────────────────────


def _team_summary(team: list[TeamRole]) -> str:
    parts = []
    for r in team:
        label = f"{r.count} {r.role}{'s' if r.count > 1 else ''}"
        if r.instructions:
            label += f" ({r.instructions[:80]})"
        parts.append(label)
    return ", ".join(parts)


def _isolation_table(isolation: list[IsolationInfo], worktrees: list[WorktreeInfo]) -> str:
    """Build a markdown table for either isolation entries or legacy worktrees."""
    items = isolation or []
    if not items and worktrees:
        # Legacy fallback
        items = [
            IsolationInfo(
                role=wt.role, instance=wt.instance,
                branch=wt.branch, path=wt.path,
                status=wt.status, strategy="git_worktree",
            )
            for wt in worktrees
        ]

    if not items:
        return "(no workspaces — work directly in the project directory)"

    has_branch = any(i.branch for i in items)
    if has_branch:
        lines = ["| Role | Branch | Working Directory |", "|------|--------|-------------------|"]
        for it in items:
            lines.append(f"| {it.role}-{it.instance} | `{it.branch}` | `{it.path}` |")
    else:
        lines = ["| Role | Working Directory |", "|------|-------------------|"]
        for it in items:
            lines.append(f"| {it.role}-{it.instance} | `{it.path}` |")
    return "\n".join(lines)


def _isolation_instructions(template) -> str:
    """Generate isolation-strategy-specific instructions."""
    if not template:
        return ""
    if template.isolation_strategy == "git_worktree":
        return (
            "When delegating to subagents via the Agent tool, you MUST:\n"
            "1. Tell each subagent to `cd` into their assigned worktree path FIRST\n"
            "2. Include the worktree path in the subagent prompt\n"
            "3. Tell them to commit their work on their branch when done"
        )
    elif template.isolation_strategy == "subdirectory":
        return (
            "When delegating to subagents via the Agent tool, you MUST:\n"
            "1. Tell each subagent to work in their assigned workspace directory\n"
            "2. Include the workspace path in the subagent prompt\n"
            "3. Tell them to save all output files in their workspace"
        )
    return ""


def _role_instructions(team: list[TeamRole], template=None) -> str:
    # Build a lookup of role personas from template + custom roles
    persona_map: dict[str, str] = {}
    if template and template.role_presets:
        for rp in template.role_presets:
            if rp.persona:
                persona_map[rp.role] = rp.persona
    # Also check custom roles
    try:
        from . import roles as roles_svc
        for cr in roles_svc.list_roles():
            if cr.persona and cr.role not in persona_map:
                persona_map[cr.role] = cr.persona
    except Exception:
        pass

    parts = []
    for r in team:
        persona = persona_map.get(r.role, "")
        if persona and r.instructions:
            parts.append(f"\n- **{r.role}** (persona: \"{persona}\"): {r.instructions}")
        elif persona:
            parts.append(f"\n- **{r.role}** (persona: \"{persona}\")")
        elif r.instructions:
            parts.append(f"\n- **{r.role}**: {r.instructions}")
    if parts:
        return "Role-specific instructions:" + "".join(parts)
    return ""


def _build_phase_prompt(wf: Workflow, phase: WorkflowPhase) -> str:
    """Build the prompt for a phase, using template if available."""
    template = templates_svc.get_template(wf.template_id)

    if template:
        phase_def = _get_phase_def(template, phase.phase_id)
        if phase_def:
            # Build variable context
            config_vars = {f"config.{k}": str(v) for k, v in wf.config.values.items()}
            variables = {
                "team_summary": _team_summary(wf.team),
                "iteration_number": str(phase.iteration_number or ""),
                "project_name": wf.project_name,
                "isolation_table": _isolation_table(wf.isolation, wf.worktrees),
                "isolation_instructions": _isolation_instructions(template),
                "role_instructions": _role_instructions(wf.team, template),
                "subagent_report_instruction": SUBAGENT_REPORT_INSTRUCTION,
                **config_vars,
            }
            return templates_svc.render_prompt(phase_def.prompt, variables)

    # ── Legacy fallback: hardcoded prompts for old workflows ─────────────
    return _build_legacy_prompt(wf, phase)


def _build_legacy_prompt(wf: Workflow, phase: WorkflowPhase) -> str:
    """Fallback for workflows without a valid template."""
    team_desc = _team_summary(wf.team)
    sn = phase.sprint_number or phase.iteration_number
    total = wf.config.values.get(
        "total_iterations",
        wf.config.total_sprints or 4,
    )
    duration = wf.config.values.get(
        "iteration_duration_hint",
        wf.config.sprint_duration_hint or "1 week",
    )
    merge = wf.config.values.get(
        "merge_strategy",
        wf.config.merge_strategy or "squash",
    )

    pt = phase.phase_type or phase.phase_id

    if pt in (WorkflowPhaseType.QUARTER_PLANNING, "planning"):
        return (
            f"## WORKFLOW PHASE: Quarter Planning\n\n"
            f"You are running an autonomous team workflow. Your team: {team_desc}.\n\n"
            f"**Your task:**\n"
            f"1. Read PROJECT.md thoroughly to understand the project goals.\n"
            f"2. Create a quarter roadmap with {total} sprints "
            f"(each sprint ~{duration}).\n"
            f"3. Break the project down into epics, then user stories/tasks.\n"
            f"4. Update TASKS.md with a complete backlog organized by sprint.\n"
            f"   Use headings: `## Sprint 1`, `## Sprint 2`, etc.\n"
            f"5. Prioritize: most critical/foundational work in Sprint 1.\n\n"
            f"**Output format:**\n"
            f"When done, write a brief summary:\n"
            f"## Quarter Plan Complete\n"
            f"- Total sprints: N\n"
            f"- Total tasks: N\n"
            f"- Sprint 1 focus: (brief)\n\n"
            f"Do NOT ask questions. Do NOT start implementation. Plan only."
        )

    elif pt in (WorkflowPhaseType.SPRINT_PLANNING, "iteration_planning"):
        return (
            f"## WORKFLOW PHASE: Sprint {sn} Planning\n\n"
            f"Your team: {team_desc}.\n\n"
            f"**Your task:**\n"
            f"1. Read TASKS.md — find the Sprint {sn} section.\n"
            f"2. Review task dependencies and priorities.\n"
            f"3. Assign tasks to team roles (add `@engineer-1`, `@qa-1` etc. after each task).\n"
            f"4. Ensure each role has a balanced workload.\n"
            f"5. Update TASKS.md with assignments.\n\n"
            f"**Output format:**\n"
            f"## Sprint {sn} Plan\n"
            f"- Tasks assigned: N\n"
            f"- Engineer tasks: N | QA tasks: N | DevOps tasks: N\n"
            f"- Key risks: (brief)\n\n"
            f"Do NOT start implementation. Planning only."
        )

    elif pt in (WorkflowPhaseType.SPRINT_EXECUTION, "iteration_execution"):
        wt_table = _isolation_table(wf.isolation, wf.worktrees)
        role_inst = _role_instructions(wf.team)
        return (
            f"## WORKFLOW PHASE: Sprint {sn} Execution\n\n"
            f"Your team: {team_desc}.\n"
            f"{role_inst}\n\n"
            f"**IMPORTANT: Git Worktrees**\n"
            f"Each team member has an isolated working directory (git worktree) to avoid conflicts:\n\n"
            f"{wt_table}\n\n"
            f"When delegating to subagents via the Agent tool, you MUST:\n"
            f"1. Tell each subagent to `cd` into their assigned worktree path FIRST\n"
            f"2. Include the worktree path in the subagent prompt\n"
            f"3. Tell them to commit their work on their branch when done\n\n"
            f"**Your task:**\n"
            f"1. Read TASKS.md — find Sprint {sn} tasks.\n"
            f"2. For each assigned role, spawn a subagent with the Agent tool.\n"
            f"3. Give each subagent: their tasks, their worktree path, and context from PROJECT.md.\n"
            f"4. Wait for all subagents to complete.\n"
            f"5. Update TASKS.md checkboxes based on results.\n\n"
            f"Delegate ALL work. You are the coordinator."
            + SUBAGENT_REPORT_INSTRUCTION
        )

    elif pt in (WorkflowPhaseType.SPRINT_REVIEW, "iteration_review"):
        wt_table = _isolation_table(wf.isolation, wf.worktrees)
        return (
            f"## WORKFLOW PHASE: Sprint {sn} Review\n\n"
            f"Sprint execution is complete. Now review the work.\n\n"
            f"**Worktrees with completed work:**\n{wt_table}\n\n"
            f"**Your task:**\n"
            f"1. For each worktree with engineer work, spawn a QA subagent to review.\n"
            f"   Tell the subagent to `cd` into the worktree path and review all changes.\n"
            f"2. QA should check: correctness, code quality, tests passing, edge cases.\n"
            f"3. Collect review results from all subagents.\n"
            f"4. Summarize: what passed review, what needs fixes.\n\n"
            f"**Output format:**\n"
            f"## Sprint {sn} Review\n"
            f"- [x] Items that passed review\n"
            f"- [ ] Items that need fixes\n"
            + SUBAGENT_REPORT_INSTRUCTION
        )

    elif pt in (WorkflowPhaseType.SPRINT_RETRO, "iteration_retro"):
        return (
            f"## WORKFLOW PHASE: Sprint {sn} Retrospective\n\n"
            f"Sprint {sn} is complete. Worktree branches have been merged to main.\n\n"
            f"**Your task:**\n"
            f"1. Read TASKS.md — count completed vs remaining tasks for Sprint {sn}.\n"
            f"2. Generate a sprint report:\n"
            f"   - Tasks completed / total\n"
            f"   - Key accomplishments\n"
            f"   - Issues encountered\n"
            f"   - Velocity assessment\n"
            f"3. If there are remaining sprints, note any tasks to carry over.\n\n"
            f"**Output format:**\n"
            f"## Sprint {sn} Report\n"
            f"**Completed**: N/M tasks\n"
            f"**Highlights**: ...\n"
            f"**Carry-over**: ...\n"
            f"**Velocity**: on track / behind / ahead\n"
        )

    elif pt in (WorkflowPhaseType.COMPLETE, "complete"):
        return (
            f"## WORKFLOW COMPLETE\n\n"
            f"All {total} sprints are finished.\n\n"
            f"**Your task:**\n"
            f"1. Read TASKS.md — generate a final quarter report.\n"
            f"2. Summarize: total tasks completed, overall progress, key deliverables.\n"
            f"3. Note any remaining backlog items.\n\n"
            f"**Output format:**\n"
            f"## Quarter Report\n"
            f"**Duration**: {total} sprints\n"
            f"**Total tasks**: X completed / Y total\n"
            f"**Key deliverables**: ...\n"
            f"**Remaining backlog**: ...\n"
        )

    return f"Unknown phase: {pt}"
