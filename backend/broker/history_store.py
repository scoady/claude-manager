"""
HistoryStore — in-memory conversation history with optional JSONL persistence.

Each session's history is a plain list of Anthropic messages dicts
(alternating user / assistant roles).  The store is the single source of
truth; no external JSONL files are read for managed sessions.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..services.database import Database


class HistoryStore:
    def __init__(self, persist_dir: Path | None = None, db: "Database | None" = None) -> None:
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._persist_dir = persist_dir
        self._db = db

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return a copy of the full messages list for this session."""
        return list(self._sessions.get(session_id, []))

    def message_count(self, session_id: str) -> int:
        return len(self._sessions.get(session_id, []))

    def get_all_session_ids(self) -> list[str]:
        return list(self._sessions.keys())

    # ── Write ─────────────────────────────────────────────────────────────────

    def append_user(self, session_id: str, message: dict[str, Any]) -> None:
        self._sessions.setdefault(session_id, []).append(message)
        self._persist(session_id, message)
        if self._db:
            asyncio.create_task(self._db.save_message(
                uuid=str(uuid.uuid4()),
                session_id=session_id,
                role="user",
                content=message.get("content", ""),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))

    def append_assistant(self, session_id: str, content_blocks: list[Any]) -> None:
        """Append assistant turn from raw SDK content block objects.

        Manually serialize only the fields the API accepts — model_dump() includes
        internal SDK fields (e.g. parsed_output) that cause 400 errors on replay.
        """
        block_dicts = []
        for b in content_blocks:
            btype = getattr(b, "type", None)
            if btype == "text":
                block_dicts.append({"type": "text", "text": b.text})
            elif btype == "tool_use":
                block_dicts.append({
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.input,
                })
            elif btype == "thinking":
                block_dicts.append({"type": "thinking", "thinking": b.thinking})
            elif isinstance(b, dict):
                block_dicts.append(b)
            else:
                # fallback for unknown block types
                block_dicts.append(b.model_dump() if hasattr(b, "model_dump") else b)
        msg = {"role": "assistant", "content": block_dicts}
        self._sessions.setdefault(session_id, []).append(msg)
        self._persist(session_id, msg)
        if self._db:
            asyncio.create_task(self._db.save_message(
                uuid=str(uuid.uuid4()),
                session_id=session_id,
                role="assistant",
                content=block_dicts,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))

    def append_tool_results(self, session_id: str, results: list[dict[str, Any]]) -> None:
        """Append tool_result blocks as a user turn."""
        msg = {"role": "user", "content": results}
        self._sessions.setdefault(session_id, []).append(msg)
        self._persist(session_id, msg)
        if self._db:
            asyncio.create_task(self._db.save_message(
                uuid=str(uuid.uuid4()),
                session_id=session_id,
                role="user",
                content=results,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))

    def drop_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist(self, session_id: str, message: dict[str, Any]) -> None:
        if not self._persist_dir:
            return
        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            path = self._persist_dir / f"{session_id}.jsonl"
            record = {
                "uuid": str(uuid.uuid4()),
                "sessionId": session_id,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass  # persistence is best-effort
