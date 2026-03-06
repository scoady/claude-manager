"""Claude Agent Manager — FastAPI backend server."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

logger = logging.getLogger(__name__)
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel as PydanticBaseModel

from .models import (
    AddTaskRequest,
    Agent,
    AgentStatus,
    BootstrapProjectRequest,
    CreateSkillRequest,
    CreateWorkflowRequest,
    DispatchRequest,
    GlobalStats,
    InjectRequest,
    ManagedProject,
    PlanTaskRequest,
    ProjectConfig,
    RolePreset,
    SessionPhase,
    SkillInfo,
    UpdateTaskRequest,
    WidgetCreate,
    WidgetState,
    WidgetUpdate,
    WorkflowActionRequest,
    WSMessageType,
)
from .broker import AgentBroker
from .rules import RulesEngine
from .services.database import Database
from .rules.builtin_rules import SessionHealthRule
from .services import milestones as milestones_svc
from .services import workflows as workflows_svc
from .services import projects as projects_svc
from .services import settings as settings_svc
from .services import skills as skills_svc
from .services import tasks as tasks_svc
from .services import roles as roles_svc
from .services import artifacts as artifacts_svc
from .services import cron as cron_svc
from .services import templates as templates_svc
from .services.metrics import metrics_service
from .services import widget_catalog as widget_catalog_svc
from .services.canvas import canvas_service
from .ws_manager import WSManager

# ─── Singletons ───────────────────────────────────────────────────────────────

ws_manager = WSManager()
_start_time = time.time()


# ─── Agent widget helpers ─────────────────────────────────────────────────────


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_template_catalog_docs() -> str:
    """Build dynamic template documentation from the widget catalog for agent prompts."""
    templates = widget_catalog_svc.list_templates()
    if not templates:
        return "No widget templates available.\n"

    lines = []
    for t in templates:
        tid = t.get("id", "?")
        name = t.get("name", "Untitled")
        category = t.get("category", "custom")
        desc = t.get("description", "")
        schema = t.get("data_schema", {})

        lines.append(f'- **"{tid}"** ({name}, {category}): {desc}')
        if schema:
            fields = []
            for key, info in schema.items():
                ftype = info.get("type", "string") if isinstance(info, dict) else "string"
                fdesc = info.get("description", "") if isinstance(info, dict) else ""
                fields.append(f"    - `{key}` ({ftype}){': ' + fdesc if fdesc else ''}")
            lines.append("  Data fields:")
            lines.extend(fields)

    return "\n".join(lines)


# ─── Background tasks ─────────────────────────────────────────────────────────


async def _broadcast_project_list(broker: AgentBroker) -> None:
    active_map: dict[str, list[str]] = {}
    for s in broker.get_all_sessions():
        if s.project_name == "__global__":
            continue
        active_map.setdefault(s.project_name, []).append(s.session_id)
    project_list = projects_svc.list_projects(active_map)
    await ws_manager.broadcast(
        WSMessageType.PROJECT_LIST,
        [p.model_dump() for p in project_list],
    )


async def _project_refresh_task(broker: AgentBroker) -> None:
    while True:
        await asyncio.sleep(5.0)
        try:
            await _broadcast_project_list(broker)
        except Exception as exc:
            print(f"[project-refresh] error: {exc}")


async def _stats_task(broker: AgentBroker) -> None:
    while True:
        await asyncio.sleep(2.0)
        try:
            stats = _compute_stats(broker)
            await ws_manager.broadcast(WSMessageType.STATS_UPDATE, stats.model_dump())
        except Exception as exc:
            print(f"[stats] error: {exc}")


_task_cache: dict[str, list[dict]] = {}


async def _notify_controller_queue(project_name: str, context: str) -> None:
    """Notify global controller about task changes, fall back to direct dispatch."""
    broker: AgentBroker = app.state.broker
    try:
        controller = broker.get_global_controller()
        if controller and controller.phase == SessionPhase.IDLE:
            await broker.inject_message(
                controller.session_id,
                f'Project: "{project_name}"\n{context}\n'
                f'Check list_tasks(project="{project_name}") and dispatch_agent() if needed.',
            )
            return
        # Controller busy or missing — fall back to direct dispatch
        await broker.check_task_queue(project_name)
    except Exception as exc:
        print(f"[task-queue] notify error: {exc}")


async def _controller_health_task(broker: AgentBroker, mcp_config_path: str) -> None:
    """Respawn the global controller if it dies."""
    while True:
        await asyncio.sleep(10.0)
        try:
            controller = broker.get_global_controller()
            if not controller:
                logger.warning("Global controller died, respawning...")
                await broker.spawn_global_controller(mcp_config_path=mcp_config_path)
                logger.info("Global controller respawned")
        except Exception as exc:
            logger.error("Controller health check error: %s", exc)


async def _metrics_snapshot_task(broker: AgentBroker) -> None:
    """Capture metrics snapshot every 30 seconds."""
    while True:
        await asyncio.sleep(30.0)
        try:
            sessions = broker.get_all_sessions()
            # Gather task state for all active projects
            active_projects: set[str] = set()
            for s in sessions:
                active_projects.add(s.project_name)
            tasks_by_project: dict[str, list[dict]] = {}
            loop = asyncio.get_event_loop()
            for name in active_projects:
                try:
                    tasks = await loop.run_in_executor(None, tasks_svc.get_tasks, name)
                    tasks_by_project[name] = tasks
                except Exception:
                    pass
            metrics_service.snapshot(sessions, tasks_by_project)
        except Exception as exc:
            print(f"[metrics-snapshot] error: {exc}")


async def _task_poll_task(broker: AgentBroker) -> None:
    """Poll TASKS.md every 3s for projects with active agents, broadcast changes."""
    while True:
        await asyncio.sleep(3.0)
        try:
            active_projects: set[str] = set()
            for s in broker.get_all_sessions():
                if s.phase not in (SessionPhase.CANCELLED, SessionPhase.ERROR):
                    active_projects.add(s.project_name)

            for name in active_projects:
                loop = asyncio.get_event_loop()
                tasks = await loop.run_in_executor(None, tasks_svc.get_tasks, name)
                cached = _task_cache.get(name)
                if tasks != cached:
                    # Check if new pending tasks appeared (controller wrote TASKS.md)
                    old_pending = {t["index"] for t in (cached or []) if t["status"] == "pending"}
                    new_pending = {t["index"] for t in tasks if t["status"] == "pending"}
                    has_new_tasks = bool(new_pending - old_pending)

                    _task_cache[name] = tasks
                    await ws_manager.broadcast(WSMessageType.TASKS_UPDATED, {
                        "project_name": name,
                        "tasks": tasks,
                    })

                    # Auto-dispatch if new pending tasks detected
                    if has_new_tasks:
                        try:
                            await broker.check_task_queue(name)
                        except Exception as exc:
                            print(f"[task-poll] auto-dispatch error: {exc}")
        except Exception as exc:
            print(f"[task-poll] error: {exc}")


def _compute_stats(broker: AgentBroker) -> GlobalStats:
    sessions = [s for s in broker.get_all_sessions() if s.project_name != "__global__"]
    working = sum(
        1 for s in sessions
        if s.phase not in (SessionPhase.IDLE, SessionPhase.CANCELLED, SessionPhase.ERROR)
    )
    idle = sum(1 for s in sessions if s.phase == SessionPhase.IDLE)
    projects = projects_svc.list_projects()
    return GlobalStats(
        total_projects=len(projects),
        total_agents=len(sessions),
        working_agents=working,
        idle_agents=idle,
        uptime_seconds=time.time() - _start_time,
    )


# ─── Cron dispatch helper ──────────────────────────────────────────────────────


def _make_cron_dispatch(broker: AgentBroker):
    """Return an async callable that dispatches a cron task through the broker."""

    async def _dispatch(project_name: str, task: str) -> None:
        loop = asyncio.get_event_loop()
        project = await loop.run_in_executor(
            None, projects_svc.get_project, project_name, []
        )
        if not project:
            logger.warning("Cron: project %s not found, skipping", project_name)
            return
        model = project.config.model
        mcp_config = project.config.mcp_config
        await broker.create_session(
            project_name=project_name,
            project_path=project.path,
            initial_task=f"[Cron scheduled task] {task}",
            model=model,
            mcp_config_path=mcp_config,
        )

    return _dispatch


# ─── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Optional PostgreSQL persistence
    db: Database | None = None
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        db = Database()
        await db.init(database_url)

    broker = AgentBroker(ws_manager=ws_manager, db=db)
    rules = RulesEngine(broker=broker, ws_manager=ws_manager, tick_interval=30.0)

    # Built-in rules
    rules.register(SessionHealthRule(
        rule_id="builtin-session-health",
        name="Session Health Monitor",
        error_timeout_seconds=120.0,
        cooldown_seconds=60.0,
    ))

    app.state.broker = broker
    app.state.rules = rules

    # Spawn global controller
    controller_mcp = str(Path(__file__).resolve().parent / "mcp" / "controller_mcp_config.json")
    try:
        await broker.spawn_global_controller(mcp_config_path=controller_mcp)
        logger.info("Global controller spawned")
    except Exception as exc:
        logger.error("Failed to spawn global controller: %s", exc)

    tasks = [
        asyncio.create_task(_project_refresh_task(broker)),
        asyncio.create_task(_stats_task(broker)),
        asyncio.create_task(_task_poll_task(broker)),
        asyncio.create_task(_metrics_snapshot_task(broker)),
        asyncio.create_task(cron_svc.cron_loop(_make_cron_dispatch(broker))),
        asyncio.create_task(_controller_health_task(broker, controller_mcp)),
        rules.start(),
    ]

    yield

    rules.stop()
    for s in broker.get_all_sessions():
        s.cancel()
    for t in tasks:
        t.cancel()
    if db:
        await db.close()


# ─── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Claude Agent Manager",
    description="Agent orchestration dashboard for managed projects",
    version="3.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Projects API ─────────────────────────────────────────────────────────────


@app.get("/api/projects", response_model=list[ManagedProject])
async def list_projects() -> list[ManagedProject]:
    broker: AgentBroker = app.state.broker
    active_map: dict[str, list[str]] = {}
    for s in broker.get_all_sessions():
        if s.project_name == "__global__":
            continue
        active_map.setdefault(s.project_name, []).append(s.session_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, projects_svc.list_projects, active_map)


@app.post("/api/projects", response_model=ManagedProject, status_code=201)
async def create_project(body: BootstrapProjectRequest) -> ManagedProject:
    broker: AgentBroker = app.state.broker
    try:
        loop = asyncio.get_event_loop()
        project = await loop.run_in_executor(
            None, projects_svc.bootstrap_project, body.name, body.description
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Apply model override from the create form
    if body.model:
        project.config.model = body.model
        projects_svc.update_project_config(body.name, project.config)

    await _broadcast_project_list(broker)

    # Notify global controller about the new project so it can plan tasks
    controller = broker.get_global_controller()
    if controller:
        project_name = project.name
        setup_prompt = (
            f'New project created: "{project_name}"\n'
            f'Path: {project.path}\n\n'
            f'Read PROJECT.md at that path to understand the goal, then use '
            f'create_tasks(project="{project_name}", tasks=[...]) to add tasks.\n'
            f'Each task should be a single actionable unit of work, ordered by dependencies.\n'
            f'Be specific. Workers will be auto-dispatched for each task you create.\n'
            f'Narrate what you\'re doing — the user sees your output in real-time.'
        )
        await broker.inject_message(controller.session_id, setup_prompt)

    return project


@app.get("/api/projects/{name}", response_model=ManagedProject)
async def get_project(name: str) -> ManagedProject:
    broker: AgentBroker = app.state.broker
    active_sessions = [s.session_id for s in broker.get_sessions_for_project(name)]
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(None, projects_svc.get_project, name, active_sessions)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.put("/api/projects/{name}/config", response_model=ProjectConfig)
async def update_project_config(name: str, config: ProjectConfig) -> ProjectConfig:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, projects_svc.update_project_config, name, config)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return config


@app.delete("/api/projects/{name}", status_code=204)
async def delete_project(name: str) -> None:
    broker: AgentBroker = app.state.broker
    # Kill all agents for this project
    for s in broker.get_sessions_for_project(name):
        await broker.cancel_session(s.session_id)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, projects_svc.delete_project, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await _broadcast_project_list(broker)


@app.post("/api/projects/{name}/dispatch", status_code=202)
async def dispatch_task(name: str, body: DispatchRequest) -> dict[str, Any]:
    broker: AgentBroker = app.state.broker
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(None, projects_svc.get_project, name, [])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Track dispatched work in TASKS.md so it appears on the Tasks tab
    tasks = await loop.run_in_executor(None, tasks_svc.add_task, name, body.task)
    # Mark as in-progress since we're dispatching immediately
    new_task_idx = len(tasks) - 1
    tasks = await loop.run_in_executor(
        None, tasks_svc.update_task_status, name, new_task_idx, "in_progress"
    )
    await ws_manager.broadcast(WSMessageType.TASKS_UPDATED, {
        "project_name": name,
        "tasks": tasks,
    })

    # Route through global controller
    controller = broker.get_global_controller()
    if controller and controller.phase == SessionPhase.IDLE:
        task_prompt = (
            f'Project: "{name}"\n'
            f'New task (TASKS.md index #{new_task_idx}): "{body.task}"\n\n'
            f'Handle this task. Use dispatch_agent(project="{name}", task_index={new_task_idx}) '
            f'to spawn a worker, or answer directly if it\'s a simple request.\n'
            f'Narrate what you\'re doing — the user sees your output.'
        )
        await broker.inject_message(controller.session_id, task_prompt)
        return {"status": "delegated", "session_ids": [controller.session_id]}

    # Fallback: controller busy or missing — spawn single standalone agent
    model = body.model or project.config.model
    mcp_config = project.config.mcp_config or str(
        Path(__file__).resolve().parent / "mcp" / "controller_mcp_config.json"
    )

    narrated_task = (
        "Narrate what you're doing in plain English as you work. "
        "The user sees your text on a live dashboard. Write short status updates "
        "before tool calls and summarize results after. Never go silent.\n\n"
        f"{body.task}"
    )

    session = await broker.create_session(
        project_name=name,
        project_path=project.path,
        initial_task=narrated_task,
        model=model,
        mcp_config_path=mcp_config,
        task_index=new_task_idx,
    )

    return {"status": "dispatched", "session_ids": [session.session_id]}


# ─── Tasks API ────────────────────────────────────────────────────────────────


@app.get("/api/projects/{name}/tasks")
async def get_tasks(name: str) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, tasks_svc.get_tasks, name)


@app.post("/api/projects/{name}/tasks", status_code=201)
async def add_task(name: str, body: AddTaskRequest) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    tasks = await loop.run_in_executor(None, tasks_svc.add_task, name, body.text)
    await ws_manager.broadcast(WSMessageType.TASKS_UPDATED, {
        "project_name": name,
        "tasks": tasks,
    })
    # Notify controller about new work
    await _notify_controller_queue(name, f'New task added: "{body.text}"')
    return tasks


@app.put("/api/projects/{name}/tasks/{task_index}")
async def update_task(name: str, task_index: int, body: UpdateTaskRequest) -> list[dict[str, Any]]:
    try:
        loop = asyncio.get_event_loop()
        tasks = await loop.run_in_executor(
            None, tasks_svc.update_task_status, name, task_index, body.status
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await ws_manager.broadcast(WSMessageType.TASKS_UPDATED, {
        "project_name": name,
        "tasks": tasks,
    })
    return tasks


@app.delete("/api/projects/{name}/tasks/{task_index}")
async def delete_task(name: str, task_index: int) -> list[dict[str, Any]]:
    try:
        loop = asyncio.get_event_loop()
        tasks = await loop.run_in_executor(None, tasks_svc.delete_task, name, task_index)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await ws_manager.broadcast(WSMessageType.TASKS_UPDATED, {
        "project_name": name,
        "tasks": tasks,
    })
    return tasks


@app.post("/api/projects/{name}/tasks/plan", status_code=202)
async def plan_task(name: str, body: PlanTaskRequest) -> dict[str, Any]:
    broker: AgentBroker = app.state.broker
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(None, projects_svc.get_project, name, [])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # First add the task to TASKS.md
    await loop.run_in_executor(None, tasks_svc.add_task, name, body.text)

    plan_prompt = (
        f'Read PROJECT.md and TASKS.md in this project. '
        f'A new task has been added: "{body.text}". '
        f'Break this task into concrete, actionable sub-tasks and update TASKS.md. '
        f'Add sub-tasks as indented checkboxes under the main task. '
        f'Do NOT execute any tasks or write any code. Only plan and update TASKS.md. '
        f'When done, write a brief summary of the plan.'
    )

    # Route through global controller if idle
    controller = broker.get_global_controller()
    if controller and controller.phase == SessionPhase.IDLE:
        await broker.inject_message(
            controller.session_id,
            f'Project: "{name}"\n{plan_prompt}',
        )
        return {"status": "planning", "session_id": controller.session_id}

    # Fallback: spawn standalone agent
    model = body.model or project.config.model
    session = await broker.create_session(
        project_name=name,
        project_path=project.path,
        initial_task=plan_prompt,
        model=model,
    )

    return {"status": "planning", "session_id": session.session_id}


@app.post("/api/projects/{name}/tasks/{task_index}/start", status_code=202)
async def start_task(name: str, task_index: int, body: DispatchRequest | None = None) -> dict[str, Any]:
    broker: AgentBroker = app.state.broker
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(None, projects_svc.get_project, name, [])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    tasks = tasks_svc.get_tasks(name)
    if task_index >= len(tasks):
        raise HTTPException(status_code=404, detail="Task not found")

    task = tasks[task_index]

    # Mark task as in-progress
    await loop.run_in_executor(
        None, tasks_svc.update_task_status, name, task_index, "in_progress"
    )

    task_prompt = (
        f'Start this task from TASKS.md: "{task["text"]}"\n\n'
        f'Use dispatch_agent(project="{name}", task_index={task_index}) to spawn a worker agent. '
        f'Monitor with get_agents(). When complete, use report_complete(project="{name}", task_index={task_index}, summary="..."). '
        f'Do NOT implement anything yourself — you coordinate via MCP tools only.'
    )

    await ws_manager.broadcast(WSMessageType.TASKS_UPDATED, {
        "project_name": name,
        "tasks": tasks_svc.get_tasks(name),
    })

    # Route through global controller if idle
    controller = broker.get_global_controller()
    if controller and controller.phase == SessionPhase.IDLE:
        await broker.inject_message(
            controller.session_id,
            f'Project: "{name}"\n{task_prompt}',
        )
        return {"status": "delegated", "session_id": controller.session_id, "task": task["text"]}

    # Fallback: spawn standalone agent with its own dashboard widget
    model = (body.model if body else None) or project.config.model
    session = await broker.create_session(
        project_name=name,
        project_path=project.path,
        initial_task=task_prompt,
        model=model,
        task_index=task_index,
    )

    return {"status": "started", "session_id": session.session_id, "task": task["text"]}


@app.post("/api/projects/{name}/tasks/{task_index}/complete")
async def complete_task(name: str, task_index: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark a task as completed. Called by orchestrator MCP report_complete tool."""
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(None, projects_svc.get_project, name, [])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    tasks = tasks_svc.get_tasks(name)
    if task_index >= len(tasks):
        raise HTTPException(status_code=404, detail="Task not found")

    # Mark done
    await loop.run_in_executor(
        None, tasks_svc.update_task_status, name, task_index, "done"
    )

    updated_tasks = tasks_svc.get_tasks(name)
    await ws_manager.broadcast(WSMessageType.TASKS_UPDATED, {
        "project_name": name,
        "tasks": updated_tasks,
    })

    # Record milestone if summary provided
    summary = (body or {}).get("summary", "")
    if summary:
        milestones_svc.add_milestone(
            project_name=name,
            session_id="orchestrator",
            task=tasks[task_index].get("text", ""),
            summary=summary,
            agent_type="orchestrator",
            model="",
        )
        all_milestones = milestones_svc.get_milestones(name)
        await ws_manager.broadcast(WSMessageType.MILESTONES_UPDATED, {
            "project_name": name,
            "milestones": all_milestones,
        })

    # Notify controller to pick up next pending task
    await _notify_controller_queue(name, f'Task #{task_index} "{tasks[task_index].get("text", "")}" is complete.')

    return {"status": "completed", "task": tasks[task_index].get("text", "")}


