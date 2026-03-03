"""Claude Agent Manager — FastAPI backend server."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .models import (
    Agent,
    AgentMessage,
    GlobalStats,
    SendMessageRequest,
    SendMessageResponse,
    WSMessageType,
)
from .services.agents import (
    PROJECTS_DIR,
    discover_agents,
    poll_for_updates,
    read_session_messages,
)
from .services.message_sender import send_message
from .services import settings as settings_svc
from .ws_manager import WSManager

# ─── State ───────────────────────────────────────────────────────────────────

ws_manager = WSManager()
_start_time = time.time()
_agent_cache: list[Agent] = []
_cache_lock = asyncio.Lock()

POLL_INTERVAL = 1.5   # seconds between file-change polls
REFRESH_INTERVAL = 5.0  # seconds between full agent rediscovery

# ─── Background tasks ─────────────────────────────────────────────────────────


async def _refresh_agents_task() -> None:
    """Periodically rediscover all agents and broadcast updates."""
    global _agent_cache
    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        try:
            agents = await discover_agents()
            async with _cache_lock:
                _agent_cache = agents
            await ws_manager.broadcast(
                WSMessageType.AGENT_LIST,
                [a.model_dump() for a in agents],
            )
            stats = _compute_stats(agents)
            await ws_manager.broadcast(
                WSMessageType.STATS_UPDATE,
                stats.model_dump(),
            )
        except Exception as exc:
            print(f"[refresh-agents] error: {exc}")


async def _poll_messages_task() -> None:
    """Poll session files for new messages and broadcast them."""
    known: dict[str, int] = {}
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            updates = await poll_for_updates(known)
            for session_id, new_msgs in updates:
                # Update known message counts
                known[session_id] = known.get(session_id, 0) + len(new_msgs)

                for msg in new_msgs:
                    await ws_manager.broadcast(
                        WSMessageType.NEW_MESSAGE,
                        {"session_id": session_id, "message": msg.model_dump()},
                    )
                    # Build activity event
                    activity_text = _summarize_message(msg)
                    if activity_text:
                        agent = _find_agent(session_id)
                        await ws_manager.broadcast(
                            WSMessageType.ACTIVITY_EVENT,
                            {
                                "session_id": session_id,
                                "project": agent.project_name if agent else session_id[:8],
                                "text": activity_text,
                                "role": msg.role,
                                "timestamp": msg.timestamp or datetime.now(timezone.utc).isoformat(),
                            },
                        )
        except Exception as exc:
            print(f"[poll-messages] error: {exc}")


def _summarize_message(msg: AgentMessage) -> str | None:
    """Generate a short activity line for a message."""
    for c in msg.content:
        if c.type == "text" and c.text:
            text = c.text.strip()
            return text[:72] + "…" if len(text) > 72 else text
        if c.type == "tool_use" and c.tool_call:
            return f"⚙ {c.tool_call.name}()"
    return None


def _find_agent(session_id: str) -> Agent | None:
    return next((a for a in _agent_cache if a.session_id == session_id), None)


def _compute_stats(agents: list[Agent]) -> GlobalStats:
    from .models import AgentStatus
    active = sum(1 for a in agents if a.status == AgentStatus.ACTIVE)
    working = sum(1 for a in agents if a.status == AgentStatus.WORKING)
    idle = sum(1 for a in agents if a.status == AgentStatus.IDLE)
    total_msgs = sum(a.message_count for a in agents)
    return GlobalStats(
        total_agents=len(agents),
        active_agents=active,
        working_agents=working,
        idle_agents=idle,
        total_messages=total_msgs,
        uptime_seconds=time.time() - _start_time,
    )


# ─── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent_cache
    # Initial discovery
    _agent_cache = await discover_agents()
    # Start background tasks
    tasks = [
        asyncio.create_task(_refresh_agents_task()),
        asyncio.create_task(_poll_messages_task()),
    ]
    yield
    for t in tasks:
        t.cancel()


# ─── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Claude Agent Manager",
    description="Real-time control panel for local Claude agents",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── REST API ─────────────────────────────────────────────────────────────────


@app.get("/api/agents", response_model=list[Agent])
async def list_agents() -> list[Agent]:
    """List all discovered Claude agents."""
    async with _cache_lock:
        return _agent_cache


@app.get("/api/agents/{session_id}", response_model=Agent)
async def get_agent(session_id: str) -> Agent:
    agent = _find_agent(session_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.get("/api/agents/{session_id}/messages", response_model=list[AgentMessage])
async def get_messages(session_id: str) -> list[AgentMessage]:
    """Get full conversation history for an agent session."""
    agent = _find_agent(session_id)
    project_path = agent.project_path if agent else None

    # Find the session file
    session_file = _find_session_file(session_id)
    if not session_file:
        raise HTTPException(status_code=404, detail="Session file not found")

    messages = await read_session_messages(session_file)
    return messages


@app.post("/api/agents/{session_id}/message", response_model=SendMessageResponse)
async def send_agent_message(
    session_id: str, request: SendMessageRequest
) -> SendMessageResponse:
    """Send a message to a Claude agent session."""
    agent = _find_agent(session_id)
    project_path = agent.project_path if agent else None

    # Update status to working
    if agent:
        agent.status = __import__("backend.models", fromlist=["AgentStatus"]).AgentStatus.WORKING
        await ws_manager.broadcast(WSMessageType.AGENT_UPDATE, agent.model_dump())

    success, stdout, stderr = await send_message(
        session_id=session_id,
        message=request.message,
        project_path=project_path,
    )

    return SendMessageResponse(
        session_id=session_id,
        success=success,
        response=stdout if success else None,
        error=stderr if not success else None,
    )


@app.get("/api/stats", response_model=GlobalStats)
async def get_stats() -> GlobalStats:
    async with _cache_lock:
        return _compute_stats(_agent_cache)


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


@app.get("/api/settings/projects")
async def get_all_project_settings() -> list[dict[str, Any]]:
    """List every known project with its .claude settings."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, settings_svc.list_project_settings)


