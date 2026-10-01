import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from aiogram import Bot, types
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from constants import CORRECT_ANSWER_PREFIX, WRONG_ANSWER_PREFIX

T = TypeVar("T")
TELEGRAM_API_SEMAPHORE = asyncio.Semaphore(8)


async def telegram_call(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
) -> T:
    """Bound concurrent Telegram API calls and handle transient 429/server errors."""
    async with TELEGRAM_API_SEMAPHORE:
        for attempt in range(retries + 1):
            try:
                return await operation()
            except TelegramRetryAfter as exc:
                if attempt >= retries:
                    raise
                await asyncio.sleep(min(exc.retry_after, 60))
            except TelegramAPIError:
                if attempt >= retries:
                    raise
                await asyncio.sleep(min(2 ** attempt, 8))


def get_callback_user_info(
    callback: types.CallbackQuery,
    prefix: str,
):
    """Strictly parse callback data and bind it to the clicking Telegram user."""
    if callback.message is None or callback.data is None:
        return None, False, None

    if not callback.data.startswith(prefix):
        return None, False, callback.message.chat.id

    raw_user_id = callback.data[len(prefix):]
    if not raw_user_id.isdigit():
        return None, False, callback.message.chat.id

    user_id = int(raw_user_id)
    if user_id <= 0:
        return None, False, callback.message.chat.id

    chat_id = callback.message.chat.id
    return user_id, callback.from_user.id == user_id, chat_id


async def set_permissions_to(
    user_id: int,
    chat_id: int,
    permissions: bool,
    bot: Bot,
) -> None:
    """Set a complete, explicit permission profile for a verified/unverified member."""
    value = permissions
    await telegram_call(
        lambda: bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=types.ChatPermissions(
                can_send_messages=value,
                can_send_audios=value,
                can_send_documents=value,
                can_send_photos=value,
                can_send_videos=value,
                can_send_video_notes=value,
                can_send_voice_notes=value,
                can_send_polls=value,
                can_send_other_messages=value,
                can_add_web_page_previews=value,
            ),
            use_independent_chat_permissions=True,
        )
    )


def get_dice_keyboard(dice_value: int, user_id: int) -> InlineKeyboardBuilder:
    """Create a six-button keyboard with exactly one correct callback."""
    builder = InlineKeyboardBuilder()

    for number in range(1, 7):
        callback_data = (
            f"{CORRECT_ANSWER_PREFIX}{user_id}"
            if dice_value == number
            else f"{WRONG_ANSWER_PREFIX}{user_id}"
        )
        builder.add(
            types.InlineKeyboardButton(
                text=str(number),
                callback_data=callback_data,
            )
        )

    builder.adjust(3)
    return builder