# ─── Orchestrator API ────────────────────────────────────────────────────────


@app.post("/api/projects/{name}/orchestrator")
async def ensure_orchestrator(name: str):
    """Ensure a controller agent is alive and has a fresh status update."""
    broker: AgentBroker = app.state.broker
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(None, projects_svc.get_project, name, [])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    controller = broker.get_global_controller()

    if controller:
        if controller.phase == SessionPhase.IDLE:
            await broker.inject_message(
                controller.session_id,
                f'Project: "{name}"\n'
                "Give a brief project status update: Read TASKS.md. "
                "What tasks are complete, what's in progress, what's next? "
                "Be concise — 2-3 sentences max.",
            )
            return {"status": "refreshed", "session_id": controller.session_id}
        else:
            return {"status": "active", "session_id": controller.session_id}
    else:
        # Global controller dead — try to respawn it
        controller_mcp = str(
            Path(__file__).resolve().parent / "mcp" / "controller_mcp_config.json"
        )
        session = await broker.spawn_global_controller(mcp_config_path=controller_mcp)
        return {"status": "spawned", "session_id": session.session_id}


# ─── Milestones API ────────────────────────────────────────────────────────


@app.get("/api/projects/{name}/milestones")
async def get_milestones(name: str) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, milestones_svc.get_milestones, name)


