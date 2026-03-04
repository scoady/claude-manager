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
    DispatchRequest,
    GlobalStats,
    InjectRequest,
    ManagedProject,
    PlanTaskRequest,
    ProjectConfig,
    SessionPhase,
    SkillInfo,
    UpdateTaskRequest,
    WSMessageType,
)
from .broker import AgentBroker
from .rules import RulesEngine
from .services.database import Database
from .rules.builtin_rules import SessionHealthRule
from .services import projects as projects_svc
from .services import settings as settings_svc
from .services import skills as skills_svc
from .services import tasks as tasks_svc
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

    controller_task = (
        "You are the CONTROLLER agent for this project. "
        "Read PROJECT.md to understand the project goal. "
        "Then open TASKS.md and replace the placeholder with a concrete checklist of tasks. "
        "When done planning, report your plan and wait for instructions. "
        "IMPORTANT: You are a coordinator — you MUST NOT write code or implement anything yourself. "
        "When you receive a task, ALWAYS use the Agent tool (subagent_type='general-purpose') to delegate the work to a subagent. "
        "Your only job is to plan, delegate via Agent tool, review results, and update TASKS.md."
    )
    asyncio.create_task(broker.create_session(
        project_name=project.name,
        project_path=project.path,
        initial_task=controller_task,
        model=project.config.model,
        is_controller=True,
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
            f'IMMEDIATELY use the Agent tool (subagent_type="general-purpose") to delegate this work to a subagent. '
            f'Do NOT implement anything yourself — you are the coordinator. '
            f'Give the subagent a clear, detailed prompt with all context it needs from PROJECT.md. '
            f'After the subagent finishes, update TASKS.md if applicable and report the result.'
        )
        await broker.inject_message(controller.session_id, task_prompt)
        return {"status": "delegated", "session_ids": [controller.session_id]}

    # Fallback: controller busy or missing — spawn standalone agent
    model = body.model or project.config.model
    session_ids = []
    for _ in range(project.config.parallelism):
        session = await broker.create_session(
            project_name=name,
            project_path=project.path,
            initial_task=body.task,
            model=model,
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
        f'IMMEDIATELY use the Agent tool (subagent_type="general-purpose") to delegate this to a subagent. '
        f'Do NOT implement anything yourself — you are the coordinator. '
        f'Give the subagent a clear, detailed prompt including context from PROJECT.md and TASKS.md. '
        f'After the subagent finishes, update the checkbox in TASKS.md (change [~] to [x]) and report the result.'
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
    )

    return {"status": "started", "session_id": session.session_id, "task": task["text"]}


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
        # Send current agent states
        for s in broker.get_all_sessions():
            await ws_manager.send(websocket, WSMessageType.AGENT_SPAWNED, s.to_dict())

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
