"""Cron service — scheduled task management with file-based JSON storage."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from croniter import croniter

logger = logging.getLogger(__name__)

CRON_BASE = Path.home() / ".claude" / "cron"


# ─── Models ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_job(
    *,
    name: str,
    schedule: str,
    task: str,
    project: str,
    enabled: bool = True,
) -> dict[str, Any]:
    """Create a new cron job dict."""
    now = datetime.now(timezone.utc)
    cron = croniter(schedule, now)
    next_run = cron.get_next(datetime).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "schedule": schedule,
        "task": task,
        "project": project,
        "enabled": enabled,
        "last_run": None,
        "next_run": next_run,
        "run_count": 0,
        "created_at": _now_iso(),
    }


# ─── Storage ─────────────────────────────────────────────────────────────────


def _jobs_path(project: str) -> Path:
    return CRON_BASE / project / "jobs.json"


def _read_jobs(project: str) -> list[dict[str, Any]]:
    p = _jobs_path(project)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read cron jobs for %s: %s", project, e)
        return []


def _write_jobs(project: str, jobs: list[dict[str, Any]]) -> None:
    p = _jobs_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(jobs, indent=2))


def _recalc_next_run(job: dict[str, Any]) -> None:
    """Recalculate next_run based on now."""
    try:
        now = datetime.now(timezone.utc)
        cron = croniter(job["schedule"], now)
        job["next_run"] = cron.get_next(datetime).isoformat()
    except Exception:
        job["next_run"] = None


# ─── CRUD ────────────────────────────────────────────────────────────────────


def list_jobs(project: str) -> list[dict[str, Any]]:
    jobs = _read_jobs(project)
    # Recalculate next_run for enabled jobs
    for j in jobs:
        if j.get("enabled"):
            _recalc_next_run(j)
    return jobs


def create_job(
    project: str,
    name: str,
    schedule: str,
    task: str,
    enabled: bool = True,
) -> dict[str, Any]:
    # Validate cron expression
    if not croniter.is_valid(schedule):
        raise ValueError(f"Invalid cron expression: {schedule}")

    jobs = _read_jobs(project)
    job = _make_job(name=name, schedule=schedule, task=task, project=project, enabled=enabled)
    jobs.append(job)
    _write_jobs(project, jobs)
    return job


def update_job(
    project: str,
    job_id: str,
    **updates: Any,
) -> dict[str, Any]:
    jobs = _read_jobs(project)
    for j in jobs:
        if j["id"] == job_id:
            for key in ("name", "schedule", "task", "enabled"):
                if key in updates and updates[key] is not None:
                    j[key] = updates[key]
            # Re-validate schedule if changed
            if "schedule" in updates and updates["schedule"] is not None:
                if not croniter.is_valid(j["schedule"]):
                    raise ValueError(f"Invalid cron expression: {j['schedule']}")
            _recalc_next_run(j)
            _write_jobs(project, jobs)
            return j
    raise ValueError(f"Job not found: {job_id}")


def delete_job(project: str, job_id: str) -> bool:
    jobs = _read_jobs(project)
    new_jobs = [j for j in jobs if j["id"] != job_id]
    if len(new_jobs) == len(jobs):
        raise ValueError(f"Job not found: {job_id}")
    _write_jobs(project, new_jobs)
    return True


def get_job(project: str, job_id: str) -> dict[str, Any] | None:
    jobs = _read_jobs(project)
    for j in jobs:
        if j["id"] == job_id:
            if j.get("enabled"):
                _recalc_next_run(j)
            return j
    return None


def mark_job_run(project: str, job_id: str) -> None:
    """Update last_run, run_count, and next_run after a job executes."""
    jobs = _read_jobs(project)
    for j in jobs:
        if j["id"] == job_id:
            j["last_run"] = _now_iso()
            j["run_count"] = j.get("run_count", 0) + 1
            _recalc_next_run(j)
            break
    _write_jobs(project, jobs)


# ─── Scheduler loop ─────────────────────────────────────────────────────────


def get_all_enabled_jobs() -> list[dict[str, Any]]:
    """Scan all projects for enabled cron jobs."""
    result: list[dict[str, Any]] = []
    if not CRON_BASE.exists():
        return result
    for project_dir in CRON_BASE.iterdir():
        if project_dir.is_dir():
            jobs = _read_jobs(project_dir.name)
            for j in jobs:
                if j.get("enabled"):
                    result.append(j)
    return result


def get_due_jobs() -> list[dict[str, Any]]:
    """Return jobs whose next_run is in the past (i.e., due now)."""
    now = datetime.now(timezone.utc)
    due = []
    for job in get_all_enabled_jobs():
        next_run_str = job.get("next_run")
        if not next_run_str:
            continue
        try:
            next_run = datetime.fromisoformat(next_run_str)
            # Ensure timezone-aware comparison
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)
            if next_run <= now:
                due.append(job)
        except (ValueError, TypeError):
            continue
    return due


async def cron_tick(dispatch_fn) -> int:
    """Check for due jobs and dispatch them. Returns number of jobs fired.

    dispatch_fn should be an async callable:
        async def dispatch(project_name: str, task: str) -> None
    """
    due = get_due_jobs()
    fired = 0
    for job in due:
        project = job["project"]
        task = job["task"]
        job_id = job["id"]
        try:
            logger.info("Cron firing job %s (%s) for project %s", job["name"], job_id, project)
            await dispatch_fn(project, task)
            mark_job_run(project, job_id)
            fired += 1
        except Exception as e:
            logger.error("Cron job %s failed: %s", job_id, e)
    return fired


async def cron_loop(dispatch_fn, interval: float = 30.0) -> None:
    """Background loop that checks for due cron jobs every `interval` seconds."""
    while True:
        try:
            await cron_tick(dispatch_fn)
        except Exception as e:
            logger.error("Cron loop error: %s", e)
        await asyncio.sleep(interval)
