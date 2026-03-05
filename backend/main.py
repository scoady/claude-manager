"""Claude Agent Manager — FastAPI backend server."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
from .services import templates as templates_svc
from .services import widget_catalog as widget_catalog_svc
from .services.canvas import canvas_service
from .ws_manager import WSManager

# ─── Singletons ───────────────────────────────────────────────────────────────

ws_manager = WSManager()
_start_time = time.time()


# ─── Agent widget helpers ─────────────────────────────────────────────────────


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ─── Background tasks ─────────────────────────────────────────────────────────


async def _broadcast_project_list(broker: AgentBroker) -> None:
    active_map: dict[str, list[str]] = {}
    for s in broker.get_all_sessions():
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
    """Trigger direct task dispatch when tasks are added or completed."""
    broker: AgentBroker = app.state.broker
    try:
        await broker.check_task_queue(project_name)
    except Exception as exc:
        print(f"[task-queue] notify error: {exc}")


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
    sessions = broker.get_all_sessions()
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

    tasks = [
        asyncio.create_task(_project_refresh_task(broker)),
        asyncio.create_task(_stats_task(broker)),
        asyncio.create_task(_task_poll_task(broker)),
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

    project_name = project.name

    async def _spawn_controller():
        controller_task = (
            "You are the CONTROLLER agent for this project.\n\n"
            "Your job is to READ PROJECT.md and CREATE a task plan.\n\n"
            "Steps:\n"
            "1. Read PROJECT.md to understand the project goal\n"
            f"2. Use create_tasks(project=\"{project_name}\", tasks=[...]) to add tasks\n"
            "   - Each task should be a single actionable unit of work\n"
            "   - Order them logically (dependencies first)\n"
            "   - Be specific: 'Create hello.sh that prints Hello World' not 'Set up project'\n"
            "3. Stop when all tasks are created.\n\n"
            "IMPORTANT: You are a planner only. Do NOT write code, do NOT dispatch agents.\n"
            "Worker agents are spawned automatically for each task you create.\n"
            "Always use create_tasks() — NEVER edit TASKS.md directly."
        )
        await broker.create_session(
            project_name=project.name,
            project_path=project.path,
            initial_task=controller_task,
            model=project.config.model,
            is_controller=True,
            mcp_config_path=project.config.mcp_config,
        )

    asyncio.create_task(_spawn_controller())

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

    # Route through controller if available and idle
    controller = broker.get_controller_for_project(name)
    if controller and controller.phase == SessionPhase.IDLE:
        task_prompt = (
            f'New task dispatched: "{body.task}"\n\n'
            f'Use dispatch_custom(project="{name}", task="...") to spawn a worker agent for this. '
            f'Give the worker a clear, detailed prompt with all context it needs. '
            f'Do NOT implement anything yourself — you are the coordinator. '
            f'Monitor with get_agents() and report results when done.\n\n'
            f'After dispatching, UPDATE the dashboard using canvas_put(project="{name}", '
            f'widget_id="project-status", ...) to reflect the new task and its status.'
        )
        await broker.inject_message(controller.session_id, task_prompt)
        return {"status": "delegated", "session_ids": [controller.session_id]}

    # Fallback: controller busy or missing — spawn standalone agent with canvas MCP
    _CANVAS_MCP_CONFIG = str(
        Path(__file__).resolve().parent / "mcp" / "canvas_mcp_config.json"
    )
    model = body.model or project.config.model
    mcp_config = project.config.mcp_config or _CANVAS_MCP_CONFIG

    session_ids = []
    for _ in range(project.config.parallelism):
        session = await broker.create_session(
            project_name=name,
            project_path=project.path,
            initial_task=body.task,
            model=model,
            mcp_config_path=mcp_config,
        )
        session_ids.append(session.session_id)

    return {"status": "dispatched", "session_ids": session_ids}


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

    # Route through controller if available and idle
    controller = broker.get_controller_for_project(name)
    if controller and controller.phase == SessionPhase.IDLE:
        await broker.inject_message(controller.session_id, plan_prompt)
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

    # Route through controller if available and idle
    controller = broker.get_controller_for_project(name)
    if controller and controller.phase == SessionPhase.IDLE:
        await broker.inject_message(controller.session_id, task_prompt)
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

    controller = broker.get_controller_for_project(name)

    if controller:
        if controller.phase == SessionPhase.IDLE:
            # Controller exists but idle — inject status check
            await broker.inject_message(
                controller.session_id,
                "Give a brief project status update: Read TASKS.md. "
                "What tasks are complete, what's in progress, what's next? "
                "Be concise — 2-3 sentences max.",
            )
            return {"status": "refreshed", "session_id": controller.session_id}
        else:
            # Controller is already working
            return {"status": "active", "session_id": controller.session_id}
    else:
        # No controller — spawn one with orchestrator MCP tools
        session = await broker.create_session(
            project_name=name,
            project_path=project.path,
            initial_task=(
                "You are the project orchestrator for this project.\n\n"
                "You have MCP tools for managing agents and the dashboard:\n"
                f"- list_tasks(project=\"{name}\") — get all tasks from TASKS.md\n"
                f"- dispatch_agent(project=\"{name}\", task_index=N) — spawn a worker agent for a task\n"
                f"- get_agents(project=\"{name}\") — check status of all agents\n"
                f"- report_complete(project=\"{name}\", task_index=N, summary=\"...\") — mark a task done\n"
                f"- dispatch_custom(project=\"{name}\", task=\"...\") — spawn an ad-hoc agent\n"
                f"- canvas_put(project=\"{name}\", ...) — publish/update a dashboard widget\n\n"
                "Your workflow:\n"
                "1. Read PROJECT.md for context, then use list_tasks() to see current state\n"
                "2. Wait for instructions — when told to work, use dispatch_agent() to assign tasks to workers\n"
                "3. Monitor with get_agents(), then report_complete() when tasks finish\n"
                "\nDo NOT implement anything yourself. You coordinate by dispatching agents via MCP tools."
            ),
            model=project.config.model if project.config else None,
            is_controller=True,
            mcp_config_path=project.config.mcp_config if project.config else None,
        )
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

    # Inject first phase prompt into controller
    controller = broker.get_controller_for_project(name)
    if controller and controller.phase == SessionPhase.IDLE:
        await broker.inject_message(controller.session_id, prompt)
    else:
        # No idle controller — create one
        project = await loop.run_in_executor(None, projects_svc.get_project, name, [])
        if project:
            asyncio.create_task(broker.create_session(
                project_name=name,
                project_path=project.path,
                initial_task=prompt,
                model=project.config.model,
                is_controller=True,
                mcp_config_path=project.config.mcp_config,
            ))

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
                controller = broker.get_controller_for_project(name)
                if controller and controller.phase == SessionPhase.IDLE:
                    await broker.inject_message(controller.session_id, prompt)
            result = wf.model_dump()
        elif body.action == "skip_phase":
            wf, prompt = await loop.run_in_executor(
                None, workflows_svc.advance_phase, name
            )
            if prompt:
                controller = broker.get_controller_for_project(name)
                if controller and controller.phase == SessionPhase.IDLE:
                    await broker.inject_message(controller.session_id, prompt)
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
    return [s.to_dict() for s in broker.get_all_sessions()]


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


# ─── Canvas API ──────────────────────────────────────────────────────────────


@app.get("/api/canvas/{project}", response_model=list[WidgetState])
async def list_canvas_widgets(project: str) -> list[WidgetState]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, canvas_service.get_widgets, project)


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
    """Generate system dashboard widgets (sys-* prefix) from live project data.

    System widgets go to the reserved top strip. Always re-generates
    from current data so the dashboard reflects the latest state.
    """
    loop = asyncio.get_event_loop()
    proj = await loop.run_in_executor(None, projects_svc.get_project, project, [])
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    tasks = await loop.run_in_executor(None, tasks_svc.get_tasks, project)
    broker: AgentBroker = app.state.broker
    agents = [s.to_dict() for s in broker.get_sessions_for_project(project)]
    milestones = await loop.run_in_executor(None, milestones_svc.get_milestones, project)

    total = len(tasks)
    done = sum(1 for t in tasks if t.get("status") == "done")
    active = sum(1 for t in tasks if t.get("status") == "in_progress")
    pending = total - done - active
    pct = round((done / total) * 100) if total > 0 else 0
    agent_count = len(agents)
    working = sum(1 for a in agents if a.get("phase") not in ("idle", "cancelled", "error"))

    widgets_created = []

    # ── System Widget 1: Task Constellation (2-col span) ──
    # SVG constellation graph — each task is a node, edges connect sequential tasks
    # Colors: done=green, active=cyan glow, pending=dim
    task_nodes_svg = ""
    task_list_html = ""
    cols = 6
    for i, t in enumerate(tasks[:18]):
        st = t.get("status", "pending")
        cx = 30 + (i % cols) * 52
        cy = 24 + (i // cols) * 40
        if st == "done":
            fill, stroke, filt = "#4ade80", "#4ade80", ' filter="url(#glow-done)"'
            r = 5
        elif st == "in_progress":
            fill, stroke, filt = "#67e8f9", "#67e8f9", ' filter="url(#glow-active)"'
            r = 6
        else:
            fill, stroke, filt = "#1a2640", "#243352", ""
            r = 4
        glow_ring = f'<circle cx="{cx}" cy="{cy}" r="11" fill="none" stroke="{fill}" stroke-opacity="0.15"><animate attributeName="r" values="9;15;9" dur="3s" repeatCount="indefinite"/><animate attributeName="stroke-opacity" values="0.15;0.05;0.15" dur="3s" repeatCount="indefinite"/></circle>' if st == "in_progress" else ""
        task_nodes_svg += f'{glow_ring}<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1"{filt}/>'
        # Edge to next node
        if i < len(tasks) - 1 and i % cols < cols - 1:
            nx = cx + 52
            edge_color = "#243352" if st == "pending" else "#1a2640"
            task_nodes_svg += f'<line x1="{cx+r+2}" y1="{cy}" x2="{nx-r-2}" y2="{cy}" stroke="{edge_color}" stroke-width="1" stroke-dasharray="2,4" stroke-opacity="0.5"/>'

        text = _escape_html((t.get("text", "") or "")[:55])
        color = "#4ade80" if st == "done" else "#fbbf24" if st == "in_progress" else "#475569"
        badge = "done" if st == "done" else "active" if st == "in_progress" else "pending"
        badge_bg = "rgba(74,222,128,0.12)" if st == "done" else "rgba(251,191,36,0.12)" if st == "in_progress" else "rgba(71,85,105,0.1)"
        task_list_html += (
            f'<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-family:\'DM Sans\',system-ui,sans-serif">'
            f'<span style="width:5px;height:5px;border-radius:50%;background:{color};flex-shrink:0;box-shadow:0 0 6px {color}30"></span>'
            f'<span style="color:#94a3b8;font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{text}</span>'
            f'<span style="color:{color};font-size:9px;font-family:\'IBM Plex Mono\',monospace;padding:1px 6px;background:{badge_bg};border-radius:8px;flex-shrink:0">{badge}</span>'
            f'</div>'
        )

    svg_height = 24 + ((min(len(tasks), 18) - 1) // cols + 1) * 40 + 10
    constellation_html = f"""<div style="color:#e2e8f0">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
    <div style="display:flex;align-items:baseline;gap:2px">
      <span style="font-family:'Plus Jakarta Sans',system-ui;font-size:24px;font-weight:700;color:#67e8f9;letter-spacing:-0.02em">{pct}</span>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#475569">%</span>
    </div>
    <div style="flex:1;height:3px;background:#1a2640;border-radius:2px;overflow:hidden">
      <div style="height:100%;width:{pct}%;background:linear-gradient(90deg,#4ade80,#67e8f9);border-radius:2px;box-shadow:0 0 10px rgba(74,222,128,0.3)"></div>
    </div>
    <div style="display:flex;gap:8px;font-family:'IBM Plex Mono',monospace;font-size:10px">
      <span style="color:#4ade80">{done} done</span>
      <span style="color:#475569">&middot;</span>
      <span style="color:#fbbf24">{active} active</span>
      <span style="color:#475569">&middot;</span>
      <span style="color:#475569">{pending} pending</span>
    </div>
  </div>
  <svg viewBox="0 0 340 {svg_height}" style="width:100%;height:auto;max-height:70px" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <filter id="glow-active"><feGaussianBlur stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <filter id="glow-done"><feGaussianBlur stdDeviation="2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>
    {task_nodes_svg}
  </svg>
  <div style="margin-top:8px;max-height:120px;overflow-y:auto">{task_list_html}</div>
