"""In-memory message-deletion queue; no database or user history is stored."""

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from utils import telegram_call

RETRY_SECONDS = 60
POLL_SECONDS = 5
MAX_DELETE_RETRIES = 10
_pending_deletions: dict[tuple[int, int], dict] = {}


def schedule_message_deletion(chat_id: int, message_id: int, delay_seconds: int) -> None:
    """Queue a bot message for deletion after delay_seconds (default: 5 minutes)."""
    _pending_deletions[(int(chat_id), int(message_id))] = {
        "delete_at": time.time() + max(0, delay_seconds),
        "failures": 0,
    }


def pending_deletion_count() -> int:
    return len(_pending_deletions)


async def message_deletion_worker(bot: Bot) -> None:
    while True:
        now = time.time()
        due = sorted(
            (
                (key, dict(record))
                for key, record in _pending_deletions.items()
                if record["delete_at"] <= now
            ),
            key=lambda item: item[1]["delete_at"],
        )[:100]

        for (chat_id, message_id), record in due:
            # It may have been replaced while another callback was processing.
            if (chat_id, message_id) not in _pending_deletions:
                continue
            try:
                await telegram_call(
                    lambda: bot.delete_message(chat_id=chat_id, message_id=message_id)
                )
            except TelegramBadRequest as exc:
                error_text = str(exc).lower()
                if any(
                    phrase in error_text
                    for phrase in (
                        "message to delete not found",
                        "message can't be deleted",
                        "message can\'t be deleted",
                    )
                ):
                    _pending_deletions.pop((chat_id, message_id), None)
                else:
                    logging.warning(
                        "Telegram rejected deletion chat=%s message=%s: %s",
                        chat_id, message_id, exc,
                    )
                    _retry_later(chat_id, message_id, record["failures"])
            except Exception:
                logging.exception(
                    "Failed to delete bot message chat=%s message=%s",
                    chat_id, message_id,
                )
                _retry_later(chat_id, message_id, record["failures"])
            else:
                _pending_deletions.pop((chat_id, message_id), None)

        await asyncio.sleep(POLL_SECONDS)


def _retry_later(chat_id: int, message_id: int, failures: int) -> None:
    key = (chat_id, message_id)
    if key not in _pending_deletions:
        return
    next_failures = failures + 1
    if next_failures >= MAX_DELETE_RETRIES:
        _pending_deletions.pop(key, None)
        logging.error(
            "Giving up deleting bot message chat=%s message=%s after %s failed attempts. "
            "Check bot Delete messages permission and chat configuration.",
            chat_id,
            message_id,
            next_failures,
        )
        return
    _pending_deletions[key] = {
        "delete_at": time.time() + RETRY_SECONDS,
        "failures": next_failures,
    }
