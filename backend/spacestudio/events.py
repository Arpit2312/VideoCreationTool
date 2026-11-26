"""In-process pub/sub broadcasting to SSE subscribers.

Event types: video.updated, stage.updated, log, queue.updated, health.updated.
``publish`` may be called from any thread; delivery happens on the loop the subscriber lives on.
"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

EVENT_TYPES = {"video.updated", "stage.updated", "log", "queue.updated", "health.updated"}


@dataclass
class Subscriber:
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop
    video_id: Optional[str] = None
    dropped: int = 0


@dataclass
class EventBus:
    max_queue: int = 1000
    _subs: list[Subscriber] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def subscribe(self, video_id: Optional[str] = None) -> Subscriber:
        sub = Subscriber(asyncio.Queue(maxsize=self.max_queue), asyncio.get_running_loop(), video_id)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, type_: str, data: dict[str, Any]) -> None:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_}")
        msg = {"type": type_, "data": data}
        vid = data.get("video_id")
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            if sub.video_id and vid and sub.video_id != vid:
                continue
            try:
                sub.loop.call_soon_threadsafe(self._put, sub, msg)
            except RuntimeError:  # loop closed
                self.unsubscribe(sub)

    @staticmethod
    def _put(sub: Subscriber, msg: dict) -> None:
        try:
            sub.queue.put_nowait(msg)
        except asyncio.QueueFull:
            sub.dropped += 1


bus = EventBus()


def publish(type_: str, **data: Any) -> None:
    bus.publish(type_, data)


def sse_format(msg: dict) -> str:
    return f"event: {msg['type']}\ndata: {json.dumps(msg['data'], default=str)}\n\n"