</div>"""

    w1 = WidgetCreate(
        id="sys-task-constellation",
        title=f"Tasks — {done}/{total} complete",
        html=constellation_html,
        col_span=2, row_span=1,
    )
    widget1 = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, "sys-task-constellation", w1,
    )
    widgets_created.append(widget1)

    # ── System Widget 2: Agent Network ──
    # Shows agents as nodes with connection lines to a central hub
    agent_nodes_svg = ""
    agent_list_html = ""
    cx_hub, cy_hub = 80, 50

    # Hub node with glow
    agent_nodes_svg += (
        f'<circle cx="{cx_hub}" cy="{cy_hub}" r="14" fill="#0e1525" stroke="#67e8f9" stroke-width="1" stroke-opacity="0.4"/>'
        f'<circle cx="{cx_hub}" cy="{cy_hub}" r="18" fill="none" stroke="#67e8f9" stroke-width="0.5" stroke-opacity="0.15" stroke-dasharray="3,3"><animateTransform attributeName="transform" type="rotate" from="0 {cx_hub} {cy_hub}" to="360 {cx_hub} {cy_hub}" dur="20s" repeatCount="indefinite"/></circle>'
        f'<text x="{cx_hub}" y="{cy_hub+3}" text-anchor="middle" fill="#67e8f9" font-size="7" font-family="Plus Jakarta Sans,system-ui" font-weight="600" letter-spacing="0.05em">HUB</text>'
    )

    if agents:
        angle_step = 360 / max(len(agents), 1)
        import math
        for i, a in enumerate(agents[:8]):
            angle = math.radians(i * angle_step - 90)
            ax = cx_hub + 42 * math.cos(angle)
            ay = cy_hub + 36 * math.sin(angle)
            phase = a.get("phase", "idle")
            is_ctrl = a.get("is_controller", False)
            node_color = "#fbbf24" if is_ctrl else "#a78bfa" if phase in ("thinking", "tool_use") else "#4ade80" if phase == "idle" else "#67e8f9"
            is_active = phase not in ("idle", "cancelled", "error")
            pulse = f'<circle cx="{ax:.0f}" cy="{ay:.0f}" r="10" fill="none" stroke="{node_color}" stroke-opacity="0.15"><animate attributeName="r" values="7;13;7" dur="2.5s" repeatCount="indefinite"/><animate attributeName="stroke-opacity" values="0.15;0.04;0.15" dur="2.5s" repeatCount="indefinite"/></circle>' if is_active else ""
            # Connection line — animated dash for active agents
            dash = 'stroke-dasharray="4,6"' if is_active else 'stroke-dasharray="2,5"'
            line_anim = f'<animate attributeName="stroke-dashoffset" values="0;-20" dur="2s" repeatCount="indefinite"/>' if is_active else ""
            agent_nodes_svg += f'<line x1="{cx_hub}" y1="{cy_hub}" x2="{ax:.0f}" y2="{ay:.0f}" stroke="{node_color}" stroke-width="1" stroke-opacity="0.25" {dash}>{line_anim}</line>'
            agent_nodes_svg += pulse
            filt = ' filter="url(#glow-active)"' if is_active else ""
            agent_nodes_svg += f'<circle cx="{ax:.0f}" cy="{ay:.0f}" r="5" fill="{node_color}"{filt}/>'

            role = "CTRL" if is_ctrl else "AGENT"
            sid = (a.get("session_id") or "")[:6]
            role_bg = "rgba(251,191,36,0.12)" if is_ctrl else "rgba(167,139,250,0.12)"
            agent_list_html += (
                f'<div style="display:flex;align-items:center;gap:6px;padding:3px 0;font-family:\'DM Sans\',system-ui,sans-serif">'
                f'<span style="width:5px;height:5px;border-radius:50%;background:{node_color};flex-shrink:0;box-shadow:0 0 6px {node_color}30"></span>'
                f'<span style="color:{node_color};font-size:9px;font-family:\'IBM Plex Mono\',monospace;padding:1px 5px;background:{role_bg};border-radius:6px;font-weight:500">{role}</span>'
                f'<span style="color:#475569;font-size:9px;font-family:\'IBM Plex Mono\',monospace;flex:1">{sid}</span>'
                f'<span style="color:{node_color};font-size:9px;font-family:\'IBM Plex Mono\',monospace">{phase}</span>'
                f'</div>'
            )
    else:
        agent_list_html = '<div style="color:#475569;font-size:11px;text-align:center;padding:8px 0;font-family:\'DM Sans\',system-ui,sans-serif">No active agents</div>'

    agent_html = f"""<div style="color:#e2e8f0">
  <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px">
    <span style="font-family:'Plus Jakarta Sans',system-ui;font-size:22px;font-weight:700;color:#a78bfa;letter-spacing:-0.02em">{agent_count}</span>
    <span style="font-family:'DM Sans',system-ui;font-size:11px;color:#94a3b8">agent{'s' if agent_count != 1 else ''}</span>
    {('<span style="font-family:IBM Plex Mono,monospace;font-size:9px;color:#fbbf24;padding:2px 8px;background:rgba(251,191,36,0.1);border-radius:8px;margin-left:auto">' + str(working) + ' active</span>') if working else ''}
  </div>
  <svg viewBox="0 0 160 100" style="width:100%;height:auto;max-height:75px" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <filter id="glow-active"><feGaussianBlur stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>
    {agent_nodes_svg}
  </svg>
  <div style="margin-top:6px">{agent_list_html}</div>
