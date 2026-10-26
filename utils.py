import asyncio
import logging
import time
from typing import Awaitable, Callable, TypeVar

from aiogram import Bot, types
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from captcha_payload import build_callback_data
from constants import CAPTCHA_LOCK_SECONDS
from permissions_policy import (
    CAPTCHA_LOCKED_PERMISSIONS,
    VERIFIED_MEMBER_PERMISSIONS,
    validate_permissions_policy,
)

T = TypeVar("T")
TELEGRAM_API_SEMAPHORE = asyncio.Semaphore(8)

validate_permissions_policy()

async def telegram_call(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
) -> T:
    """Bound concurrent Telegram calls and retry transient API errors."""
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
    raise RuntimeError("Unreachable Telegram retry state")


def locked_permissions():
    return types.ChatPermissions(**CAPTCHA_LOCKED_PERMISSIONS)


def verified_member_permissions():
    """Permissions explicitly granted after a successful CAPTCHA.

    Telegram applies these restrictions/rights to the member in the whole
    supergroup, including all forum topics. This does not change chat defaults
    or permissions of any other member.
    """
    return types.ChatPermissions(**VERIFIED_MEMBER_PERMISSIONS)


async def apply_captcha_restriction(
    bot: Bot,
    chat_id: int,
    user_id: int,
    *,
    restrict: bool,
) -> None:
    """Apply the temporary CAPTCHA lock or the explicit verified-member policy.

    The lock carries an expiry so that state lost on restart cannot keep the
    member muted forever; the verified-member policy has none.
    """
    permissions = locked_permissions() if restrict else verified_member_permissions()
    until_date = int(time.time()) + CAPTCHA_LOCK_SECONDS if restrict else None
    await telegram_call(
        lambda: bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=permissions,
            until_date=until_date,
            use_independent_chat_permissions=True,
        )
    )


def get_dice_keyboard(challenge_token: str, user_id: int) -> InlineKeyboardBuilder:
    """Build buttons that carry a selected number, not a precomputed verdict."""
    builder = InlineKeyboardBuilder()
    for number in range(1, 7):
        callback_data = build_callback_data(user_id, challenge_token, number)
        builder.add(
            types.InlineKeyboardButton(text=str(number), callback_data=callback_data)
        )
    builder.adjust(3)
    return builder
