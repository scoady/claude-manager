"""Workflow service — multi-phase autonomous execution with team composition and git worktrees."""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..models import (
    CreateWorkflowRequest,
    TeamRole,
    Workflow,
    WorkflowConfig,
    WorkflowPhase,
    WorkflowPhaseType,
    WorkflowStatus,
    WorktreeInfo,
)
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
        return Workflow(**data)
    except Exception:
        return None


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

    phases = _generate_phases(req.config.total_sprints)

    wf = Workflow(
        id=str(uuid.uuid4()),
        project_name=project_name,
        team=req.team,
        config=req.config,
        status=WorkflowStatus.DRAFT,
        phases=phases,
        current_phase_index=0,
        worktrees=[],
        created_at=_now(),
    )
    _write_workflow(project_name, wf)
    return wf


def _generate_phases(total_sprints: int) -> list[WorkflowPhase]:
    phases = [WorkflowPhase(phase_type=WorkflowPhaseType.QUARTER_PLANNING)]
    for sprint in range(1, total_sprints + 1):
        phases.append(WorkflowPhase(
            phase_type=WorkflowPhaseType.SPRINT_PLANNING,
            sprint_number=sprint,
        ))
        phases.append(WorkflowPhase(
            phase_type=WorkflowPhaseType.SPRINT_EXECUTION,
            sprint_number=sprint,
        ))
        phases.append(WorkflowPhase(
            phase_type=WorkflowPhaseType.SPRINT_REVIEW,
            sprint_number=sprint,
        ))
        phases.append(WorkflowPhase(
            phase_type=WorkflowPhaseType.SPRINT_RETRO,
            sprint_number=sprint,
        ))
    phases.append(WorkflowPhase(phase_type=WorkflowPhaseType.COMPLETE))
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

    # Mark current phase complete
    current = wf.phases[wf.current_phase_index]
    current.status = "complete"
    current.completed_at = _now()

    # If leaving sprint review, merge worktrees
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

    # Create worktrees when entering sprint execution
    if next_phase.phase_type == WorkflowPhaseType.SPRINT_EXECUTION:
        wf.worktrees = _create_worktrees(project_name, wf.team, next_phase.sprint_number)

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
    if wf and wf.worktrees:
        _cleanup_worktrees(project_name, wf.worktrees)
    path = _workflow_path(project_name)
    if path.exists():
        path.unlink()


# ─── Git Worktree Management ────────────────────────────────────────────────


def _worktrees_dir(project_name: str) -> Path:
    return MANAGED_DIR / project_name / ".worktrees"


