"""WebSocket connection manager."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket


class WSManager:
    """Manages active WebSocket connections and broadcasts."""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def send(self, ws: WebSocket, msg_type: str, data: Any) -> None:
        payload = {
            "type": msg_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            self.disconnect(ws)

    async def broadcast(self, msg_type: str, data: Any) -> None:
        payload = {
            "type": msg_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        text = json.dumps(payload)
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self.active)