@app.delete("/api/projects/{name}/milestones/{milestone_id}")
async def delete_milestone(name: str, milestone_id: str) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    milestones = await loop.run_in_executor(
        None, milestones_svc.delete_milestone, name, milestone_id
    )
    await ws_manager.broadcast(WSMessageType.MILESTONES_UPDATED, {
        "project_name": name,
        "milestones": milestones,
    })
    return milestones


@app.delete("/api/projects/{name}/milestones")
async def clear_milestones(name: str) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    milestones = await loop.run_in_executor(
        None, milestones_svc.clear_milestones, name
    )
    await ws_manager.broadcast(WSMessageType.MILESTONES_UPDATED, {
        "project_name": name,
        "milestones": milestones,
    })
    return milestones


# ─── Templates API ────────────────────────────────────────────────────────────


@app.get("/api/templates")
async def list_templates() -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    templates = await loop.run_in_executor(None, templates_svc.list_templates)
    return [t.model_dump() for t in templates]


@app.get("/api/templates/{template_id}")
async def get_template(template_id: str) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    tpl = await loop.run_in_executor(None, templates_svc.get_template, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl.model_dump()


@app.post("/api/templates", status_code=201)
async def create_template(body: dict[str, Any]) -> dict[str, Any]:
    from .models import WorkflowTemplate
    try:
        tpl = WorkflowTemplate(**body)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, templates_svc.create_custom_template, tpl)
        return tpl.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─── Workflow API ─────────────────────────────────────────────────────────────


