import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, TypeVar

from aiogram import Bot, types
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from captcha_payload import build_callback_data

T = TypeVar("T")
TELEGRAM_API_SEMAPHORE = asyncio.Semaphore(8)

# Permission fields currently supported by Telegram Bot API / aiogram 3.31.
_PERMISSION_FIELDS = (
    "can_send_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_react_to_messages",
    "can_edit_tag",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_topics",
)


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


def _permissions_dict(permissions) -> Optional[dict]:
    if permissions is None:
        return None
    if hasattr(permissions, "model_dump"):
        value = permissions.model_dump(exclude_none=True)
    elif isinstance(permissions, dict):
        value = dict(permissions)
    else:
        value = {
            field: getattr(permissions, field)
            for field in _PERMISSION_FIELDS
            if hasattr(permissions, field) and getattr(permissions, field) is not None
        }
    result = {
        key: bool(val) for key, val in value.items()
        if key in _PERMISSION_FIELDS and val is not None
    }
    return result or None


def permissions_snapshot_for_restricted_member(member) -> Optional[dict]:
    """Capture explicit moderator restrictions if the member was restricted already."""
    status = getattr(member, "status", None)
    status = getattr(status, "value", status)
    if status != "restricted":
        return None
    result = {}
    for field in _PERMISSION_FIELDS:
        if hasattr(member, field):
            value = getattr(member, field)
            if value is not None:
                result[field] = bool(value)
    # Preserve a moderator-set restriction expiry along with its permissions.
    until_date = getattr(member, "until_date", None)
    if until_date is None:
        result["__until_date"] = 0  # no expiry / permanent restriction
    elif hasattr(until_date, "timestamp"):
        if getattr(until_date, "tzinfo", None) is None:
            until_date = until_date.replace(tzinfo=timezone.utc)
        result["__until_date"] = int(until_date.timestamp())
    else:
        try:
            result["__until_date"] = int(until_date)
        except (TypeError, ValueError):
            result["__until_date"] = 0
    return result or None


def _chat_permissions_from_dict(data: dict):
    allowed = {
        field: bool(value) for field, value in data.items()
        if field in _PERMISSION_FIELDS and value is not None
    }
    return types.ChatPermissions(**allowed)


def locked_permissions():
    return types.ChatPermissions(**{field: False for field in _PERMISSION_FIELDS})


async def apply_captcha_restriction(
    bot: Bot,
    chat_id: int,
    user_id: int,
    *,
    restrict: bool,
    original_permissions: Optional[str] = None,
) -> None:
    """Restrict newcomers or restore saved member/group defaults without granting extras."""
    until_date = None
    if restrict:
        permissions = locked_permissions()
    else:
        restored = None
        if original_permissions:
            try:
                restored = json.loads(original_permissions)
            except (TypeError, json.JSONDecodeError):
                logging.exception(
                    "Invalid saved permission snapshot chat=%s user=%s",
                    chat_id,
                    user_id,
                )

        if restored:
            raw_until = restored.pop("__until_date", None)
            # Legacy snapshots omit expiry metadata; their permissions are retained.
            expiry_is_valid = (
                raw_until is None or int(raw_until) == 0 or int(raw_until) > time.time()
            )
            saved_permissions = {
                field: value for field, value in restored.items()
                if field in _PERMISSION_FIELDS
            }
            if saved_permissions and expiry_is_valid:
                permissions = _chat_permissions_from_dict(saved_permissions)
                if raw_until is not None and int(raw_until) > time.time():
                    until_date = datetime.fromtimestamp(int(raw_until), tz=timezone.utc)
            else:
                restored = None

        if not restored:
            chat = await telegram_call(lambda: bot.get_chat(chat_id))
            chat_defaults = _permissions_dict(getattr(chat, "permissions", None))
            if chat_defaults is None:
                # Fail closed rather than accidentally granting all permissions.
                raise RuntimeError(
                    f"Chat {chat_id} did not return default permissions; "
                    f"refusing to unrestrict user {user_id}"
                )
            permissions = _chat_permissions_from_dict(chat_defaults)

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
