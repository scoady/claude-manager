"""
Async PostgreSQL persistence layer for Claude Agent Manager.

Falls back gracefully — if DATABASE_URL is not set (or asyncpg is unavailable),
all methods become no-ops and the app runs in memory-only mode.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    project_name  TEXT NOT NULL,
    project_path  TEXT NOT NULL,
    task          TEXT,
    model         TEXT,
    status        TEXT NOT NULL DEFAULT 'idle',
    phase         TEXT NOT NULL DEFAULT 'starting',
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at      TIMESTAMPTZ,
    turn_count    INT NOT NULL DEFAULT 0,
    input_tokens  INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    git_branch    TEXT,
    milestones    JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS messages (
    uuid         TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    role         TEXT NOT NULL,
    content      JSONB NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    parent_uuid  TEXT,
    cwd          TEXT
);

CREATE INDEX IF NOT EXISTS messages_session_idx ON messages(session_id);
CREATE INDEX IF NOT EXISTS sessions_project_idx ON sessions(project_name);
"""


class Database:
    """Thin asyncpg wrapper. Call init() before any other method."""

    def __init__(self) -> None:
        self._pool = None

    async def init(self, url: str) -> None:
        try:
            import asyncpg  # type: ignore
        except ImportError:
            log.warning("asyncpg not installed — DB persistence disabled")
            return
        try:
            self._pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
            async with self._pool.acquire() as conn:
                await conn.execute(TABLE_DDL)
            log.info("Database connected and tables ready")
        except Exception as exc:
            log.warning("DB init failed (%s) — running in memory-only mode", exc)
            self._pool = None

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── Sessions ──────────────────────────────────────────────────────────────

    async def save_session(
        self,
        session_id: str,
        project_name: str,
        project_path: str,
        task: str | None = None,
        model: str | None = None,
    ) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO sessions (session_id, project_name, project_path, task, model)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (session_id) DO NOTHING
                    """,
                    session_id, project_name, project_path, task, model,
                )
        except Exception as exc:
            log.debug("save_session error: %s", exc)

    async def update_session(self, session_id: str, **kwargs: Any) -> None:
        if not self._pool or not kwargs:
            return
        allowed = {"status", "phase", "turn_count", "input_tokens", "output_tokens",
                   "ended_at", "git_branch", "milestones"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        try:
            async with self._pool.acquire() as conn:
                # Build parameterised SET clause
                parts = [f"{k} = ${i+2}" for i, k in enumerate(updates)]
                vals = list(updates.values())
                # Serialise milestones if present
                if "milestones" in updates:
                    idx = list(updates.keys()).index("milestones")
                    vals[idx] = json.dumps(vals[idx])
                await conn.execute(
                    f"UPDATE sessions SET {', '.join(parts)} WHERE session_id = $1",
                    session_id, *vals,
                )
        except Exception as exc:
            log.debug("update_session error: %s", exc)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        if not self._pool:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM sessions WHERE session_id = $1", session_id
                )
                return dict(row) if row else None
        except Exception as exc:
            log.debug("get_session error: %s", exc)
            return None

    async def list_sessions(self, project_name: str | None = None) -> list[dict[str, Any]]:
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                if project_name:
                    rows = await conn.fetch(
                        "SELECT * FROM sessions WHERE project_name = $1 ORDER BY started_at DESC",
                        project_name,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM sessions ORDER BY started_at DESC"
                    )
                return [dict(r) for r in rows]
        except Exception as exc:
            log.debug("list_sessions error: %s", exc)
            return []

    # ── Messages ──────────────────────────────────────────────────────────────

    async def save_message(
        self,
        uuid: str,
        session_id: str,
        role: str,
        content: Any,
        timestamp: str | None = None,
        parent_uuid: str | None = None,
        cwd: str | None = None,
    ) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                content_json = json.dumps(content) if not isinstance(content, str) else content
                if timestamp:
                    await conn.execute(
                        """
                        INSERT INTO messages (uuid, session_id, role, content, timestamp, parent_uuid, cwd)
                        VALUES ($1, $2, $3, $4::jsonb, $5::timestamptz, $6, $7)
                        ON CONFLICT (uuid) DO NOTHING
                        """,
                        uuid, session_id, role, content_json, timestamp, parent_uuid, cwd,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO messages (uuid, session_id, role, content, parent_uuid, cwd)
                        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                        ON CONFLICT (uuid) DO NOTHING
                        """,
                        uuid, session_id, role, content_json, parent_uuid, cwd,
                    )
        except Exception as exc:
            log.debug("save_message error: %s", exc)

    async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM messages WHERE session_id = $1 ORDER BY timestamp ASC",
                    session_id,
                )
                result = []
                for r in rows:
                    d = dict(r)
                    if isinstance(d.get("content"), str):
                        try:
                            d["content"] = json.loads(d["content"])
                        except Exception:
                            pass
                    result.append(d)
                return result
        except Exception as exc:
            log.debug("get_session_messages error: %s", exc)
            return []