@app.get("/api/settings/projects/{project_key}")
async def get_project_settings(project_key: str) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, settings_svc.get_project_settings, project_key)


@app.put("/api/settings/projects/{project_key}")
async def put_project_settings(project_key: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, settings_svc.set_project_settings, project_key, body)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return body


@app.post("/api/settings/projects/{project_key}/permissions/{kind}")
async def add_permission(
    project_key: str, kind: str, body: dict[str, str]
) -> dict[str, Any]:
    if kind not in ("allow", "deny"):
        raise HTTPException(status_code=400, detail="kind must be 'allow' or 'deny'")
    permission = body.get("permission", "").strip()
    if not permission:
        raise HTTPException(status_code=400, detail="permission is required")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, settings_svc.add_permission, project_key, permission, kind
    )


@app.delete("/api/settings/projects/{project_key}/permissions/{kind}/{permission:path}")
async def remove_permission(
    project_key: str, kind: str, permission: str
) -> dict[str, Any]:
    if kind not in ("allow", "deny"):
        raise HTTPException(status_code=400, detail="kind must be 'allow' or 'deny'")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, settings_svc.remove_permission, project_key, permission, kind
    )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime": time.time() - _start_time,
        "agents": len(_agent_cache),
        "ws_connections": ws_manager.connection_count,
    }


# ─── WebSocket ────────────────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        # Send initial state
        async with _cache_lock:
            agents = _agent_cache.copy()
        await ws_manager.send(websocket, WSMessageType.AGENT_LIST, [a.model_dump() for a in agents])
        await ws_manager.send(websocket, WSMessageType.STATS_UPDATE, _compute_stats(agents).model_dump())

        # Keep alive — client sends pings
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

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _find_session_file(session_id: str) -> Path | None:
    """Locate a session JSONL file by session ID across all project dirs."""
    if not PROJECTS_DIR.exists():
        return None
    for project_dir in PROJECTS_DIR.iterdir():
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