@app.get("/api/projects/{name}/workflow")
async def get_workflow(name: str) -> dict[str, Any] | None:
    loop = asyncio.get_event_loop()
    wf = await loop.run_in_executor(None, workflows_svc.get_workflow, name)
    return wf.model_dump() if wf else None


@app.post("/api/projects/{name}/workflow", status_code=201)
async def create_workflow(name: str, body: CreateWorkflowRequest) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(None, projects_svc.get_project, name, [])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        wf = await loop.run_in_executor(
            None, workflows_svc.create_workflow, name, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return wf.model_dump()


@app.post("/api/projects/{name}/workflow/start", status_code=200)
async def start_workflow(name: str) -> dict[str, Any]:
    broker: AgentBroker = app.state.broker
    loop = asyncio.get_event_loop()

    try:
        wf, prompt = await loop.run_in_executor(
            None, workflows_svc.start_workflow, name
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Update INSTRUCTIONS.md with template's workflow overlay
    tpl = templates_svc.get_template(wf.template_id)
    if tpl and tpl.instructions_overlay:
        await loop.run_in_executor(
            None, projects_svc.update_instructions_overlay, name, tpl.instructions_overlay
        )

    # Inject first phase prompt into global controller
    controller = broker.get_global_controller()
    if controller and controller.phase == SessionPhase.IDLE:
        await broker.inject_message(
            controller.session_id,
            f'Project: "{name}"\n{prompt}',
        )
    else:
        # Controller busy — queue prompt for when it's idle (inject queues it)
        if controller:
            await broker.inject_message(
                controller.session_id,
                f'Project: "{name}"\n{prompt}',
            )

    await ws_manager.broadcast(WSMessageType.WORKFLOW_UPDATED, {
        "project_name": name,
        "workflow": wf.model_dump(),
    })
    return wf.model_dump()


@app.post("/api/projects/{name}/workflow/action", status_code=200)
async def workflow_action(name: str, body: WorkflowActionRequest) -> dict[str, Any]:
    broker: AgentBroker = app.state.broker
    loop = asyncio.get_event_loop()

    try:
        if body.action == "pause":
            wf = await loop.run_in_executor(
                None, workflows_svc.pause_workflow, name
            )
            result = wf.model_dump()
        elif body.action == "resume":
            wf, prompt = await loop.run_in_executor(
                None, workflows_svc.resume_workflow, name
            )
            if prompt:
                controller = broker.get_global_controller()
                if controller:
                    await broker.inject_message(
                        controller.session_id,
                        f'Project: "{name}"\n{prompt}',
                    )
            result = wf.model_dump()
        elif body.action == "skip_phase":
            wf, prompt = await loop.run_in_executor(
                None, workflows_svc.advance_phase, name
            )
            if prompt:
                controller = broker.get_global_controller()
                if controller:
                    await broker.inject_message(
                        controller.session_id,
                        f'Project: "{name}"\n{prompt}',
                    )
            result = wf.model_dump() if wf else {}
        elif body.action == "cancel":
            await loop.run_in_executor(
                None, workflows_svc.delete_workflow, name
            )
            result = {"status": "cancelled"}
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await ws_manager.broadcast(WSMessageType.WORKFLOW_UPDATED, {
        "project_name": name,
        "workflow": result,
    })
    return result


@app.delete("/api/projects/{name}/workflow", status_code=204)
async def delete_workflow(name: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, workflows_svc.delete_workflow, name)
    await ws_manager.broadcast(WSMessageType.WORKFLOW_UPDATED, {
        "project_name": name,
        "workflow": None,
    })


# ─── Agents API ────────────────────────────────────────────────────────────────


@app.get("/api/agents")
async def list_agents() -> list[dict[str, Any]]:
    broker: AgentBroker = app.state.broker
    return [
        s.to_dict() for s in broker.get_all_sessions()
        if s.project_name != "__global__"
    ]


@app.get("/api/agents/{session_id}/messages")
async def get_agent_messages(session_id: str) -> list[Any]:
    broker: AgentBroker = app.state.broker
    session = broker.get_session(session_id)
    if not session:
        return []
    return session.get_messages()


@app.post("/api/agents/{session_id}/inject")
async def inject_message(session_id: str, body: InjectRequest) -> dict[str, Any]:
    broker: AgentBroker = app.state.broker
    ok = await broker.inject_message(session_id, body.message)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    session = broker.get_session(session_id)
    return {"status": "queued" if session and session.phase != SessionPhase.IDLE else "sent"}


@app.delete("/api/agents/{session_id}", status_code=204)
async def kill_agent(session_id: str) -> None:
    broker: AgentBroker = app.state.broker
    ok = await broker.cancel_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    await _broadcast_project_list(broker)


# ─── Rules API ─────────────────────────────────────────────────────────────────


@app.get("/api/rules")
async def list_rules() -> list[dict[str, Any]]:
    rules: RulesEngine = app.state.rules
    return [r.to_dict() for r in rules.get_rules()]


# ─── Settings API ─────────────────────────────────────────────────────────────


@app.get("/api/settings/global")
async def get_global_settings() -> dict[str, Any]:
    return settings_svc.get_global_settings()


@app.put("/api/settings/global")
async def put_global_settings(body: dict[str, Any]) -> dict[str, Any]:
    try:
        settings_svc.set_global_settings(body)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return body


@app.get("/api/settings/plugins")
async def get_plugins() -> list[dict[str, Any]]:
    return settings_svc.get_plugins()


@app.post("/api/settings/plugins/{plugin_id:path}/enable")
async def enable_plugin(plugin_id: str) -> dict[str, Any]:
    settings_svc.set_plugin_enabled(plugin_id, True)
    return {"id": plugin_id, "enabled": True}


@app.post("/api/settings/plugins/{plugin_id:path}/disable")
async def disable_plugin(plugin_id: str) -> dict[str, Any]:
    settings_svc.set_plugin_enabled(plugin_id, False)
    return {"id": plugin_id, "enabled": False}


# ─── Skills API ──────────────────────────────────────────────────────────────


@app.get("/api/skills", response_model=list[SkillInfo])
async def list_skills() -> list[SkillInfo]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, skills_svc.list_global_skills)


@app.get("/api/skills/marketplace")
async def list_marketplace() -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, skills_svc.list_marketplace_plugins)


@app.post("/api/skills", response_model=SkillInfo, status_code=201)
async def create_skill(body: CreateSkillRequest) -> SkillInfo:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, skills_svc.create_skill, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/{name}/skills", response_model=list[SkillInfo])
async def get_project_skills(name: str) -> list[SkillInfo]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, skills_svc.list_available_for_project, name)


@app.post("/api/projects/{name}/skills/{skill_name}/enable")
async def enable_project_skill(name: str, skill_name: str) -> dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, skills_svc.enable_skill_for_project, name, skill_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"project": name, "skill": skill_name, "enabled": True}


@app.post("/api/projects/{name}/skills/{skill_name}/disable")
async def disable_project_skill(name: str, skill_name: str) -> dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, skills_svc.disable_skill_for_project, name, skill_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"project": name, "skill": skill_name, "enabled": False}


# ─── Roles API ──────────────────────────────────────────────────────────────


