"""Real-time broadcasting system for synchronizing updates across clients."""

import json
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any, Dict, List, Optional, Set
from datetime import datetime


class RealtimeEvent:
    """Represents a real-time event to be broadcast to clients."""

    def __init__(
        self,
        event_type: str,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        scope: Optional[str] = None,
        exclude_user: Optional[int] = None
    ):
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
            'type': self.event_type,
            'data': self.data,
            'timestamp': self.timestamp,
            'id': self.id
        }
        if self.scope:
            payload['scope'] = self.scope

        return f"data: {json.dumps(payload)}\n\n"

    def matches_client(self, user_id: int, subscribed_scopes: Set[str]) -> bool:
        """Check if this event should be sent to a specific client."""
        # Exclude specific user if set
        if self.exclude_user and self.exclude_user == user_id:
            return False

        # If event is targeted to specific user, only send to that user
        if self.user_id is not None and self.user_id != user_id:
            return False

        # If event has a scope, check if client is subscribed to it
        if self.scope and self.scope not in subscribed_scopes:
            return False

        return True


class RealtimeBroadcaster:
    """
    Manages real-time event broadcasting to connected clients.

    This is a simple in-memory broadcaster suitable for single-server deployments.
    For multi-server deployments, consider using Redis pub/sub.
    """

    def __init__(self, max_queue_size: int = 100):
        """
        Initialize the broadcaster.

        Args:
            max_queue_size: Maximum number of events to keep in each client queue
        """
        self.max_queue_size = max_queue_size
        self._clients: Dict[int, Dict[str, Any]] = {}  # user_id -> client_info
        self._lock = Lock()
        self._global_events: deque = deque(maxlen=1000)  # Keep last 1000 events

    def register_client(
        self,
        user_id: int,
        subscribed_scopes: Optional[Set[str]] = None
    ) -> str:
        """
        Register a new client for receiving events.

        Args:
            user_id: ID of the user connecting
            subscribed_scopes: Set of scopes this client wants to receive

        Returns:
            Client ID for this connection
        """
        client_id = f"{user_id}_{int(time.time() * 1000)}"

        with self._lock:
            if user_id not in self._clients:
                self._clients[user_id] = {
                    'connections': {},
                    'last_event_id': 0
                }

            self._clients[user_id]['connections'][client_id] = {
                'queue': deque(maxlen=self.max_queue_size),
                'subscribed_scopes': subscribed_scopes or {'all'},
                'connected_at': time.time()
            }

        return client_id

    def unregister_client(self, user_id: int, client_id: str) -> None:
        """Remove a client from receiving events."""
        with self._lock:
            if user_id in self._clients:
                self._clients[user_id]['connections'].pop(client_id, None)
                if not self._clients[user_id]['connections']:
                    del self._clients[user_id]

    def broadcast(
        self,
        event_type: str,
        data: Dict[str, Any],
        user_id: Optional[int] = None,
        scope: Optional[str] = None,
        exclude_user: Optional[int] = None
    ) -> None:
        """
        Broadcast an event to all matching clients.

        Args:
            event_type: Type of event (e.g., 'task:created')
            data: Event data payload
            user_id: If set, only send to this specific user
            scope: Optional scope filter
            exclude_user: If set, exclude this user from receiving the event
        """
        event = RealtimeEvent(
            event_type=event_type,
            data=data,
            user_id=user_id,
            scope=scope,
            exclude_user=exclude_user
        )

        with self._lock:
            # Store in global events for late joiners
            self._global_events.append(event)

            # Distribute to all matching clients
            for uid, user_data in self._clients.items():
                for client_id, client_info in user_data['connections'].items():
                    if event.matches_client(uid, client_info['subscribed_scopes']):
                        client_info['queue'].append(event)

    def get_events(
        self,
        user_id: int,
        client_id: str,
        since_id: Optional[int] = None
    ) -> List[RealtimeEvent]:
        """
        Get pending events for a specific client.

        Args:
            user_id: User ID
            client_id: Client connection ID
            since_id: Only return events after this ID

        Returns:
            List of events for this client
        """
        with self._lock:
            if user_id not in self._clients:
                return []

            client_info = self._clients[user_id]['connections'].get(client_id)
            if not client_info:
                return []

            queue = client_info['queue']

            if since_id is None:
                events = list(queue)
                queue.clear()
                return events

            # Return only events newer than since_id
            events = [e for e in queue if e.id > since_id]

            # Clear processed events from queue
            if events:
                # Keep only events newer than the newest one we're returning
                newest_id = max(e.id for e in events)
                client_info['queue'] = deque(
                    (e for e in queue if e.id > newest_id),
                    maxlen=self.max_queue_size
                )

            return events

    def get_connected_users(self) -> List[int]:
        """Get list of currently connected user IDs."""
        with self._lock:
            return list(self._clients.keys())

    def get_client_count(self) -> int:
        """Get total number of connected clients."""
        with self._lock:
            return sum(
                len(user_data['connections'])
                for user_data in self._clients.values()
            )