def _create_worktrees(
    project_name: str, team: list[TeamRole], sprint_number: int | None
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
    working_roles = [r for r in team if r.role in ("engineer", "devops", "qa", "designer")]

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
                # Also delete old branch
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

    for wt in wf.worktrees:
        if wt.status != "active":
            continue
        try:
            if wf.config.merge_strategy == "squash":
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


def _worktree_table(worktrees: list[WorktreeInfo]) -> str:
    if not worktrees:
        return "(no worktrees — work directly in the project directory)"
    lines = ["| Role | Branch | Working Directory |", "|------|--------|-------------------|"]
    for wt in worktrees:
        lines.append(f"| {wt.role}-{wt.instance} | `{wt.branch}` | `{wt.path}` |")
    return "\n".join(lines)


def _build_phase_prompt(wf: Workflow, phase: WorkflowPhase) -> str:
    team_desc = _team_summary(wf.team)

    if phase.phase_type == WorkflowPhaseType.QUARTER_PLANNING:
        return (
            f"## WORKFLOW PHASE: Quarter Planning\n\n"
            f"You are running an autonomous team workflow. Your team: {team_desc}.\n\n"
            f"**Your task:**\n"
            f"1. Read PROJECT.md thoroughly to understand the project goals.\n"
            f"2. Create a quarter roadmap with {wf.config.total_sprints} sprints "
            f"(each sprint ~{wf.config.sprint_duration_hint}).\n"
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

    elif phase.phase_type == WorkflowPhaseType.SPRINT_PLANNING:
        return (
            f"## WORKFLOW PHASE: Sprint {phase.sprint_number} Planning\n\n"
            f"Your team: {team_desc}.\n\n"
            f"**Your task:**\n"
            f"1. Read TASKS.md — find the Sprint {phase.sprint_number} section.\n"
            f"2. Review task dependencies and priorities.\n"
            f"3. Assign tasks to team roles (add `@engineer-1`, `@qa-1` etc. after each task).\n"
            f"4. Ensure each role has a balanced workload.\n"
            f"5. Update TASKS.md with assignments.\n\n"
            f"**Output format:**\n"
            f"## Sprint {phase.sprint_number} Plan\n"
            f"- Tasks assigned: N\n"
            f"- Engineer tasks: N | QA tasks: N | DevOps tasks: N\n"
            f"- Key risks: (brief)\n\n"
            f"Do NOT start implementation. Planning only."
        )

    elif phase.phase_type == WorkflowPhaseType.SPRINT_EXECUTION:
        wt_table = _worktree_table(wf.worktrees)
        role_instructions = ""
        for r in wf.team:
            if r.instructions:
                role_instructions += f"\n- **{r.role}**: {r.instructions}"

        return (
            f"## WORKFLOW PHASE: Sprint {phase.sprint_number} Execution\n\n"
            f"Your team: {team_desc}.\n"
            f"{'Role-specific instructions:' + role_instructions if role_instructions else ''}\n\n"
            f"**IMPORTANT: Git Worktrees**\n"
            f"Each team member has an isolated working directory (git worktree) to avoid conflicts:\n\n"
            f"{wt_table}\n\n"
            f"When delegating to subagents via the Agent tool, you MUST:\n"
            f"1. Tell each subagent to `cd` into their assigned worktree path FIRST\n"
            f"2. Include the worktree path in the subagent prompt\n"
            f"3. Tell them to commit their work on their branch when done\n\n"
            f"**Your task:**\n"
            f"1. Read TASKS.md — find Sprint {phase.sprint_number} tasks.\n"
            f"2. For each assigned role, spawn a subagent with the Agent tool.\n"
            f"3. Give each subagent: their tasks, their worktree path, and context from PROJECT.md.\n"
            f"4. Wait for all subagents to complete.\n"
            f"5. Update TASKS.md checkboxes based on results.\n\n"
            f"Delegate ALL work. You are the coordinator."
            + SUBAGENT_REPORT_INSTRUCTION
        )

    elif phase.phase_type == WorkflowPhaseType.SPRINT_REVIEW:
        wt_table = _worktree_table(wf.worktrees)
        return (
            f"## WORKFLOW PHASE: Sprint {phase.sprint_number} Review\n\n"
            f"Sprint execution is complete. Now review the work.\n\n"
            f"**Worktrees with completed work:**\n{wt_table}\n\n"
            f"**Your task:**\n"
            f"1. For each worktree with engineer work, spawn a QA subagent to review.\n"
            f"   Tell the subagent to `cd` into the worktree path and review all changes.\n"
            f"2. QA should check: correctness, code quality, tests passing, edge cases.\n"
            f"3. Collect review results from all subagents.\n"
            f"4. Summarize: what passed review, what needs fixes.\n\n"
            f"**Output format:**\n"
            f"## Sprint {phase.sprint_number} Review\n"
            f"- [x] Items that passed review\n"
            f"- [ ] Items that need fixes\n"
            + SUBAGENT_REPORT_INSTRUCTION
        )

    elif phase.phase_type == WorkflowPhaseType.SPRINT_RETRO:
        return (
            f"## WORKFLOW PHASE: Sprint {phase.sprint_number} Retrospective\n\n"
            f"Sprint {phase.sprint_number} is complete. Worktree branches have been merged to main.\n\n"
            f"**Your task:**\n"
            f"1. Read TASKS.md — count completed vs remaining tasks for Sprint {phase.sprint_number}.\n"
            f"2. Generate a sprint report:\n"
            f"   - Tasks completed / total\n"
            f"   - Key accomplishments\n"
            f"   - Issues encountered\n"
            f"   - Velocity assessment\n"
            f"3. If there are remaining sprints, note any tasks to carry over.\n\n"
            f"**Output format:**\n"
            f"## Sprint {phase.sprint_number} Report\n"
            f"**Completed**: N/M tasks\n"
            f"**Highlights**: ...\n"
            f"**Carry-over**: ...\n"
            f"**Velocity**: on track / behind / ahead\n"
        )

    elif phase.phase_type == WorkflowPhaseType.COMPLETE:
        return (
            f"## WORKFLOW COMPLETE\n\n"
            f"All {wf.config.total_sprints} sprints are finished.\n\n"
            f"**Your task:**\n"
            f"1. Read TASKS.md — generate a final quarter report.\n"
            f"2. Summarize: total tasks completed, overall progress, key deliverables.\n"
            f"3. Note any remaining backlog items.\n\n"
            f"**Output format:**\n"
            f"## Quarter Report\n"
            f"**Duration**: {wf.config.total_sprints} sprints\n"
            f"**Total tasks**: X completed / Y total\n"
            f"**Key deliverables**: ...\n"
            f"**Remaining backlog**: ...\n"
        )

    return f"Unknown phase type: {phase.phase_type}"
