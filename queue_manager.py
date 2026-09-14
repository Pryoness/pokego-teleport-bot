"""Priority queue manager with DSP timer tracking, deduplication, and per-Pokemon limits."""

import time
import heapq
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass(order=True)
class TargetTask:
    """A queued teleport target, prioritized by priority then LOWEST expiry (least DSP time left first)."""
    priority: int  # 0=high, 1=low — sorted first
    expires_at: float  # Unix timestamp when DSP runs out
    created_at: float = field(compare=False)
    pokemon: str = field(compare=False, default="")
    url: str = field(compare=False, default="")
    message_id: int = field(compare=False, default=0)
    channel_id: int = field(compare=False, default=0)
    dsp_minutes: int = field(compare=False, default=30)
    coords: Optional[str] = field(compare=False, default=None)
    status: str = field(compare=False, default="queued")  # queued, processing, done, expired
    shiny: bool = field(compare=False, default=False)
    iv_info: Optional[str] = field(compare=False, default=None)
    all_urls: list = field(compare=False, default_factory=list)  # All coord URLs from the message
    _counter_key: int = field(compare=False, default=0)  # Internal: key used in _all_tasks when message_id is 0

    @property
    def remaining_minutes(self):
        remaining = (self.expires_at - time.time()) / 60
        return max(0, round(remaining))

    @property
    def remaining_seconds(self):
        remaining = self.expires_at - time.time()
        return max(0, int(remaining))

    @property
    def is_expired(self):
        return time.time() >= self.expires_at


