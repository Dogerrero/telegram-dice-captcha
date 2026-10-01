import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path("/var/lib/telegram-dice-captcha/pending_deletions.sqlite3")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("PRAGMA busy_timeout=10000")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    with connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS captcha_challenges (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id INTEGER,
                attempt INTEGER NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                next_action_at REAL,
                PRIMARY KEY (chat_id, user_id)
            )"""
        )
        # Migrate installations created by the previous hardening draft.
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(captcha_challenges)")
        }
        if "updated_at" not in columns:
            db.execute(
                "ALTER TABLE captcha_challenges ADD COLUMN updated_at REAL NOT NULL DEFAULT 0"
            )
            db.execute(
                "UPDATE captcha_challenges SET updated_at = created_at WHERE updated_at = 0"
            )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_captcha_due "
            "ON captcha_challenges (state, next_action_at)"
        )


def active_count(chat_id: int) -> int:
    with connect() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM captcha_challenges "
            "WHERE chat_id = ? AND state IN "
            "('sending', 'active', 'processing', 'waiting')",
            (chat_id,),
        ).fetchone()
    return int(row[0])


def begin_challenge(chat_id: int, user_id: int, attempt: int) -> None:
    now = time.time()
    with connect() as db:
        db.execute(
            """INSERT INTO captcha_challenges
               (chat_id, user_id, message_id, attempt, state,
                created_at, updated_at, expires_at, next_action_at)
               VALUES (?, ?, 0, ?, 'sending', ?, ?, 0, NULL)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
                 message_id=0, attempt=excluded.attempt, state='sending',
                 created_at=excluded.created_at, updated_at=excluded.updated_at,
                 expires_at=0, next_action_at=NULL""",
            (chat_id, user_id, attempt, now, now),
        )


def activate_challenge(
    chat_id: int,
    user_id: int,
    message_id: int,
    attempt: int,
    timeout_seconds: int,
) -> None:
    now = time.time()
    with connect() as db:
        db.execute(
            """UPDATE captcha_challenges
               SET message_id = ?, attempt = ?, state = 'active',
                   created_at = ?, updated_at = ?, expires_at = ?,
                   next_action_at = NULL
               WHERE chat_id = ? AND user_id = ? AND state = 'sending'""",
            (
                message_id,
                attempt,
                now,
                now,
                now + timeout_seconds,
                chat_id,
                user_id,
            ),
        )


def get(chat_id: int, user_id: int) -> Optional[dict]:
    with connect() as db:
        row = db.execute(
            """SELECT chat_id, user_id, message_id, attempt, state,
                      created_at, updated_at, expires_at, next_action_at
               FROM captcha_challenges
               WHERE chat_id = ? AND user_id = ?""",
            (chat_id, user_id),
        ).fetchone()
    if row is None:
        return None
    keys = (
        "chat_id", "user_id", "message_id", "attempt",
        "state", "created_at", "updated_at", "expires_at",
        "next_action_at",
    )
    return dict(zip(keys, row))


def claim_active(chat_id: int, user_id: int, message_id: int) -> Optional[dict]:
    now = time.time()
    with connect() as db:
        cursor = db.execute(
            """UPDATE captcha_challenges
               SET state = 'processing', updated_at = ?
               WHERE chat_id = ? AND user_id = ?
                 AND message_id = ? AND state = 'active'
                 AND expires_at > ?""",
            (now, chat_id, user_id, message_id, now),
        )
        if cursor.rowcount != 1:
            return None
        row = db.execute(
            """SELECT chat_id, user_id, message_id, attempt, state,
                      created_at, updated_at, expires_at, next_action_at
               FROM captcha_challenges
               WHERE chat_id = ? AND user_id = ?""",
            (chat_id, user_id),
        ).fetchone()
    keys = (
        "chat_id", "user_id", "message_id", "attempt",
        "state", "created_at", "updated_at", "expires_at",
        "next_action_at",
    )
    return dict(zip(keys, row))


def claim_expired(chat_id: int, user_id: int, message_id: int) -> Optional[dict]:
    now = time.time()
    with connect() as db:
        cursor = db.execute(
            """UPDATE captcha_challenges
               SET state = 'processing', updated_at = ?
               WHERE chat_id = ? AND user_id = ?
                 AND message_id = ? AND state = 'active'
                 AND expires_at <= ?""",
            (now, chat_id, user_id, message_id, now),
        )
        if cursor.rowcount != 1:
            return None
        row = db.execute(
            """SELECT chat_id, user_id, message_id, attempt, state,
                      created_at, updated_at, expires_at, next_action_at
               FROM captcha_challenges
               WHERE chat_id = ? AND user_id = ?""",
            (chat_id, user_id),
        ).fetchone()
    keys = (
        "chat_id", "user_id", "message_id", "attempt",
        "state", "created_at", "updated_at", "expires_at",
        "next_action_at",
    )
    return dict(zip(keys, row))


def claim_due_waiting() -> list[dict]:
    now = time.time()
    result = []
    with connect() as db:
        rows = list(
            db.execute(
                """SELECT chat_id, user_id, message_id, attempt, state,
                          created_at, updated_at, expires_at, next_action_at
                   FROM captcha_challenges
                   WHERE state = 'waiting'
                     AND next_action_at <= ?
                   ORDER BY next_action_at
                   LIMIT 100""",
                (now,),
            )
        )
        for row in rows:
            chat_id, user_id = row[0], row[1]
            cursor = db.execute(
                """UPDATE captcha_challenges
                   SET state = 'processing', updated_at = ?
                   WHERE chat_id = ? AND user_id = ? AND state = 'waiting'
                     AND next_action_at <= ?""",
                (now, chat_id, user_id, now),
            )
            if cursor.rowcount == 1:
                keys = (
                    "chat_id", "user_id", "message_id", "attempt",
                    "state", "created_at", "updated_at", "expires_at",
                    "next_action_at",
                )
                result.append(dict(zip(keys, row)))
    return result


def due_expired() -> list[dict]:
    now = time.time()
    result = []
    with connect() as db:
        rows = list(
            db.execute(
                """SELECT chat_id, user_id, message_id, attempt, state,
                          created_at, updated_at, expires_at, next_action_at
                   FROM captcha_challenges
                   WHERE state = 'active' AND expires_at <= ?
                   ORDER BY expires_at
                   LIMIT 100""",
                (now,),
            )
        )
        for row in rows:
            chat_id, user_id, message_id = row[0], row[1], row[2]
            cursor = db.execute(
                """UPDATE captcha_challenges
                   SET state = 'processing', updated_at = ?
                   WHERE chat_id = ? AND user_id = ?
                     AND message_id = ? AND state = 'active'
                     AND expires_at <= ?""",
                (now, chat_id, user_id, message_id, now),
            )
            if cursor.rowcount == 1:
                keys = (
                    "chat_id", "user_id", "message_id", "attempt",
                    "state", "created_at", "updated_at", "expires_at",
                    "next_action_at",
                )
                result.append(dict(zip(keys, row)))
    return result


def set_state(
    chat_id: int,
    user_id: int,
    state: str,
    *,
    next_action_at: Optional[float] = None,
) -> None:
    with connect() as db:
        db.execute(
            """UPDATE captcha_challenges
               SET state = ?, updated_at = ?, next_action_at = ?
               WHERE chat_id = ? AND user_id = ?""",
            (state, time.time(), next_action_at, chat_id, user_id),
        )



def reactivate(
    chat_id: int,
    user_id: int,
    timeout_seconds: int,
) -> None:
    now = time.time()
    with connect() as db:
        db.execute(
            """UPDATE captcha_challenges
               SET state = 'active', updated_at = ?, expires_at = ?,
                   next_action_at = NULL
               WHERE chat_id = ? AND user_id = ?""",
            (now, now + timeout_seconds, chat_id, user_id),
        )

def mark_waiting(
    chat_id: int,
    user_id: int,
    attempt: int,
    delay_seconds: int,
) -> None:
    with connect() as db:
        db.execute(
            """UPDATE captcha_challenges
               SET state = 'waiting', attempt = ?, updated_at = ?,
                   next_action_at = ?
               WHERE chat_id = ? AND user_id = ?""",
            (
                attempt,
                time.time(),
                time.time() + delay_seconds,
                chat_id,
                user_id,
            ),
        )


def delete(chat_id: int, user_id: int) -> None:
    with connect() as db:
        db.execute(
            "DELETE FROM captcha_challenges WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )


def reset_processing() -> None:
    """Recover jobs left in processing after a process crash."""
    now = time.time()
    with connect() as db:
        db.execute(
            """UPDATE captcha_challenges
               SET state = 'active', updated_at = ?
               WHERE state = 'processing' AND updated_at < ?""",
            (now, now - 120),
        )
