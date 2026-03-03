"""Claude Agent Manager — FastAPI backend server."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .models import (
    Agent,
    AgentStatus,
    BootstrapProjectRequest,
    DispatchRequest,
    GlobalStats,
    InjectRequest,
    ManagedProject,
    ProjectConfig,
    WSMessageType,
)
from .services import projects as projects_svc
from .services import spawner
from .services.agents import read_session_messages
from .services import settings as settings_svc
from .ws_manager import WSManager

# ─── State ───────────────────────────────────────────────────────────────────

ws_manager = WSManager()
_start_time = time.time()

# ─── Background tasks ─────────────────────────────────────────────────────────


async def _broadcast_project_list() -> None:
    """Broadcast current project list with active agent info."""
    active_map: dict[str, list[str]] = {}
    for agent in spawner.get_running_agents():
        if agent.session_id:
            active_map.setdefault(agent.project_name, []).append(agent.session_id)

    project_list = projects_svc.list_projects(active_map)
    await ws_manager.broadcast(
        WSMessageType.PROJECT_LIST,
        [p.model_dump() for p in project_list],
    )


async def _watch_agents_task() -> None:
    """Monitor spawned agents, clean up finished ones, broadcast updates."""
    while True:
        await asyncio.sleep(1.0)
        try:
            pruned = spawner.prune_finished()
            if pruned:
                await _broadcast_project_list()

            stats = _compute_stats()
            await ws_manager.broadcast(WSMessageType.STATS_UPDATE, stats.model_dump())
        except Exception as exc:
            print(f"[watch-agents] error: {exc}")


async def _project_refresh_task() -> None:
    """Periodically broadcast the project list."""
    while True:
        await asyncio.sleep(5.0)
        try:
            await _broadcast_project_list()
        except Exception as exc:
            print(f"[project-refresh] error: {exc}")


def _compute_stats() -> GlobalStats:
    agents = spawner.get_running_agents()
    working = sum(1 for a in agents if a.status == AgentStatus.WORKING)
    idle = sum(1 for a in agents if a.status == AgentStatus.IDLE)
    projects = projects_svc.list_projects()
    return GlobalStats(
        total_projects=len(projects),
        total_agents=len(agents),
        working_agents=working,
        idle_agents=idle,
        uptime_seconds=time.time() - _start_time,
    )


# ─── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(_watch_agents_task()),
        asyncio.create_task(_project_refresh_task()),
    ]
    yield
    for t in tasks:
        t.cancel()


# ─── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Claude Agent Manager",
    description="Agent orchestration dashboard for managed projects",
    version="2.0.0",
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
    """List all managed projects with active agent counts."""
    active_map: dict[str, list[str]] = {}
    for agent in spawner.get_running_agents():
        if agent.session_id:
            active_map.setdefault(agent.project_name, []).append(agent.session_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, projects_svc.list_projects, active_map)


@app.post("/api/projects", response_model=ManagedProject, status_code=201)
async def create_project(body: BootstrapProjectRequest) -> ManagedProject:
    """Bootstrap a new managed project."""
    try:
        loop = asyncio.get_event_loop()
        project = await loop.run_in_executor(
            None,
            projects_svc.bootstrap_project,
            body.name,
            body.goal,
            body.description,
        )
        await _broadcast_project_list()
        return project
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/projects/{name}", response_model=ManagedProject)
async def get_project(name: str) -> ManagedProject:
    active_sessions = [
        a.session_id
        for a in spawner.get_running_for_project(name)
        if a.session_id
    ]
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(
        None, projects_svc.get_project, name, active_sessions
    )
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
    """Spawn one or more agents for a project task."""
    loop = asyncio.get_event_loop()
    project = await loop.run_in_executor(None, projects_svc.get_project, name, [])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    parallelism = project.config.parallelism
    model = body.model or project.config.model

    pending_keys = []
    for _ in range(parallelism):
        key = await spawner.dispatch_agent(
            project_name=name,
            project_path=project.path,
            task=body.task,
            model=model,
            ws_manager=ws_manager,
        )
        pending_keys.append(key)

    return {"status": "dispatched", "pending_keys": pending_keys, "parallelism": parallelism}


# ─── Agents API ────────────────────────────────────────────────────────────────


@app.get("/api/agents", response_model=list[Agent])
async def list_agents() -> list[Agent]:
    """List all currently running agents."""
    agents = spawner.get_running_agents()
    return [
        Agent(
            session_id=a.session_id or f"pending-{a.proc.pid}",
            pid=a.proc.pid,
            project_name=a.project_name,
            project_path=a.project_path,
            status=a.status,
            task=a.task,
            last_chunk=a.last_chunk(),
            model=a.model,
            started_at=a.started_at,
            has_pending_injection=a.pending_injection is not None,
        )
        for a in agents
    ]


@app.get("/api/agents/{session_id}/messages")
async def get_agent_messages(session_id: str) -> list[Any]:
    """Get conversation history for an agent session from ~/.claude/projects/."""
    from .services.agents import PROJECTS_DIR
    session_file = _find_session_file(session_id, PROJECTS_DIR)
    if not session_file:
        return []
    messages = await read_session_messages(session_file)
    return [m.model_dump() for m in messages]


@app.post("/api/agents/{session_id}/inject")
async def inject_message(session_id: str, body: InjectRequest) -> dict[str, Any]:
    """Inject a message into a running or idle agent session."""
    ok = await spawner.inject_message(session_id, body.message, ws_manager)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "queued" if spawner.get_agent(session_id) and
            spawner.get_agent(session_id).status == AgentStatus.WORKING else "sent"}


@app.delete("/api/agents/{session_id}", status_code=204)
async def kill_agent(session_id: str) -> None:
    """Kill a running agent subprocess."""
    ok = await spawner.kill_agent(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    await _broadcast_project_list()


# ─── Settings API ─────────────────────────────────────────────────────────────


@app.get("/api/settings/global")
async def get_global_settings() -> dict[str, Any]:
    """Read ~/.claude/settings.json."""
    return settings_svc.get_global_settings()


@app.put("/api/settings/global")
async def put_global_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Overwrite ~/.claude/settings.json."""
    try:
        settings_svc.set_global_settings(body)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return body