</div>"""

    w2 = WidgetCreate(
        id="sys-agent-network",
        title=f"Agents — {working} active" if working else "Agents",
        html=agent_html,
        col_span=1, row_span=1,
    )
    widget2 = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, "sys-agent-network", w2,
    )
    widgets_created.append(widget2)

    # Broadcast all system widgets
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
- js: code that receives (root, host) — runs once after HTML is inserted
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

    broker: AgentBroker = app.state.broker

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

    # Create the widget
    w = WidgetCreate(
        id=widget_id,
        title=widget_def.get("title", "Designed Widget"),
        html=widget_def.get("html", ""),
        css=widget_def.get("css", ""),
        js=widget_def.get("js", ""),
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


# ─── Widget Catalog API ───────────────────────────────────────────────────────


@app.get("/api/widget-catalog")
async def list_widget_templates() -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, widget_catalog_svc.list_templates)


@app.post("/api/widget-catalog", status_code=201)
async def save_widget_template(body: dict[str, Any]) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, widget_catalog_svc.save_template, body)


@app.post("/api/widget-catalog/generate")
async def generate_widget_template(body: dict[str, Any]) -> dict[str, Any]:
    """Generate a parameterized widget template from a natural language description.

    Body: { description: "...", preview_data?: {...} }
    Returns a full template with {{placeholder}} variables ready to save.
    """
    description = body.get("description", "")
    preview_data = body.get("preview_data", {})

    prompt = (
        f"Create a PARAMETERIZED widget template.\n\n"
        f"DESCRIPTION: {description}\n\n"
        f"SAMPLE DATA: {json.dumps(preview_data, indent=2)}\n\n"
        f"Create a reusable template where data values use {{{{key}}}} placeholders.\n"
        f"For lists, use {{{{#each items}}}}...{{{{/each}}}} with {{{{property}}}} inside.\n"
        f"Also use {{{{@index}}}} for the iteration index.\n\n"
        f"Example placeholders: {{{{value}}}}, {{{{label}}}}, {{{{title}}}}\n"
        f"Example each: {{{{#each entries}}}}<div>{{{{name}}}}: {{{{score}}}}</div>{{{{/each}}}}\n\n"
        f"IMPORTANT: The template must work when placeholders are replaced with real data.\n"
        f"Include realistic preview data that demonstrates the widget's capability."
    )

    import subprocess
    from .broker.agent_session import CLAUDE_BIN, _get_spawn_env

    _TEMPLATE_GEN_PROMPT = _DESIGN_SYSTEM_PROMPT + """

