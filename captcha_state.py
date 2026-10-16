"""In-memory CAPTCHA state machine.

No user or challenge data is written to disk. State only exists while this
process is running; the JSON settings file stores administrator text only.
"""

import time
from copy import deepcopy
from typing import Optional

# Keyed by (chat_id, user_id); removed as soon as the flow completes or the
# member leaves. This deliberately is not a persistent user registry.
_challenges: dict[tuple[int, int], dict] = {}

_ACTIVE_STATES = {
    "sending", "active", "processing", "dispatching", "timeout_processing"
}


def _key(chat_id: int, user_id: int) -> tuple[int, int]:
    return int(chat_id), int(user_id)


def _get_mutable(chat_id: int, user_id: int) -> Optional[dict]:
    return _challenges.get(_key(chat_id, user_id))


def _snapshot(row: Optional[dict]) -> Optional[dict]:
    return deepcopy(row) if row is not None else None



def initialize_state() -> None:
    """State starts empty on each process launch; no users are loaded from disk."""


def active_count(chat_id: int, exclude_user_id: Optional[int] = None) -> int:
    return sum(
        1 for (row_chat, row_user), row in _challenges.items()
        if row_chat == chat_id
        and row_user != exclude_user_id
        and row.get("state") in _ACTIVE_STATES
    )


def get(chat_id: int, user_id: int) -> Optional[dict]:
    return _snapshot(_get_mutable(chat_id, user_id))


def queue_challenge(
    chat_id: int,
    user_id: int,
    attempt: int = 1,
    delay_seconds: int = 0,
) -> bool:
    """Start a transient flow for a new join; ignore duplicate join events."""
    key = _key(chat_id, user_id)
    if key in _challenges:
        return False
    now = time.time()
    _challenges[key] = {
        "chat_id": int(chat_id),
        "user_id": int(user_id),
        "message_id": 0,
        "attempt": int(attempt),
        "state": "preparing",
        "created_at": now,
        "updated_at": now,
        "expires_at": 0.0,
        "next_action_at": now + max(0, delay_seconds),
        "expected_value": None,
        "challenge_token": None,
        "notice_sent": False,
        "kick_ban_issued": False,
    }
    return True


def start_sending(
    chat_id: int,
    user_id: int,
    attempt: int,
    expected_value: int,
    challenge_token: str,
) -> bool:
    row = _get_mutable(chat_id, user_id)
    if row is None or row.get("state") not in {
        "dispatching", "queued", "waiting"
    }:
        return False
    row.update(
        message_id=0,
        attempt=int(attempt),
        state="sending",
        expected_value=int(expected_value),
        challenge_token=challenge_token,
        expires_at=0.0,
        next_action_at=None,
        updated_at=time.time(),
    )
    return True


def activate_challenge(
    chat_id: int,
    user_id: int,
    message_id: int,
    attempt: int,
    timeout_seconds: int,
) -> bool:
    row = _get_mutable(chat_id, user_id)
    if row is None or row.get("state") != "sending":
        return False
    now = time.time()
    row.update(
        message_id=int(message_id),
        attempt=int(attempt),
        state="active",
        created_at=now,
        updated_at=now,
        expires_at=now + max(0, timeout_seconds),
        next_action_at=None,
    )
    return True


def claim_active(
    chat_id: int,
    user_id: int,
    message_id: int,
    challenge_token: str,
) -> Optional[dict]:
    row = _get_mutable(chat_id, user_id)
    now = time.time()
    if not row or not (
        row.get("state") == "active"
        and row.get("message_id") == message_id
        and row.get("challenge_token") == challenge_token
        and row.get("expires_at", 0) > now
    ):
        return None
    row.update(state="processing", updated_at=now)
    return _snapshot(row)


def resolve_active_answer(
    chat_id: int,
    user_id: int,
    message_id: int,
    challenge_token: str,
    selected_value: int,
    max_attempts: int,
    retry_delay_seconds: int,
) -> Optional[dict]:
    """Validate and consume an answer atomically within the event loop."""
    row = _get_mutable(chat_id, user_id)
    now = time.time()
    if not row or not (
        row.get("state") == "active"
        and row.get("message_id") == message_id
        and row.get("challenge_token") == challenge_token
        and row.get("expires_at", 0) > now
        and row.get("expected_value") is not None
    ):
        return None

    expected_value = row["expected_value"]
    attempt = row["attempt"]
    if int(selected_value) == int(expected_value):
        state = "passed_pending"
        next_action_at = now
    elif attempt >= max_attempts:
        state = "kick_pending"
        next_action_at = now
    else:
        state = "waiting"
        row["attempt"] = attempt + 1
        next_action_at = now + max(0, retry_delay_seconds)

    row.update(
        state=state,
        updated_at=now,
        next_action_at=next_action_at,
        expected_value=None,
        challenge_token=None,
        expires_at=0.0,
    )
    return _snapshot(row)


