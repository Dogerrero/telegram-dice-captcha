import asyncio
import time
from collections import defaultdict, deque

MAX_ACTIVE_CAPTCHAS_PER_CHAT = 50
JOIN_WINDOW_SECONDS = 60
MAX_JOINS_PER_CHAT_WINDOW = 30

_join_events = defaultdict(deque)
_join_lock = asyncio.Lock()


async def allow_new_join(chat_id: int) -> bool:
    now = time.monotonic()
    async with _join_lock:
        events = _join_events[chat_id]
        cutoff = now - JOIN_WINDOW_SECONDS
        while events and events[0] < cutoff:
            events.popleft()

        if len(events) >= MAX_JOINS_PER_CHAT_WINDOW:
            return False

        events.append(now)

        # Prevent unbounded growth for inactive chats.
        if len(_join_events) > 1000:
            stale = [
                key for key, values in _join_events.items()
                if not values or values[-1] < cutoff
            ]
            for key in stale:
                _join_events.pop(key, None)

        return True
