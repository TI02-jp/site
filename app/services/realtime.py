"""Real-time broadcasting system for synchronizing updates across clients."""

import json
import time
from collections import deque
from threading import Event, Lock
from typing import Any, Deque, Dict, List, Optional, Set


class RealtimeEvent:
    """Represents a real-time event to be broadcast to clients."""

    def __init__(
        self,
        event_type: str,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        scope: Optional[str] = None,
        exclude_user: Optional[int] = None,
    ) -> None:
        """
        Create a new realtime event.

        Args:
            event_type: Type of event (e.g., 'task:created', 'task:status_changed')
            data: Event payload data
            user_id: Optional user ID to target specific user
            scope: Optional scope filter (e.g., 'tasks', 'companies')
            exclude_user: Optional user ID to exclude from receiving this event
        """
        self.event_type = event_type
        self.data = data
        self.user_id = user_id
        self.scope = scope
        self.exclude_user = exclude_user
        self.timestamp = time.time()
        self.id = int(self.timestamp * 1000)  # Millisecond timestamp as ID

    def to_sse(self) -> str:
        """Convert event to Server-Sent Events format."""
        payload = {
            "type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "id": self.id,
        }
        if self.scope:
            payload["scope"] = self.scope

        return f"data: {json.dumps(payload)}\n\n"

    def matches_client(self, user_id: int, subscribed_scopes: Set[str]) -> bool:
        """Check if this event should be sent to a specific client."""
        if self.exclude_user and self.exclude_user == user_id:
            return False

        if self.user_id is not None and self.user_id != user_id:
            return False

        if self.scope and self.scope not in subscribed_scopes:
            return False

        return True


class RealtimeBroadcaster:
    """
    Manages real-time event broadcasting to connected clients.

    This in-memory broadcaster is suitable for single-server deployments.
    For multi-server deployments, consider using Redis pub/sub.
    """

    def __init__(self, max_queue_size: int = 100, max_connections_per_user: int = 3):
        self.max_queue_size = max_queue_size
        self.max_connections_per_user = max_connections_per_user
        self._clients: Dict[int, Dict[str, Any]] = {}  # user_id -> client_info
        self._lock = Lock()
        self._global_events: Deque["RealtimeEvent"] = deque(maxlen=1000)

    def register_client(
        self,
        user_id: int,
        subscribed_scopes: Optional[Set[str]] = None,
    ) -> str:
        """Register a new client for receiving events.

        Limits concurrent connections per user to prevent worker exhaustion.
        """
        client_id = f"{user_id}_{int(time.time() * 1000)}"

        with self._lock:
            if user_id not in self._clients:
                self._clients[user_id] = {"connections": {}, "last_event_id": 0}

            connections = self._clients[user_id]["connections"]

            # Limit concurrent connections per user to prevent resource exhaustion
            if len(connections) >= self.max_connections_per_user:
                # Remove oldest connection
                oldest_client_id = min(
                    connections.keys(),
                    key=lambda cid: connections[cid]["connected_at"]
                )
                old_client_info = connections.pop(oldest_client_id, None)
                if old_client_info:
                    old_client_info["event"].set()  # Unblock waiter

            connections[client_id] = {
                "queue": deque(maxlen=self.max_queue_size),
                "subscribed_scopes": subscribed_scopes or {"all"},
                "connected_at": time.time(),
                "event": Event(),
            }

        return client_id

    def unregister_client(self, user_id: int, client_id: str) -> None:
        """Remove a client from receiving events."""
        with self._lock:
            user_data = self._clients.get(user_id)
            if not user_data:
                return

            connections = user_data["connections"]
            client_info = connections.pop(client_id, None)
            if client_info:
                client_info["event"].set()  # Unblock any waiters
            if not connections:
                del self._clients[user_id]

    def broadcast(
        self,
        event_type: str,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        scope: Optional[str] = None,
        exclude_user: Optional[int] = None,
    ) -> None:
        """Broadcast an event to all matching clients."""
        event = RealtimeEvent(
            event_type=event_type,
            data=data,
            user_id=user_id,
            scope=scope,
            exclude_user=exclude_user,
        )

        with self._lock:
            self._global_events.append(event)

            for uid, user_data in self._clients.items():
                for client_info in user_data["connections"].values():
                    if event.matches_client(uid, client_info["subscribed_scopes"]):
                        client_info["queue"].append(event)
                        client_info["event"].set()

    def wait_for_events(
        self,
        user_id: int,
        client_id: str,
        timeout: float = 30.0,
    ) -> bool:
        """Block until new events arrive or the timeout expires.

        Default timeout is 30s, coordinated with Apache SSE timeout (90s).
        Apache closes SSE connections after 90s, so we use shorter timeouts
        with client-side reconnection for better resource management.
        """
        with self._lock:
            user_data = self._clients.get(user_id)
            if not user_data:
                return False

            client_info = user_data["connections"].get(client_id)
            if not client_info:
                return False

            event = client_info["event"]

        return event.wait(timeout)

    def get_events(
        self,
        user_id: int,
        client_id: str,
        since_id: Optional[int] = None,
    ) -> List[RealtimeEvent]:
        """Get pending events for a specific client."""
        with self._lock:
            user_data = self._clients.get(user_id)
            if not user_data:
                return []

            client_info = user_data["connections"].get(client_id)
            if not client_info:
                return []

            queue = client_info["queue"]

            if since_id is None:
                events = list(queue)
                queue.clear()
                if not queue:
                    client_info["event"].clear()
                return events

            events = [event for event in queue if event.id > since_id]

            if events:
                newest_id = max(event.id for event in events)
                pending = deque(
                    (event for event in queue if event.id > newest_id),
                    maxlen=self.max_queue_size,
                )
                client_info["queue"] = pending
                if not pending:
                    client_info["event"].clear()
            else:
                # Nothing to deliver; reset the flag so waiters can pause.
                client_info["event"].clear()

            return events

    def get_connected_users(self) -> List[int]:
        """Get list of currently connected user IDs."""
        with self._lock:
            return list(self._clients.keys())

    def get_client_count(self) -> int:
        """Get total number of connected clients."""
        with self._lock:
            return sum(
                len(user_data["connections"]) for user_data in self._clients.values()
            )


_broadcaster = RealtimeBroadcaster()


def get_broadcaster() -> RealtimeBroadcaster:
    """Get the global broadcaster instance."""
    return _broadcaster


def broadcast_task_created(
    task_data: Dict[str, Any], exclude_user: Optional[int] = None
) -> None:
    """Broadcast that a new task was created."""
    _broadcaster.broadcast(
        event_type="task:created",
        data=task_data,
        scope="tasks",
        exclude_user=exclude_user,
    )


def broadcast_task_deleted(
    task_id: int, exclude_user: Optional[int] = None
) -> None:
    """Broadcast that a task was deleted."""
    _broadcaster.broadcast(
        event_type="task:deleted",
        data={"id": task_id},
        scope="tasks",
        exclude_user=exclude_user,
    )


def broadcast_task_status_changed(
    task_id: int,
    old_status: str,
    new_status: str,
    task_data: Dict[str, Any],
    exclude_user: Optional[int] = None,
) -> None:
    """Broadcast that a task's status changed."""
    _broadcaster.broadcast(
        event_type="task:status_changed",
        data={
            "id": task_id,
            "old_status": old_status,
            "new_status": new_status,
            "task": task_data,
        },
        scope="tasks",
        exclude_user=exclude_user,
    )