def mark_kick_ban_issued(chat_id: int, user_id: int) -> None:
    row = _get_mutable(chat_id, user_id)
    if row:
        row["kick_ban_issued"] = True
        row["updated_at"] = time.time()


def claim_due_waiting(allowed_chat_ids: Optional[set[int]] = None) -> list[dict]:
    now = time.time()
    allowed = None if allowed_chat_ids is None else set(allowed_chat_ids)
    due = []
    for key, row in sorted(
        list(_challenges.items()), key=lambda pair: pair[1].get("next_action_at") or 0
    ):
        if row.get("state") not in {"queued", "waiting"}:
            continue
        if row.get("next_action_at") is None or row["next_action_at"] > now:
            continue
        if allowed is not None and key[0] not in allowed:
            continue
        row.update(state="dispatching", updated_at=now)
        due.append(_snapshot(row))
        if len(due) >= 100:
            break
    return due


def due_expired(allowed_chat_ids: Optional[set[int]] = None) -> list[dict]:
    now = time.time()
    allowed = None if allowed_chat_ids is None else set(allowed_chat_ids)
    due = []
    for key, row in list(_challenges.items()):
        if row.get("state") != "active" or row.get("expires_at", 0) > now:
            continue
        if allowed is not None and key[0] not in allowed:
            continue
        row.update(state="timeout_processing", updated_at=now)
        due.append(_snapshot(row))
        if len(due) >= 100:
            break
    return due


def claim_due_pending(allowed_chat_ids: Optional[set[int]] = None) -> list[dict]:
    now = time.time()
    allowed = None if allowed_chat_ids is None else set(allowed_chat_ids)
    due = []
    for key, row in list(_challenges.items()):
        state = row.get("state")
        if state not in {"passed_pending", "kick_pending"}:
            continue
        if row.get("next_action_at") is not None and row["next_action_at"] > now:
            continue
        if allowed is not None and key[0] not in allowed:
            continue
        row.update(
            state="passed_processing" if state == "passed_pending" else "kick_processing",
            updated_at=now,
        )
        due.append(_snapshot(row))
        if len(due) >= 100:
            break
    return due


def claim_pending_for(chat_id: int, user_id: int, action: str) -> Optional[dict]:
    expected_state, processing_state = {
        "pass": ("passed_pending", "passed_processing"),
        "kick": ("kick_pending", "kick_processing"),
    }.get(action, (None, None))
    if expected_state is None:
        raise ValueError("Unsupported pending action")
    row = _get_mutable(chat_id, user_id)
    now = time.time()
    if not row or row.get("state") != expected_state:
        return None
    if row.get("next_action_at") is not None and row["next_action_at"] > now:
        return None
    row.update(state=processing_state, updated_at=now)
    return _snapshot(row)


def set_state(
    chat_id: int,
    user_id: int,
    state: str,
    *,
    next_action_at: Optional[float] = None,
) -> None:
    row = _get_mutable(chat_id, user_id)
    if row is None:
        return
    now = time.time()
    if next_action_at is None and state in {"passed_pending", "kick_pending"}:
        next_action_at = now
    row.update(state=state, updated_at=now, next_action_at=next_action_at)


def mark_waiting(
    chat_id: int,
    user_id: int,
    attempt: int,
    delay_seconds: int,
) -> None:
    row = _get_mutable(chat_id, user_id)
    if row is None:
        return
    now = time.time()
    row.update(
        state="waiting",
        attempt=int(attempt),
        updated_at=now,
        next_action_at=now + max(0, delay_seconds),
        expected_value=None,
        challenge_token=None,
        expires_at=0.0,
    )


def mark_notice_sent(chat_id: int, user_id: int) -> None:
    row = _get_mutable(chat_id, user_id)
    if row:
        row.update(notice_sent=True, updated_at=time.time())


def retry_state(chat_id: int, user_id: int, state: str, delay_seconds: int = 30) -> None:
    set_state(chat_id, user_id, state, next_action_at=time.time() + max(0, delay_seconds))


def delete(chat_id: int, user_id: int) -> None:
    _challenges.pop(_key(chat_id, user_id), None)


def recover_stale_states(stale_seconds: int = 300) -> None:
    """Recover only in-process interrupted transitions; nothing survives restart."""
    now = time.time()
    cutoff = now - max(0, stale_seconds)
    for row in _challenges.values():
        if row.get("updated_at", now) >= cutoff:
            continue
        state = row.get("state")
        if state in {"processing", "timeout_processing"}:
            row.update(state="active", updated_at=now, next_action_at=None)
        elif state in {"preparing", "sending", "dispatching"}:
            row.update(
                state="queued", updated_at=now, next_action_at=now,
                message_id=0, expected_value=None, challenge_token=None, expires_at=0.0,
            )
        elif state == "passed_processing":
            row.update(state="passed_pending", updated_at=now, next_action_at=now)
        elif state == "kick_processing":
            row.update(state="kick_pending", updated_at=now, next_action_at=now)
