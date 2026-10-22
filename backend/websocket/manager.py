from typing import Dict, List, Optional, Set
from fastapi import WebSocket

class ConnectionManager:
    """Manages active WebSockets connections for real-time dashboard events."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket, subprotocol: Optional[str] = None):
        """Accept the connection (only after the caller has authenticated it) and
        register the socket so it can receive broadcasts."""
        await websocket.accept(subprotocol=subprotocol)
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

ws_manager = ConnectionManager()