@app.get("/api/roles", response_model=list[RolePreset])
async def list_custom_roles() -> list[RolePreset]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, roles_svc.list_roles)


@app.get("/api/roles/all")
async def get_all_roles(template_id: str | None = None) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    roles = await loop.run_in_executor(None, roles_svc.get_all_roles, template_id)
    return [r.model_dump() for r in roles]


@app.post("/api/roles", response_model=RolePreset, status_code=201)
async def create_role(body: RolePreset) -> RolePreset:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, roles_svc.create_role, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.put("/api/roles/{role_id}")
async def update_role(role_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
        role = await loop.run_in_executor(None, roles_svc.update_role, role_id, body)
        return role.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/roles/{role_id}", status_code=204)
async def delete_role(role_id: str) -> None:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, roles_svc.delete_role, role_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ─── Artifacts API ──────────────────────────────────────────────────────────


@app.get("/api/projects/{name}/files")
async def list_project_files(name: str, path: str = "") -> list[dict[str, Any]]:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, artifacts_svc.list_files, name, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/{name}/files/content")
async def read_project_file(name: str, path: str = "") -> dict[str, Any]:
    if not path:
        raise HTTPException(status_code=400, detail="path parameter required")
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, artifacts_svc.read_file, name, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/{name}/files/status")
async def get_project_git_status(name: str) -> dict[str, str]:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, artifacts_svc.get_git_status, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class WriteFileBody(PydanticBaseModel):
    path: str
    content: str


@app.put("/api/projects/{name}/files/content")
async def write_project_file(name: str, body: WriteFileBody) -> dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, artifacts_svc.write_file, name, body.path, body.content
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/projects/{name}/files/branch")
async def get_project_git_branch(name: str) -> dict[str, str]:
    try:
        loop = asyncio.get_event_loop()
        branch = await loop.run_in_executor(None, artifacts_svc.get_git_branch, name)
        return {"branch": branch}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─── Cron API ────────────────────────────────────────────────────────────────


class CronJobCreate(PydanticBaseModel):
    name: str
    schedule: str
    task: str
    enabled: bool = True


class CronJobUpdate(PydanticBaseModel):
    name: str | None = None
    schedule: str | None = None
    task: str | None = None
    enabled: bool | None = None


@app.get("/api/projects/{name}/cron")
async def list_cron_jobs(name: str) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, cron_svc.list_jobs, name)


@app.post("/api/projects/{name}/cron", status_code=201)
async def create_cron_job(name: str, body: CronJobCreate) -> dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, cron_svc.create_job, name, body.name, body.schedule, body.task, body.enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/projects/{name}/cron/{job_id}")
async def update_cron_job(name: str, job_id: str, body: CronJobUpdate) -> dict[str, Any]:
    try:
        updates = body.model_dump(exclude_none=True)

        def _do_update():
            return cron_svc.update_job(name, job_id, **updates)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_update)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/projects/{name}/cron/{job_id}")
async def delete_cron_job(name: str, job_id: str) -> dict[str, str]:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, cron_svc.delete_job, name, job_id)
        return {"status": "deleted"}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/projects/{name}/cron/{job_id}/trigger")
async def trigger_cron_job(name: str, job_id: str) -> dict[str, str]:
    loop = asyncio.get_event_loop()
    job = await loop.run_in_executor(None, cron_svc.get_job, name, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    broker: AgentBroker = app.state.broker
    dispatch_fn = _make_cron_dispatch(broker)
    await dispatch_fn(name, job["task"])
    cron_svc.mark_job_run(name, job_id)
    return {"status": "triggered"}


# ─── Canvas API ──────────────────────────────────────────────────────────────


@app.get("/api/canvas/{project}", response_model=list[WidgetState])
async def list_canvas_widgets(project: str, tab: str | None = None) -> list[WidgetState]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, canvas_service.get_widgets, project, tab)


@app.get("/api/canvas/{project}/tabs")
async def list_canvas_tabs(project: str) -> list[str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, canvas_service.get_tabs, project)


@app.post("/api/canvas/{project}/widgets", response_model=WidgetState, status_code=201)
async def create_canvas_widget(project: str, body: WidgetCreate) -> WidgetState:
    import uuid as _uuid
    # Use the caller-supplied ID if provided (e.g. from MCP canvas_put), otherwise
    # generate a fresh UUID.  This lets the MCP tool issue stable IDs so that
    # repeated canvas_put calls with the same widget_id update rather than duplicate.
    widget_id = body.id or str(_uuid.uuid4())
    loop = asyncio.get_event_loop()
    widget = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, widget_id, body
    )
    await ws_manager.broadcast(
        WSMessageType.CANVAS_WIDGET_CREATED,
        {"widget": widget.model_dump(mode="json")},
    )
    return widget


@app.put("/api/canvas/{project}/widgets/{widget_id}", response_model=WidgetState)
async def update_canvas_widget(
    project: str, widget_id: str, body: WidgetUpdate
) -> WidgetState:
    loop = asyncio.get_event_loop()
    # Check existence
    widgets = await loop.run_in_executor(None, canvas_service.get_widgets, project)
    if not any(w.id == widget_id for w in widgets):
        raise HTTPException(status_code=404, detail="Widget not found")
    updated = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, widget_id, body
    )
    await ws_manager.broadcast(
        WSMessageType.CANVAS_WIDGET_UPDATED,
        {"widget_id": widget_id, "patch": updated.model_dump(mode="json")},
    )
    return updated


