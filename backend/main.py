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
from .services.canvas import canvas_service
from .ws_manager import WSManager

# ─── Singletons ───────────────────────────────────────────────────────────────

ws_manager = WSManager()
_start_time = time.time()

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
                    _task_cache[name] = tasks
                    await ws_manager.broadcast(WSMessageType.TASKS_UPDATED, {
                        "project_name": name,
                        "tasks": tasks,
                    })
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
    controller_task = (
        "You are the CONTROLLER agent for this project.\n\n"
        "You have MCP tools for managing agents and the dashboard:\n"
        f"- list_tasks(project=\"{project_name}\") — get all tasks from TASKS.md\n"
        f"- dispatch_agent(project=\"{project_name}\", task_index=N) — spawn a worker agent\n"
        f"- get_agents(project=\"{project_name}\") — check status of all agents\n"
        f"- report_complete(project=\"{project_name}\", task_index=N, summary=\"...\") — mark task done\n"
        f"- dispatch_custom(project=\"{project_name}\", task=\"...\") — spawn ad-hoc agent\n"
        f"- canvas_put(project=\"{project_name}\", ...) — publish a dashboard widget\n\n"
        "Your workflow:\n"
        "1. Read PROJECT.md to understand the project goal\n"
        "2. Open TASKS.md and replace the placeholder with a concrete checklist of tasks\n"
        "3. Publish a dashboard widget summarizing project status using canvas_put:\n"
        f'   canvas_put(project="{project_name}", widget_id="project-status",\n'
        '     title="Project Status", html="<html with task list, status badges, details>")\n'
        "   The widget MUST include: task names, status badges (done/active/planned),\n"
        "   and a <details> section with the full plan text.\n"
        "4. Stop and wait for instructions.\n\n"
        "IMPORTANT: You are a coordinator — NEVER write code or implement anything yourself.\n"
        "When told to start work, use dispatch_agent() to assign tasks to workers.\n"
        "Monitor with get_agents(). Use report_complete() when tasks finish.\n"
        "After each status change, UPDATE your dashboard widget with canvas_put().\n"
        "Do NOT ask questions or offer to proceed. Publish status to dashboard and stop."
    )
    asyncio.create_task(broker.create_session(
        project_name=project.name,
        project_path=project.path,
        initial_task=controller_task,
        model=project.config.model,
        is_controller=True,
        mcp_config_path=project.config.mcp_config,
    ))

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

    # Wrap the task with dashboard publishing instructions
    dashboard_task = (
        f'{body.task}\n\n'
        f'When you have results, publish a dashboard widget using canvas_put('
        f'project="{name}", widget_id="<descriptive-id>", title="<task title>", '
        f'html="<your content>"). The widget MUST include: a status badge, '
        f'a concise summary, and a <details> element with the full output/details.'
    )

    session_ids = []
    for _ in range(project.config.parallelism):
        session = await broker.create_session(
            project_name=name,
            project_path=project.path,
            initial_task=dashboard_task,
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

    # Fallback: spawn standalone agent
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
                "2. Publish a project status widget to the dashboard using canvas_put\n"
                "3. Wait for instructions — when told to work, use dispatch_agent() to assign tasks to workers\n"
                "4. Monitor with get_agents(), then report_complete() when tasks finish\n"
                "5. After each status change, UPDATE the dashboard widget with canvas_put()\n\n"
                "Dashboard widgets MUST include: task name, status badge, summary,\n"
                "and a <details> element with full text. Use widget_id='project-status'\n"
                "for the main status widget so it updates in-place.\n\n"
                "Do NOT implement anything yourself. You coordinate by dispatching agents via MCP tools."
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
    """Pre-generate dashboard widgets from project data (tasks, description, agents).

    Only seeds if no widgets exist yet for this project.
    """
    loop = asyncio.get_event_loop()
    existing = await loop.run_in_executor(None, canvas_service.get_widgets, project)
    if existing:
        return {"seeded": False, "reason": "widgets already exist", "count": len(existing)}

    # Gather project data
    proj = await loop.run_in_executor(None, projects_svc.get_project, project, [])
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    tasks = await loop.run_in_executor(None, tasks_svc.get_tasks, project)
    broker: AgentBroker = app.state.broker
    agents = [s.to_dict() for s in broker.get_sessions_for_project(project)]
    milestones = await loop.run_in_executor(None, milestones_svc.get_milestones, project)

    widgets_created = []

    # ── Widget 1: Task Progress ──
    total = len(tasks)
    done = sum(1 for t in tasks if t.get("status") == "done")
    active = sum(1 for t in tasks if t.get("status") == "in_progress")
    pct = round((done / total) * 100) if total > 0 else 0

    task_rows = ""
    for t in tasks[:20]:  # cap at 20 for display
        st = t.get("status", "pending")
        color = "#4ade80" if st == "done" else "#fbbf24" if st == "in_progress" else "#475569"
        label = "done" if st == "done" else "active" if st == "in_progress" else st
        text = (t.get("text", "") or "")[:80]
        task_rows += (
            f'<tr><td style="padding:4px 8px;border-bottom:1px solid rgba(255,255,255,0.04)">'
            f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{color};margin-right:8px"></span>'
            f'{text}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid rgba(255,255,255,0.04);color:{color};font-size:11px;text-align:right">{label}</td></tr>'
        )

    progress_html = f"""
<div style="font-family:'IBM Plex Mono',monospace;color:#e2e8f0">
  <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px">
    <div style="font-size:28px;font-weight:700;color:#67e8f9">{pct}<span style="font-size:14px;color:#475569">%</span></div>
    <div style="font-size:11px;color:#94a3b8">{done}/{total} tasks{'&ensp;&bull;&ensp;' + str(active) + ' active' if active else ''}</div>
  </div>
  <div style="height:4px;background:#1a2640;border-radius:2px;overflow:hidden;margin-bottom:16px">
    <div style="height:100%;width:{pct}%;background:linear-gradient(90deg,#4ade80,#67e8f9);border-radius:2px;box-shadow:0 0 8px rgba(74,222,128,0.3);transition:width 0.5s ease"></div>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:12px">
    <thead><tr>
      <th style="text-align:left;padding:4px 8px;color:#475569;font-weight:500;border-bottom:1px solid #243352">Task</th>
      <th style="text-align:right;padding:4px 8px;color:#475569;font-weight:500;border-bottom:1px solid #243352">Status</th>
    </tr></thead>
    <tbody>{task_rows}</tbody>
  </table>
</div>"""

    w1 = WidgetCreate(
        id="task-progress",
        title=f"Task Progress — {done}/{total}",
        html=progress_html,
        grid_col=1, grid_row=1, col_span=2, row_span=1,
    )
    widget1 = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, "task-progress", w1
    )
    widgets_created.append(widget1)

    # ── Widget 2: Agent Activity ──
    agent_count = len(agents)
    working = sum(1 for a in agents if a.get("phase") not in ("idle", "cancelled", "error"))
    agent_items = ""
    for a in agents[:8]:
        phase = a.get("phase", "idle")
        ph_color = "#4ade80" if phase == "idle" else "#fbbf24" if phase in ("thinking", "tool_use") else "#67e8f9"
        role = "Controller" if a.get("is_controller") else "Worker"
        sid = (a.get("session_id") or "")[:8]
        agent_items += (
            f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04)">'
            f'<span style="width:6px;height:6px;border-radius:50%;background:{ph_color};flex-shrink:0"></span>'
            f'<span style="color:#94a3b8;font-size:11px;flex:1">{role}</span>'
            f'<span style="color:#475569;font-size:10px">{sid}</span>'
            f'<span style="color:{ph_color};font-size:10px">{phase}</span>'
            f'</div>'
        )
    if not agent_items:
        agent_items = '<div style="color:#475569;font-size:12px;padding:12px 0;text-align:center">No agents running</div>'

    agent_html = f"""
<div style="font-family:'IBM Plex Mono',monospace;color:#e2e8f0">
  <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:12px">
    <span style="font-size:22px;font-weight:700;color:#a78bfa">{agent_count}</span>
    <span style="font-size:11px;color:#94a3b8">agent{'s' if agent_count != 1 else ''}{' &bull; ' + str(working) + ' working' if working else ''}</span>
  </div>
  {agent_items}
</div>"""

    w2 = WidgetCreate(
        id="agent-activity",
        title="Agent Activity",
        html=agent_html,
        grid_col=3, grid_row=1, col_span=1, row_span=1,
    )
    widget2 = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, "agent-activity", w2
    )
    widgets_created.append(widget2)

    # ── Widget 3: Project Info ──
    desc = proj.description or "No description"
    desc_escaped = desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    milestone_count = len(milestones)
    latest_ms = ""
    if milestones:
        ms = milestones[-1]
        ms_text = (ms.get("summary") or ms.get("task") or "")[:100]
        latest_ms = f'<div style="margin-top:8px;padding:8px;background:rgba(74,222,128,0.06);border:1px solid rgba(74,222,128,0.15);border-radius:6px;font-size:11px;color:#94a3b8;word-wrap:break-word;overflow-wrap:break-word"><span style="color:#4ade80;font-weight:600">Latest:</span> {ms_text}</div>'

    info_html = f"""
<div style="font-family:'IBM Plex Mono',monospace;color:#e2e8f0">
  <div style="font-size:12px;color:#94a3b8;line-height:1.5;margin-bottom:12px;word-wrap:break-word;overflow-wrap:break-word">{desc_escaped}</div>
  <div style="display:flex;gap:16px;font-size:11px;color:#475569">
    <span><span style="color:#67e8f9;font-weight:600">{total}</span> tasks</span>
    <span><span style="color:#a78bfa;font-weight:600">{agent_count}</span> agents</span>
    <span><span style="color:#4ade80;font-weight:600">{milestone_count}</span> milestones</span>
  </div>
  {latest_ms}
</div>"""

    w3 = WidgetCreate(
        id="project-info",
        title=proj.name,
        html=info_html,
        grid_col=1, grid_row=2, col_span=3, row_span=1,
    )
    widget3 = await loop.run_in_executor(
        None, canvas_service.upsert_widget, project, "project-info", w3
    )
    widgets_created.append(widget3)

    # Broadcast all created widgets
    for w in widgets_created:
        await ws_manager.broadcast(
            WSMessageType.CANVAS_WIDGET_CREATED,
            {"widget": w.model_dump(mode="json")},
        )

    return {"seeded": True, "count": len(widgets_created)}


@app.delete("/api/canvas/{project}")
async def clear_canvas(project: str) -> dict[str, bool]:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, canvas_service.clear, project)
    await ws_manager.broadcast(WSMessageType.CANVAS_CLEARED, {"project": project})
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