class QueueManager:
    """Thread-safe priority queue for teleport targets."""

    def __init__(self):
        self._lock = threading.Lock()
        self._heap = []  # Min-heap by (priority, expires_at) → LOWER DSP (earliest expiry) first
        self._all_tasks = {}  # message_id -> TargetTask
        self._counter = 0  # Tie-breaker for heap

    def add(self, pokemon, url, dsp_minutes, message_id=0, channel_id=0, shiny=False, iv_info=None, priority=1, queue_limit=0, all_urls=None, coords=None):
        """Add a target to the queue. Returns True if added, False if duplicate or rejected.
        
        priority: 0=high (always first), 1=low (normal DSP order)
        queue_limit: max per-Pokemon (0 = no limit). When at limit, replace lowest DSP if new is higher.
        """
        with self._lock:
            # Deduplicate by message_id or URL
            if message_id and message_id in self._all_tasks:
                return False
            # Only dedup by URL if it's a real URL (not empty/placeholder)
            # Reveal Coords targets have url="" — dedup those by message_id only
            if url and url != "no_url":
                for task in self._all_tasks.values():
                    if task.url == url:
                        return False
            # Dedup by coordinates (different channels may share the same spawn)
            if coords:
                for task in self._all_tasks.values():
                    if task.coords and task.coords == coords:
                        return False

            # Per-Pokemon queue limit with replace-lowest-DSP logic
            if queue_limit > 0:
                same_pokemon = [(k, t) for k, t in self._all_tasks.items()
                                if t.pokemon == pokemon.lower() and t.status in ("queued", "processing")]
                if len(same_pokemon) >= queue_limit:
                    # Find the one with lowest DSP (earliest expiry)
                    same_pokemon.sort(key=lambda kt: kt[1].expires_at)
                    lowest_key, lowest_task = same_pokemon[0]
                    new_expires = time.time() + (dsp_minutes * 60)
                    if new_expires > lowest_task.expires_at:
                        # New one has more DSP time — replace the lowest
                        del self._all_tasks[lowest_key]
                        # Remove from heap (will be cleaned up lazily)
                    else:
                        # New one has less DSP time — reject
                        return False

            expires_at = time.time() + (dsp_minutes * 60)
            self._counter += 1
            counter_key = self._counter
            task = TargetTask(
                priority=priority,
                expires_at=expires_at,
                created_at=time.time(),
                pokemon=pokemon.lower(),
                url=url,
                message_id=message_id,
                channel_id=channel_id,
                dsp_minutes=dsp_minutes,
                shiny=shiny,
                iv_info=iv_info,
                all_urls=all_urls or [],
                _counter_key=counter_key,
                coords=coords,
            )
            # Use expires_at so LOWER DSP (earlier expiry) sorts first in the heap
            heapq.heappush(self._heap, (task.priority, task.expires_at, counter_key, task))
            self._all_tasks[message_id or counter_key] = task
            return True

    def get_next(self):
        """Get the highest priority task. High priority (0) first, then highest DSP. Returns None if empty."""
        with self._lock:
            while self._heap:
                priority, neg_expires, counter, task = heapq.heappop(self._heap)
                key = task.message_id or task._counter_key
                if key not in self._all_tasks:
                    continue
                if task.is_expired:
                    del self._all_tasks[key]
                    continue
                if task.status in ("done", "expired", "processing"):
                    del self._all_tasks[key]
                    continue
                task.status = "processing"
                return task
            return None

    def mark_done(self, task):
        """Mark a task as completed."""
        with self._lock:
            key = task.message_id or task._counter_key
            task.status = "done"
            if key in self._all_tasks:
                del self._all_tasks[key]

    def mark_expired(self, task):
        """Mark a task as expired."""
        with self._lock:
            key = task.message_id or task._counter_key
            task.status = "expired"
            if key in self._all_tasks:
                del self._all_tasks[key]

    def release(self, task):
        """Put a task back into the queue (e.g., after failed processing)."""
        with self._lock:
            key = task.message_id or task._counter_key
            task.status = "queued"
            self._counter += 1
            heapq.heappush(self._heap, (task.priority, task.expires_at, self._counter, task))
            self._all_tasks[key] = task

    def cleanup_expired(self):
        """Remove all expired tasks from the queue. Only reports truly expired tasks."""
        with self._lock:
            remaining = []
            removed = []
            while self._heap:
                priority, neg_expires, counter, task = heapq.heappop(self._heap)
                key = task.message_id or task._counter_key
                if task.is_expired:
                    # Genuinely expired — report it
                    if key in self._all_tasks:
                        del self._all_tasks[key]
                    removed.append(task)
                elif task.status in ("done", "expired"):
                    # Already processed — silently clean up stale heap entry
                    if key in self._all_tasks:
                        del self._all_tasks[key]
                    # Don't add to removed (no "Expired" message)
                else:
                    remaining.append((priority, neg_expires, counter, task))
            self._heap = remaining
            heapq.heapify(self._heap)
            return removed

    def get_queue(self):
        """Return all non-expired queued tasks sorted by priority then lowest DSP (earliest expiry first)."""
        with self._lock:
            # First clean up expired tasks
            keys_to_remove = []
            for key, task in list(self._all_tasks.items()):
                if task.status not in ("queued", "processing"):
                    keys_to_remove.append(key)
                elif task.is_expired:
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                if key in self._all_tasks:
                    del self._all_tasks[key]

            tasks = []
            for key, task in self._all_tasks.items():
                if task.status in ("queued", "processing"):
                    # Parse lat/lng from coords string
                    lat = None
                    lng = None
                    if task.coords:
                        try:
                            parts = task.coords.replace(",", " ").split()
                            lat = float(parts[0])
                            lng = float(parts[1])
                        except (ValueError, IndexError):
                            pass
                    # Parse IV percentage from iv_info string
                    iv_percent = 0
                    if task.iv_info:
                        import re
                        m = re.search(r'(\d+)%', task.iv_info)
                        if m:
                            iv_percent = int(m.group(1))
                    tasks.append({
                        "pokemon": task.pokemon,
                        "url": task.url,
                        "dsp_minutes": task.dsp_minutes,
                        "remaining_minutes": task.remaining_minutes,
                        "remaining_seconds": task.remaining_seconds,
                        "status": task.status,
                        "is_expired": task.is_expired,
                        "shiny": task.shiny,
                        "iv_info": task.iv_info,
                        "iv_percent": iv_percent,
                        "priority": task.priority,
                        "coords": task.coords,
                        "lat": lat,
                        "lng": lng,
                        "message_id": task.message_id,
                        "channel_id": task.channel_id,
                        "created_at": task.created_at,
                        "expires_at": task.expires_at,
                        "_counter_key": task._counter_key,
                    })
            # Sort: high priority (0) first, then LOWEST DSP (earliest expiry) first
            tasks.sort(key=lambda t: (t.get("priority", 1), t.get("remaining_seconds", 0)))
            return tasks

    def get_pokemon_count(self, pokemon_name):
        """Count how many of a specific Pokemon are in the queue."""
        name = pokemon_name.lower()
        with self._lock:
            return sum(1 for t in self._all_tasks.values()
                       if t.pokemon == name and t.status in ("queued", "processing") and not t.is_expired)

    def remove_pokemon(self, pokemon_name):
        """Remove all entries of a specific Pokemon from the queue. Returns count removed."""
        name = pokemon_name.lower()
        with self._lock:
            keys_to_remove = [k for k, t in self._all_tasks.items()
                              if t.pokemon == name and t.status in ("queued", "processing")]
            for key in keys_to_remove:
                del self._all_tasks[key]
            return len(keys_to_remove)

    def remove_by_message_id(self, message_id):
        """Remove a single queue item by message_id or counter_key. Returns True if removed."""
        with self._lock:
            for key, task in list(self._all_tasks.items()):
                if (str(task.message_id) == str(message_id) or str(task._counter_key) == str(message_id)) and task.status in ("queued", "processing"):
                    del self._all_tasks[key]
                    return True
            return False

    def clear(self):
        """Clear the entire queue."""
        with self._lock:
            self._heap = []
            self._all_tasks = {}
            self._counter = 0

    def size(self):
        with self._lock:
            return len(self._all_tasks)

    def peek_all(self):
        """Return all queued tasks as TargetTask objects without modifying them."""
        with self._lock:
            tasks = []
            for key, task in list(self._all_tasks.items()):
                if task.status in ("queued", "processing") and not task.is_expired:
                    tasks.append(task)
            # Sort by priority then earliest expiry (lowest DSP first)
            tasks.sort(key=lambda t: (t.priority, t.expires_at))
            return tasks

    def claim(self, task):
        """Claim a specific task by marking it as 'processing'. Returns True on success.

        This is the safe way to select a specific task from the queue without
        the get_next()/release() loop that can cause infinite loops.
        """
        with self._lock:
            key = task.message_id or task._counter_key
            if key in self._all_tasks:
                t = self._all_tasks[key]
                if t.status == "queued":
                    t.status = "processing"
                    return True
                # Already processing — return True if it's the same object
                return t is task
            return False


queue = QueueManager()