# Global broadcaster instance
_broadcaster = RealtimeBroadcaster()


def get_broadcaster() -> RealtimeBroadcaster:
    """Get the global broadcaster instance."""
    return _broadcaster


# Convenience functions for common operations

def broadcast_task_created(task_data: Dict[str, Any], exclude_user: Optional[int] = None) -> None:
    """Broadcast that a new task was created."""
    _broadcaster.broadcast(
        event_type='task:created',
        data=task_data,
        scope='tasks',
        exclude_user=exclude_user
    )


def broadcast_task_updated(task_data: Dict[str, Any], exclude_user: Optional[int] = None) -> None:
    """Broadcast that a task was updated."""
    _broadcaster.broadcast(
        event_type='task:updated',
        data=task_data,
        scope='tasks',
        exclude_user=exclude_user
    )


def broadcast_task_deleted(task_id: int, exclude_user: Optional[int] = None) -> None:
    """Broadcast that a task was deleted."""
    _broadcaster.broadcast(
        event_type='task:deleted',
        data={'id': task_id},
        scope='tasks',
        exclude_user=exclude_user
    )


def broadcast_task_status_changed(
    task_id: int,
    old_status: str,
    new_status: str,
    task_data: Dict[str, Any],
    exclude_user: Optional[int] = None
) -> None:
    """Broadcast that a task's status changed."""
    _broadcaster.broadcast(
        event_type='task:status_changed',
        data={
            'id': task_id,
            'old_status': old_status,
            'new_status': new_status,
            'task': task_data
        },
        scope='tasks',
        exclude_user=exclude_user
    )


def broadcast_company_created(company_data: Dict[str, Any], exclude_user: Optional[int] = None) -> None:
    """Broadcast that a new company was created."""
    _broadcaster.broadcast(
        event_type='company:created',
        data=company_data,
        scope='companies',
        exclude_user=exclude_user
    )


def broadcast_company_updated(company_data: Dict[str, Any], exclude_user: Optional[int] = None) -> None:
    """Broadcast that a company was updated."""
    _broadcaster.broadcast(
        event_type='company:updated',
        data=company_data,
        scope='companies',
        exclude_user=exclude_user
    )


def broadcast_company_deleted(company_id: int, exclude_user: Optional[int] = None) -> None:
    """Broadcast that a company was deleted."""
    _broadcaster.broadcast(
        event_type='company:deleted',
        data={'id': company_id},
        scope='companies',
        exclude_user=exclude_user
    )


def broadcast_user_presence(user_id: int, status: str) -> None:
    """Broadcast user presence change (online/offline)."""
    _broadcaster.broadcast(
        event_type='user:presence',
        data={'user_id': user_id, 'status': status},
        scope='presence'
    )
