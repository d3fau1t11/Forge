import logging
from typing import Dict, List
from fastapi import WebSocket

logger = logging.getLogger("forge.websocket")

class ConnectionManager:
    """Manages active WebSockets connections for real-time dashboard events."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict):
        failed_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as exc:
                # Best-effort delivery: a failed send means the socket is dead, so
                # log it and drop it from the pool instead of leaving a stale entry.
                logger.warning(
                    "WebSocket broadcast failed; dropping connection: %s", exc
                )
                failed_connections.append(connection)

        for connection in failed_connections:
            self.disconnect(connection)

ws_manager = ConnectionManager()