ADDITIONAL FOR TEMPLATES:
- Use {{placeholder}} syntax for all dynamic data values
- For repeating lists, use {{#each listName}}...{{/each}} blocks
- Inside each blocks, use {{property}} for item fields and {{@index}} for index
- The template will be rendered server-side before sending to the browser
- Include a data_schema describing expected fields and their types
- Include preview_data with realistic sample values

OUTPUT FORMAT — respond with ONLY a JSON object:
{
  "name": "Short Template Name",
  "description": "What this template visualizes",
  "category": "metrics|chart|status|log|custom",
  "data_schema": {"field": {"type": "string|number|array", "description": "..."}},
  "preview_data": {"field": "sample value"},
  "html": "... {{placeholders}} ...",
  "css": "...",
  "js": "...",
  "col_span": 1,
  "row_span": 1
}
"""

    cmd = [
        CLAUDE_BIN, "--print",
        "--model", "claude-sonnet-4-6",
        "--system-prompt", _TEMPLATE_GEN_PROMPT,
        "--output-format", "text",
        "--max-turns", "1",
        "--", prompt,
    ]

    loop = asyncio.get_event_loop()

    def _run():
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            env=_get_spawn_env(),
        )
        return result.stdout.strip()

    try:
        raw = await loop.run_in_executor(None, _run)

        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        template_def = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Template generation failed: {exc}")

    # Merge preview_data from request if provided
    if preview_data and not template_def.get("preview_data"):
        template_def["preview_data"] = preview_data

    return template_def


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