@app.delete("/api/canvas/{project}/widgets/{widget_id}")
async def delete_canvas_widget(project: str, widget_id: str) -> dict[str, bool]:
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(
        None, canvas_service.delete_widget, project, widget_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Widget not found")
    await ws_manager.broadcast(
        WSMessageType.CANVAS_WIDGET_REMOVED,
        {"widget_id": widget_id},
    )
    return {"ok": True}


@app.post("/api/canvas/{project}/scene", response_model=list[WidgetState])
async def replace_canvas_scene(
    project: str, body: list[WidgetCreate]
) -> list[WidgetState]:
    loop = asyncio.get_event_loop()
    widgets = await loop.run_in_executor(
        None, canvas_service.replace_scene, project, body
    )
    await ws_manager.broadcast(
        WSMessageType.CANVAS_SCENE_REPLACED,
        {"widgets": [w.model_dump(mode="json") for w in widgets]},
    )
    return widgets


@app.post("/api/canvas/{project}/seed")
async def seed_canvas(project: str) -> dict[str, Any]:
    """Seed the dashboard with default widgets (Stellar Command + Terminal).

    Only seeds if the canvas is currently empty — avoids overwriting user changes.
    Widget templates in backend/canvas_defaults/ use {{PROJECT}} placeholders
    that are replaced with the actual project name at seed time.
    """
    loop = asyncio.get_event_loop()
    proj = await loop.run_in_executor(None, projects_svc.get_project, project, [])
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # Skip if canvas already has widgets
    existing = await loop.run_in_executor(None, canvas_service.get_widgets, project)
    if existing:
        return {"seeded": False, "count": 0, "reason": "canvas already has widgets"}

    import json as _json
    defaults_dir = Path(__file__).parent / "canvas_defaults"
    widgets_created = []

    for json_file in sorted(defaults_dir.glob("*.json")):
        try:
            raw = _json.loads(json_file.read_text("utf-8"))
            # Replace {{PROJECT}} placeholder with actual project name
            for field in ("js", "html", "css"):
                if field in raw and raw[field]:
                    raw[field] = raw[field].replace("{{PROJECT}}", project)

            widget_id = raw.pop("id", json_file.stem)
            w = WidgetCreate(**{k: v for k, v in raw.items() if k != "project"})
            widget = await loop.run_in_executor(
                None, canvas_service.upsert_widget, project, widget_id, w,
            )
            widgets_created.append(widget)
        except Exception as exc:
            print(f"[seed] failed to load {json_file.name}: {exc}")

    # Broadcast new widgets
    for w in widgets_created:
        await ws_manager.broadcast(
            WSMessageType.CANVAS_WIDGET_CREATED,
            {"widget": w.model_dump(mode="json")},
        )

    return {"seeded": True, "count": len(widgets_created)}


_DESIGN_SYSTEM_PROMPT = """You are a frontend widget designer. You produce self-contained HTML/CSS/JS
widgets that render inside a .widget-content container on the host page.

RENDERING CONTEXT:
- Your HTML goes into a .widget-content div inside a .widget-frame card
- Widgets inherit the host page's full CSS — variables, fonts, and classes are available
- You receive two JS args: `root` (the .widget-content element) and `host` (the .widget-frame element)
- The widget is ~300-500px wide, 150-400px tall (flexible grid cell)
- Background is dark — the card frame is already styled, you fill the interior

CSS VARIABLES (use these instead of hardcoded values):
  var(--bg-base): #080c14        var(--bg-surface): #0e1525
  var(--bg-elevated): #141d30    var(--bg-hover): #1a2640
  var(--bg-border): #243352      var(--bg-border-dim): #1a2640
  var(--accent-cyan): #67e8f9    var(--accent-green): #4ade80
  var(--accent-amber): #fbbf24   var(--accent-red): #f87171
  var(--accent-purple): #a78bfa  var(--accent-magenta): #c084fc
  var(--accent-teal): #5eead4    var(--accent-pink): #f9a8d4
  var(--text-primary): #e2e8f0   var(--text-secondary): #94a3b8
  var(--text-muted): #475569
  var(--font-title): 'Plus Jakarta Sans' (600-800 weight, letter-spacing: -0.02em)
  var(--font-ui): 'DM Sans' (body text)
  var(--font-mono): 'IBM Plex Mono' (data, stats, badges)
  var(--radius-sm): 6px  var(--radius-md): 10px  var(--radius-lg): 16px
  var(--shadow-glow-cyan): 0 0 16px rgba(103,232,249,0.25)
  var(--shadow-glow-green): 0 0 14px rgba(74,222,128,0.25)

CAPABILITIES:
- Inline SVG with animations (SMIL or CSS)
- <canvas> element with JS (2D context, particles, generative art)
- CSS @keyframes animations, transitions, transforms
- CSS gradients, backdrop-filter, mix-blend-mode
- Any self-contained HTML/CSS/JS — no external dependencies

QUALITY:
- Premium, polished, modern dashboard aesthetic
- Smooth animations (60fps), subtle glow effects, clean typography
- Information hierarchy: big numbers/visuals first, details on click (<details>)
- All text must fit and wrap — use overflow:hidden, text-overflow:ellipsis
- Transparent backgrounds (the card provides the dark bg)

OUTPUT FORMAT — respond with ONLY a JSON object, no markdown, no explanation:
{"html": "...", "css": "...", "js": "...", "title": "...", "col_span": 1, "row_span": 1}

- html: the widget interior HTML
- css: CSS scoped to your widget (will be auto-prefixed with a data-attribute selector)
- js: a BARE function body that receives (root, host) — runs via new Function('root','host',js)
  CRITICAL: Do NOT wrap in (function(root,host){...}) or any IIFE. Just write the bare code.
  WRONG: (function(root,host){ const c = ... })
  RIGHT: const c = document.createElement('canvas'); root.appendChild(c); ...
- title: short widget title for the header bar
- col_span/row_span: grid size (1-3 cols, 1-2 rows)
"""


@app.post("/api/canvas/{project}/design")
async def design_widget(project: str, body: dict[str, Any]) -> dict[str, Any]:
    """Spawn a design subagent to create a widget from intent + data.

    Body: { widget_id, intent, data }
    The subagent generates HTML/CSS/JS and posts the widget.
    """
    widget_id = body.get("widget_id", f"designed-{int(time.time())}")
    intent = body.get("intent", "")
    data = body.get("data", {})

    prompt = (
        f"Create a dashboard widget.\n\n"
        f"INTENT: {intent}\n\n"
        f"DATA: {json.dumps(data, indent=2)}\n\n"
        f"Produce the widget as a JSON object. Be creative and visually impressive "
        f"while keeping the data clear and readable. Use animations, SVG, or canvas "
        f"if the intent calls for it. Match the design system exactly."
    )

    # Use a simple --print call to get the design output
    import subprocess
    from .broker.agent_session import CLAUDE_BIN, _get_spawn_env

    cmd = [
        CLAUDE_BIN, "--print",
        "--model", "claude-haiku-4-5-20251001",
        "--system-prompt", _DESIGN_SYSTEM_PROMPT,
        "--output-format", "text",
        "--max-turns", "1",
        "--", prompt,
    ]

    loop = asyncio.get_event_loop()

    def _run_design():
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            env=_get_spawn_env(),
        )
        return result.stdout.strip()

    try:
        raw = await loop.run_in_executor(None, _run_design)

        # Parse JSON from output (may have markdown fences)
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        widget_def = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Design agent failed: {exc}")

    # Strip IIFE wrappers — widget JS runs via new Function('root','host',js)
    # so it's already in a function body. IIFEs shadow the root/host params.
    js_code = widget_def.get("js", "")
    if js_code:
        stripped = js_code.strip()
        # Match: (function(root, host) { ... }) or (function(root,host){...})(root,host);
        import re as _re
        iife_match = _re.match(
            r'^\(function\s*\(\s*root\s*,\s*host\s*\)\s*\{(.*)\}\s*\)\s*(?:\(\s*root\s*,\s*host\s*\)\s*)?;?\s*$',
            stripped,
            _re.DOTALL,
        )
        if iife_match:
            js_code = iife_match.group(1).strip()

    # Create the widget
    w = WidgetCreate(
        id=widget_id,
        title=widget_def.get("title", "Designed Widget"),
        html=widget_def.get("html", ""),
        css=widget_def.get("css", ""),
        js=js_code,
        col_span=widget_def.get("col_span", 1),
        row_span=widget_def.get("row_span", 1),
    )
    widget = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, widget_id, w,
    )
    await ws_manager.broadcast(
        WSMessageType.CANVAS_WIDGET_CREATED,
        {"widget": widget.model_dump(mode="json")},
    )
    return {"ok": True, "widget_id": widget_id}


@app.delete("/api/canvas/{project}")
async def clear_canvas(project: str) -> dict[str, bool]:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, canvas_service.clear, project)
    await ws_manager.broadcast(WSMessageType.CANVAS_CLEARED, {"project": project})
    return {"ok": True}


