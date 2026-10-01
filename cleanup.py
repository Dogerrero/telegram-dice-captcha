import asyncio
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from utils import telegram_call

DB_PATH = Path("/var/lib/telegram-dice-captcha/pending_deletions.sqlite3")
RETRY_SECONDS = 60
POLL_SECONDS = 5


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
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


def init_cleanup_db() -> None:
    with _connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS pending_deletions (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                delete_at REAL NOT NULL,
                failures INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, message_id)
            )"""
        )


def schedule_message_deletion(chat_id: int, message_id: int, delay_seconds: int) -> None:
    delete_at = time.time() + delay_seconds
    with _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO pending_deletions "
            "(chat_id, message_id, delete_at, failures) VALUES (?, ?, ?, 0)",
            (chat_id, message_id, delete_at),
        )


async def message_deletion_worker(bot: Bot) -> None:
    init_cleanup_db()

    while True:
        now = time.time()
        with _connect() as db:
            due = list(
                db.execute(
                    "SELECT chat_id, message_id, failures "
                    "FROM pending_deletions "
                    "WHERE delete_at <= ? ORDER BY delete_at LIMIT 100",
                    (now,),
                )
            )

        for chat_id, message_id, failures in due:
            try:
                await telegram_call(
                    lambda: bot.delete_message(
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                )
            except TelegramBadRequest as exc:
                error_text = str(exc).lower()
                if (
                    "message to delete not found" in error_text
                    or "message can't be deleted" in error_text
                ):
                    _delete_record(chat_id, message_id)
                else:
                    logging.exception(
                        "Telegram rejected deletion chat=%s message=%s",
                        chat_id,
                        message_id,
                    )
                    _retry_later(chat_id, message_id, failures)
            except Exception:
                logging.exception(
                    "Failed to delete bot message chat=%s message=%s",
                    chat_id,
                    message_id,
                )
                _retry_later(chat_id, message_id, failures)
            else:
                _delete_record(chat_id, message_id)

        await asyncio.sleep(POLL_SECONDS)


def _delete_record(chat_id: int, message_id: int) -> None:
    with _connect() as db:
        db.execute(
            "DELETE FROM pending_deletions WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )


def _retry_later(chat_id: int, message_id: int, failures: int) -> None:
    with _connect() as db:
        db.execute(
            "UPDATE pending_deletions SET delete_at = ?, failures = ? "
            "WHERE chat_id = ? AND message_id = ?",
            (time.time() + RETRY_SECONDS, failures + 1, chat_id, message_id),
        )
