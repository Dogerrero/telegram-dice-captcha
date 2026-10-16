"""In-memory join burst detector; callers queue joins instead of kicking them."""
import asyncio
import os
import time
from collections import defaultdict, deque

JOIN_WINDOW_SECONDS = int(os.getenv("JOIN_WINDOW_SECONDS", "60"))
MAX_JOINS_PER_CHAT_WINDOW = int(os.getenv("MAX_JOINS_PER_CHAT_WINDOW", "30"))

_join_events = defaultdict(deque)
_join_lock = asyncio.Lock()


async def allow_new_join(chat_id: int) -> bool:
    """Return False during bursts so CAPTCHA delivery can be delayed safely."""
    now = time.monotonic()
    async with _join_lock:
        events = _join_events[chat_id]
        cutoff = now - JOIN_WINDOW_SECONDS
        while events and events[0] < cutoff:
            events.popleft()

        allowed = len(events) < MAX_JOINS_PER_CHAT_WINDOW
        events.append(now)

        # Prevent unbounded growth for inactive chats.
        if len(_join_events) > 1000:
            stale = [
                key for key, values in _join_events.items()
                if not values or values[-1] < cutoff
            ]
            for key in stale:
                _join_events.pop(key, None)

        return allowed