@app.put("/api/canvas/{project}/layout")
async def save_layout(project: str, body: list[dict[str, Any]]) -> dict[str, bool]:
    """Save GridStack layout positions for all widgets in a project."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, canvas_service.save_layout, project, body)
    return {"ok": True}


# ── Widget Templates ──────────────────────────────────────────────────────────

_TEMPLATES_DIR = Path.home() / ".claude" / "canvas" / "templates"


@app.get("/api/canvas/templates")
async def list_widget_templates() -> list[dict[str, Any]]:
    """List all saved widget templates."""
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates = []
    for f in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text("utf-8"))
            templates.append({
                "filename": f.name,
                "id": data.get("id", f.stem),
                "title": data.get("title", f.stem),
                "gs_w": data.get("gs_w"),
                "gs_h": data.get("gs_h"),
            })
        except Exception:
            pass
    return templates


@app.post("/api/canvas/templates")
async def save_widget_template(body: dict[str, Any]) -> dict[str, Any]:
    """Save a widget as a reusable template. Body is the full widget JSON."""
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    widget_id = body.get("id", body.get("widget_id", "untitled"))
    source_project = body.pop("_source_project", body.pop("project", None))
    # Replace project-specific references with {{PROJECT}} for reusability
    for field in ("js", "html", "css"):
        if field in body and body[field] and source_project:
            body[field] = body[field].replace(source_project, "{{PROJECT}}")
    filename = f"{widget_id}.json"
    path = _TEMPLATES_DIR / filename
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return {"ok": True, "filename": filename, "path": str(path)}


@app.post("/api/canvas/{project}/widgets/{widget_id}/paste-template")
async def paste_template_to_widget(
    project: str, widget_id: str, body: dict[str, Any]
) -> WidgetState:
    """Paste a template into a project, replacing {{PROJECT}} with the project name."""
    template_filename = body.get("template")
    if not template_filename:
        raise HTTPException(400, "template filename required")
    path = _TEMPLATES_DIR / template_filename
    if not path.exists():
        raise HTTPException(404, "Template not found")
    data = json.loads(path.read_text("utf-8"))
    # Replace placeholder
    for field in ("js", "html", "css"):
        if field in data and data[field]:
            data[field] = data[field].replace("{{PROJECT}}", project)
    data.pop("id", None)
    data.pop("project", None)
    data.pop("_source_project", None)
    data.pop("_collected_at", None)
    w = WidgetCreate(**{k: v for k, v in data.items() if k not in ("created_at", "updated_at")})
    loop = asyncio.get_event_loop()
    widget = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, widget_id, w,
    )
    await ws_manager.broadcast(
        WSMessageType.CANVAS_WIDGET_CREATED,
        {"widget": widget.model_dump(mode="json")},
    )
    return widget


@app.get("/api/canvas/{project}/contract")
async def get_dashboard_contract(project: str) -> dict[str, Any]:
    """Return the dashboard data contract — widget schemas for structured data requests."""
    loop = asyncio.get_event_loop()
    contract = await loop.run_in_executor(None, canvas_service.get_dashboard_contract, project)
    if not contract:
        raise HTTPException(status_code=404, detail="No widgets configured")
    return contract


@app.post("/api/canvas/{project}/controller")
async def setup_dashboard_controller(project: str, body: dict[str, Any]) -> dict[str, Any]:
    """Set the dashboard prompt and inject it into the controller as a persistent task.

    Body: { "prompt": "I want a dashboard showing..." }

    The controller will:
    1. Design/create widgets based on the prompt
    2. Remember the data contract for those widgets
    3. Include data requests when dispatching subagents
    4. Stay idle between dispatches, ready for prompt changes
    """
    prompt = body.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Dashboard prompt is required")

    broker: AgentBroker = app.state.broker
    loop = asyncio.get_event_loop()

    # Save dashboard_prompt to project config
    project_data = await loop.run_in_executor(None, projects_svc.get_project, project, [])
    if not project_data:
        raise HTTPException(status_code=404, detail="Project not found")

    config = project_data.config
    config.dashboard_prompt = prompt
    await loop.run_in_executor(None, projects_svc.update_project_config, project, config)

    # Inject dashboard setup into global controller
    controller = broker.get_global_controller()
    dashboard_instruction = (
        f'Project: "{project}"\n'
        f'DASHBOARD SETUP REQUEST:\n\n'
        f'The user wants this dashboard: "{prompt}"\n\n'
        f'Use your canvas tools to design and create widgets that match this vision. '
        f'Use canvas_design() for creative/custom widgets and canvas_put() for data-driven ones.\n\n'
        f'After creating the widgets, remember their IDs and data schemas. '
        f'When you dispatch subagents, include a data contract asking them to provide '
        f'structured data updates for these widgets. Use request_dashboard_data() '
        f'periodically to poll running agents for fresh data, then update widgets '
        f'with canvas_put().\n\n'
        f'This dashboard is your persistent responsibility — keep it updated as work progresses. '
        f'Stay idle between updates, ready for new dispatch requests or dashboard changes.'
    )
    if controller:
        await broker.inject_message(controller.session_id, dashboard_instruction)
        return {"status": "injected", "session_id": controller.session_id}

    # Global controller dead — respawn it
    controller_mcp = str(
        Path(__file__).resolve().parent / "mcp" / "controller_mcp_config.json"
    )
    session = await broker.spawn_global_controller(mcp_config_path=controller_mcp)
    await broker.inject_message(session.session_id, dashboard_instruction)
    return {"status": "spawned", "session_id": session.session_id}


# ─── Widget Catalog API ───────────────────────────────────────────────────────


@app.get("/api/widget-catalog")
async def list_widget_templates() -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, widget_catalog_svc.list_templates)


@app.get("/api/widget-catalog/{template_id}/render")
async def render_widget_template(template_id: str) -> dict[str, Any]:
    """Render a widget template with its preview_data."""
    loop = asyncio.get_event_loop()
    tmpl = await loop.run_in_executor(None, widget_catalog_svc.get_template, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    preview_data = tmpl.get("preview_data", {})
    result = await loop.run_in_executor(
        None, widget_catalog_svc.render_template, template_id, preview_data
    )
    if not result:
        raise HTTPException(status_code=500, detail="Render failed")
    html, css, js = result
    return {
        "html": html, "css": css, "js": js,
        "name": tmpl.get("name", ""),
        "col_span": tmpl.get("col_span", 1),
        "row_span": tmpl.get("row_span", 1),
    }


@app.post("/api/widget-catalog", status_code=201)
async def save_widget_template(body: dict[str, Any]) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, widget_catalog_svc.save_template, body)


_TEMPLATE_GEN_PROMPT = _DESIGN_SYSTEM_PROMPT + """

YOU ARE BUILDING A REUSABLE, PARAMETERIZED TEMPLATE — NOT A STATIC WIDGET.

## CRITICAL RULES — ZERO HARDCODED DATA

