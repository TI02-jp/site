from __future__ import annotations

"""Simple server-sent events helper for meeting notifications."""

import json
from queue import Queue
from typing import Dict, Any, Iterable

from flask import Response, stream_with_context

_subscribers: set[Queue] = set()


def publish(event: Dict[str, Any]) -> None:
    """Publish *event* to all active subscribers."""
    for q in list(_subscribers):
        q.put(event)


def _consume() -> Iterable[str]:
    q: Queue = Queue()
    _subscribers.add(q)
    try:
        while True:
            event = q.get()
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        _subscribers.discard(q)


def sse_response() -> Response:
    """Return a streaming response for server-sent events."""
    return Response(stream_with_context(_consume()), mimetype="text/event-stream")