@app.get("/api/settings/plugins")
async def get_plugins() -> list[dict[str, Any]]:
    """List installed plugins with their enabled state."""
    return settings_svc.get_plugins()


@app.post("/api/settings/plugins/{plugin_id:path}/enable")
async def enable_plugin(plugin_id: str) -> dict[str, Any]:
    settings_svc.set_plugin_enabled(plugin_id, True)
    return {"id": plugin_id, "enabled": True}


@app.post("/api/settings/plugins/{plugin_id:path}/disable")
async def disable_plugin(plugin_id: str) -> dict[str, Any]:
    settings_svc.set_plugin_enabled(plugin_id, False)
    return {"id": plugin_id, "enabled": False}


@app.get("/api/stats", response_model=GlobalStats)
async def get_stats() -> GlobalStats:
    return _compute_stats()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime": time.time() - _start_time,
        "agents": len(spawner.get_running_agents()),
        "ws_connections": ws_manager.connection_count,
    }


# ─── WebSocket ────────────────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        # Send initial state
        active_map: dict[str, list[str]] = {}
        for agent in spawner.get_running_agents():
            if agent.session_id:
                active_map.setdefault(agent.project_name, []).append(agent.session_id)

        project_list = projects_svc.list_projects(active_map)
        await ws_manager.send(
            websocket, WSMessageType.PROJECT_LIST, [p.model_dump() for p in project_list]
        )
        await ws_manager.send(websocket, WSMessageType.STATS_UPDATE, _compute_stats().model_dump())

        # Keep alive
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
_FRONTEND_DEV = Path(__file__).parent.parent / "frontend"
_FRONTEND_DIR = _FRONTEND_DIST if _FRONTEND_DIST.exists() else _FRONTEND_DEV

if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _find_session_file(session_id: str, projects_dir: Path) -> Path | None:
    """Locate a session JSONL file by session ID in ~/.claude/projects/."""
    if not projects_dir.exists():
        return None
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=4040,
        reload=True,
        log_level="info",
    )