Every single piece of text, number, label, status, message, name, title, value,
and list item in the HTML MUST come from a {{placeholder}} or {{#each}} block.
There must be ABSOLUTELY ZERO hardcoded content strings in the HTML.

If it's a string the user would see → it MUST be a {{variable}}.
If it's a list of items → it MUST use {{#each items}}...{{/each}}.
If it's a number, count, or metric → it MUST be a {{variable}}.

WRONG: <span class="label">CPU Usage</span>
RIGHT: <span class="label">{{label}}</span>

WRONG: <div class="entry">2026-03-05 Connection established</div>
RIGHT: {{#each entries}}<div class="entry">{{time}} {{message}}</div>{{/each}}

## Template Syntax

- Simple values: {{fieldName}}
- Lists: {{#each arrayField}}...{{/each}}
- Inside each: {{property}} for item fields, {{@index}} for 0-based index
- The template engine does simple string replacement — no conditionals, no helpers.
- CSS and JS can reference variables too if needed.

## data_schema — THIS IS THE CONTRACT

The data_schema defines EVERY field the template expects. Agents use this schema
to know what data to push. Make it thorough:

```json
{
  "title": {"type": "string", "description": "Widget heading"},
  "entries": {
    "type": "array",
    "description": "Log entries to display",
    "items": {
      "time": {"type": "string", "description": "Timestamp"},
      "message": {"type": "string", "description": "Log message"}
    }
  }
}
```

## preview_data — Realistic sample values

Provide preview_data that demonstrates the template with realistic but generic
sample values. This is used for catalog previews. Example:

```json
{
  "title": "System Monitor",
  "entries": [
    {"time": "12:00:01", "message": "Service started"},
    {"time": "12:00:05", "message": "Health check passed"}
  ]
}
```

## OUTPUT FORMAT

Respond with ONLY a JSON object (no markdown fences, no explanation):
{
  "name": "Short Template Name",
  "description": "One-line description of what this template visualizes",
  "category": "metrics|chart|status|log|custom",
  "data_schema": {"field": {"type": "...", "description": "..."}, ...},
  "preview_data": {"field": "sample value", ...},
  "html": "... ONLY {{placeholders}} for all visible text ...",
  "css": "...",
  "js": "...",
  "col_span": 1,
  "row_span": 1
}

FINAL CHECK: Before responding, scan your HTML output. If ANY visible text string
is not wrapped in {{...}}, you have a bug. Fix it.
"""


def _extract_json(raw: str) -> dict:
    """Extract JSON from Claude output — handles code fences, preamble, etc."""
    import re as _re

    text = raw.strip()
    if not text:
        raise ValueError("Empty response from Claude")

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence_match = _re.search(r"```(?:json)?\s*\n?(.*?)```", text, _re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Find first { ... last } — greedy brace extraction
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from response: {text[:200]}...")


@app.post("/api/widget-catalog/generate")
async def generate_widget_template(body: dict[str, Any]) -> dict[str, Any]:
    """Generate a parameterized widget template from a natural language description.

    Body: { description: "...", preview_data?: {...} }
    Returns a full template with {{placeholder}} variables ready to save.
    """
    import subprocess
    from .broker.agent_session import CLAUDE_BIN, _get_spawn_env

    description = body.get("description", "")
    preview_data = body.get("preview_data", {})

    prompt = (
        f"Create a PARAMETERIZED widget template.\n\n"
        f"DESCRIPTION: {description}\n\n"
        + (f"SAMPLE DATA (use as preview_data basis): {json.dumps(preview_data, indent=2)}\n\n" if preview_data else "")
        + f"RULES:\n"
        f"1. EVERY visible text string in the HTML must be a {{{{placeholder}}}} variable\n"
        f"2. EVERY list/repeated element must use {{{{#each array}}}}...{{{{/each}}}}\n"
        f"3. ZERO hardcoded text — if a user would see it, it must be a variable\n"
        f"4. data_schema must describe EVERY field with type and description\n"
        f"5. preview_data must have realistic sample values for all schema fields\n"
        f"6. The template is rendered server-side by replacing {{{{key}}}} with data values\n\n"
        f"Respond with ONLY a JSON object — no markdown fences, no explanation."
    )

    cmd = [
        CLAUDE_BIN, "--print",
        "--model", "claude-opus-4-6",
        "--system-prompt", _TEMPLATE_GEN_PROMPT,
        "--output-format", "text",
        "--max-turns", "3",
        "--", prompt,
    ]

    loop = asyncio.get_event_loop()
    last_error = None

    for attempt in range(2):
        def _run():
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
                env=_get_spawn_env(),
            )
            if result.returncode != 0 and not result.stdout.strip():
                stderr_msg = result.stderr.strip()[:300] if result.stderr else "no output"
                raise RuntimeError(f"Claude exited {result.returncode}: {stderr_msg}")
            return result.stdout.strip()

        try:
            raw = await loop.run_in_executor(None, _run)
            template_def = _extract_json(raw)

            # Validate required fields
            if "html" not in template_def and "css" not in template_def:
                raise ValueError("Template missing html/css fields")

            # Merge preview_data from request if provided
            if preview_data and not template_def.get("preview_data"):
                template_def["preview_data"] = preview_data

            return template_def

        except Exception as exc:
            last_error = exc
            logger.warning("Template generation attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                continue  # retry once
            break

    raise HTTPException(status_code=500, detail=f"Template generation failed after 2 attempts: {last_error}")


@app.post("/api/widget-catalog/{template_id}/preview")
async def preview_widget_template(template_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Render a template with data and return the output html/css/js."""
    data = body.get("data", {})
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, widget_catalog_svc.render_template, template_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="Template not found")
    html, css, js = result
    return {"html": html, "css": css, "js": js}


# Parameterized routes AFTER literal ones to avoid FastAPI matching "generate" as {template_id}
@app.get("/api/widget-catalog/{template_id}")
async def get_widget_template(template_id: str) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    tmpl = await loop.run_in_executor(None, widget_catalog_svc.get_template, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tmpl


@app.delete("/api/widget-catalog/{template_id}")
async def delete_widget_template(template_id: str) -> dict[str, bool]:
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, widget_catalog_svc.delete_template, template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


# ─── Metrics API ──────────────────────────────────────────────────────────────


@app.get("/api/metrics/agents")
async def get_agent_metrics(since: str = "1h", resolution: str = "1m") -> list[dict]:
    """Time-series agent activity data."""
    return metrics_service.get_agent_activity(since, resolution)


@app.get("/api/metrics/costs")
async def get_cost_metrics(since: str = "1h", resolution: str = "1m") -> list[dict]:
    """Time-series cost accumulation data."""
    return metrics_service.get_cost_series(since, resolution)


@app.get("/api/metrics/tasks")
async def get_task_metrics(since: str = "1h", resolution: str = "1m") -> list[dict]:
    """Time-series task throughput data."""
    return metrics_service.get_task_throughput(since, resolution)


@app.get("/api/metrics/models")
async def get_model_metrics(since: str = "24h") -> list[dict]:
    """Model usage breakdown."""
    return metrics_service.get_model_usage(since)


@app.get("/api/metrics/projects")
async def get_project_metrics(since: str = "1h", resolution: str = "5m") -> dict[str, list[dict]]:
    """Per-project activity time-series."""
    return metrics_service.get_project_activity(since, resolution)


@app.get("/api/metrics/health")
async def get_system_health() -> dict:
    """Current system health stats."""
    return metrics_service.get_system_health(ws_connections=ws_manager.connection_count)


@app.get("/api/metrics/summary")
async def get_metrics_summary() -> dict:
    """Quick summary: total agents today, total cost, uptime, etc."""
    return metrics_service.get_summary(ws_connections=ws_manager.connection_count)


@app.get("/api/stats", response_model=GlobalStats)
async def get_stats() -> GlobalStats:
    return _compute_stats(app.state.broker)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    broker: AgentBroker = app.state.broker
    return {
        "status": "ok",
        "uptime": time.time() - _start_time,
        "agents": len(broker.get_all_sessions()),
        "ws_connections": ws_manager.connection_count,
    }


# ─── WebSocket ────────────────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    broker: AgentBroker = app.state.broker
    try:
        # Send initial state on connect
        active_map: dict[str, list[str]] = {}
        for s in broker.get_all_sessions():
            active_map.setdefault(s.project_name, []).append(s.session_id)
        project_list = projects_svc.list_projects(active_map)
        await ws_manager.send(
            websocket, WSMessageType.PROJECT_LIST, [p.model_dump() for p in project_list]
        )
        await ws_manager.send(
            websocket, WSMessageType.STATS_UPDATE, _compute_stats(broker).model_dump()
        )
        # Send current agent states as a sync batch (not agent_spawned)
        # so the frontend can distinguish "here's existing state" from "new agent just appeared"
        agents = [s.to_dict() for s in broker.get_all_sessions()]
        await ws_manager.send(websocket, WSMessageType.AGENT_STATE_SYNC, {"agents": agents})

        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws_manager.send(websocket, "pong", {})
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ─── Static Files ─────────────────────────────────────────────────────────────

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
_FRONTEND_DEV  = Path(__file__).parent.parent / "frontend"
_FRONTEND_DIR  = _FRONTEND_DIST if _FRONTEND_DIST.exists() else _FRONTEND_DEV

if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=4040,
        reload=True,
        log_level="info",
    )
