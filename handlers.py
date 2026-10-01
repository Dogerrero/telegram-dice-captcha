import asyncio
import logging

from aiogram import F, Bot, Router, types
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated
from constants import (
    CORRECT_ANSWER_PREFIX,
    WRONG_ANSWER_PREFIX,
    DICE_SEND_MSG,
    CORRECT_ANSWER_MSG,
    WRONG_USER_MSG,
    WRONG_ANSWER_MSG,
    BAN_TIMEOUT,
    CAPTCHA_TIMEOUT,
    MAX_ATTEMPTS,
    MESSAGE_DELETE_TIMEOUT,
)
from utils import get_callback_user_info, get_dice_keyboard, set_permissions_to

router = Router()
_active_challenges = {}
_attempts = {}


async def _kick_user(bot: Bot, chat_id: int, user_id: int) -> None:
    key = (chat_id, user_id)
    _active_challenges.pop(key, None)
    _attempts.pop(key, None)
    try:
        notice = await bot.send_message(chat_id, "Пользователь исключён: капча не пройдена за 2 попытки.")
        asyncio.create_task(_delete_message_after_timeout(bot, chat_id, notice.message_id))
    except Exception:
        logging.exception("Failed to announce captcha kick for user %s", user_id)
    try:
        # Ban then unban removes the member but allows a later rejoin (and a fresh captcha).
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
    except Exception:
        logging.exception("Failed to remove user %s from chat %s", user_id, chat_id)


async def _send_challenge(bot: Bot, chat_id: int, user_id: int, attempt: int) -> None:
    dice = await bot.send_dice(chat_id, emoji="🎲")
    asyncio.create_task(_delete_message_after_timeout(bot, chat_id, dice.message_id))
    dice_value = dice.dice.value
    keyboard = get_dice_keyboard(dice_value=dice_value, user_id=user_id)
    message = await bot.send_message(chat_id, DICE_SEND_MSG, reply_markup=keyboard.as_markup())
    key = (chat_id, user_id)
    _attempts[key] = attempt
    _active_challenges[key] = message.message_id
    asyncio.create_task(_expire_challenge_after_timeout(bot, chat_id, user_id, message.message_id))


async def _delete_message_after_timeout(bot: Bot, chat_id: int, message_id: int) -> None:
    await asyncio.sleep(MESSAGE_DELETE_TIMEOUT)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        logging.exception("Failed to delete expired message %s", message_id)


async def _next_attempt_after_wrong(bot: Bot, chat_id: int, user_id: int, attempt: int) -> None:
    await asyncio.sleep(BAN_TIMEOUT)
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if member.status.value not in {"left", "kicked"} and (chat_id, user_id) not in _active_challenges:
            await _send_challenge(bot, chat_id, user_id, attempt)
    except Exception:
        logging.exception("Failed to resend captcha for user %s in chat %s", user_id, chat_id)


async def _expire_challenge_after_timeout(bot: Bot, chat_id: int, user_id: int, message_id: int) -> None:
    await asyncio.sleep(CAPTCHA_TIMEOUT)
    key = (chat_id, user_id)
    if _active_challenges.get(key) != message_id:
        return
    _active_challenges.pop(key, None)
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception:
        logging.exception("Failed to deactivate expired captcha message %s", message_id)

    attempt = _attempts.get(key, 1)
    if attempt >= MAX_ATTEMPTS:
        await _kick_user(bot, chat_id, user_id)
    else:
        await _send_challenge(bot, chat_id, user_id, attempt + 1)


def _is_active(callback: types.CallbackQuery, user_id: int, chat_id: int) -> bool:
    return (
        callback.message is not None
        and _active_challenges.get((chat_id, user_id)) == callback.message.message_id
    )


@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def new_member_handler(event: ChatMemberUpdated, bot: Bot):
    user_id = event.new_chat_member.user.id
    chat_id = event.chat.id
    await set_permissions_to(user_id=user_id, chat_id=chat_id, permissions=False, bot=bot)
    await _send_challenge(bot, chat_id, user_id, attempt=1)


@router.callback_query(F.data.startswith(CORRECT_ANSWER_PREFIX))
async def correct_answer_handler(callback: types.CallbackQuery, bot: Bot):
    user_id, is_target_user, chat_id = get_callback_user_info(
        callback=callback, prefix=CORRECT_ANSWER_PREFIX)

    if not is_target_user:
        await callback.answer(WRONG_USER_MSG, show_alert=True)
    elif not _is_active(callback, user_id, chat_id):
        await callback.answer("Эта капча устарела. Дождитесь новой.", show_alert=True)
    else:
        _active_challenges.pop((chat_id, user_id), None)
        _attempts.pop((chat_id, user_id), None)
        await set_permissions_to(user_id=user_id, chat_id=chat_id, permissions=True, bot=bot)
        await callback.answer(CORRECT_ANSWER_MSG)
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith(WRONG_ANSWER_PREFIX))
async def wrong_answer_handler(callback: types.CallbackQuery, bot: Bot):
    user_id, is_target_user, chat_id = get_callback_user_info(
        callback=callback, prefix=WRONG_ANSWER_PREFIX)

    if not is_target_user:
        await callback.answer(WRONG_USER_MSG, show_alert=True)
    elif not _is_active(callback, user_id, chat_id):
        await callback.answer("Эта капча устарела. Дождитесь новой.", show_alert=True)
    else:
        await callback.answer(WRONG_ANSWER_MSG, show_alert=True)
        await set_permissions_to(user_id=user_id, chat_id=chat_id, permissions=False, bot=bot)
        key = (chat_id, user_id)
        attempt = _attempts.get(key, 1)
        _active_challenges.pop(key, None)
        await callback.message.edit_reply_markup(reply_markup=None)
        if attempt >= MAX_ATTEMPTS:
            await _kick_user(bot, chat_id, user_id)
        else:
            asyncio.create_task(_next_attempt_after_wrong(bot, chat_id, user_id, attempt + 1))
