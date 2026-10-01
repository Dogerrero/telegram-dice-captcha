import asyncio
import logging
from html import escape

from aiogram import F, Bot, Router, types
from aiogram.exceptions import TelegramAPIError
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
from cleanup import schedule_message_deletion
from captcha_state import (
    active_count,
    activate_challenge,
    begin_challenge,
    claim_active,
    claim_expired,
    claim_due_waiting,
    delete,
    get,
    init_db,
    mark_waiting,
    reset_processing,
    reactivate,
    set_state,
)
from rate_limit import MAX_ACTIVE_CAPTCHAS_PER_CHAT, allow_new_join
from utils import (
    get_callback_user_info,
    get_dice_keyboard,
    set_permissions_to,
    telegram_call,
)

router = Router()


async def _kick_user(bot: Bot, chat_id: int, user_id: int) -> None:
    delete(chat_id, user_id)

    try:
        notice = await telegram_call(
            lambda: bot.send_message(
                chat_id,
                "Пользователь исключён: капча не пройдена за 2 попытки.",
            )
        )
        schedule_message_deletion(
            chat_id,
            notice.message_id,
            MESSAGE_DELETE_TIMEOUT,
        )
    except TelegramAPIError:
        logging.exception(
            "Failed to announce captcha kick for user %s in chat %s",
            user_id,
            chat_id,
        )

    try:
        await telegram_call(
            lambda: bot.ban_chat_member(
                chat_id=chat_id,
                user_id=user_id,
            )
        )
        await telegram_call(
            lambda: bot.unban_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                only_if_banned=True,
            )
        )
    except TelegramAPIError:
        logging.exception(
            "Failed to remove user %s from chat %s",
            user_id,
            chat_id,
        )


async def _send_challenge(
    bot: Bot,
    chat_id: int,
    user_id: int,
    attempt: int,
) -> None:
    if active_count(chat_id) >= MAX_ACTIVE_CAPTCHAS_PER_CHAT:
        logging.warning(
            "Captcha capacity reached chat=%s user=%s",
            chat_id,
            user_id,
        )
        await _kick_user(bot, chat_id, user_id)
        return

    # Create a persistent 'sending' state before the first API call. This
    # prevents a callback from being accepted before the message ID is bound.
    begin_challenge(chat_id, user_id, attempt)

    try:
        display_name = "Пользователь"
        try:
            member = await telegram_call(
                lambda: bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            )
            display_name = escape(
                " ".join(
                    part for part in (member.user.first_name, member.user.last_name)
                    if part
                )
            ) or display_name
        except TelegramAPIError:
            logging.exception(
                "Could not fetch display name for captcha user %s in chat %s",
                user_id,
                chat_id,
            )

        dice = await telegram_call(
            lambda: bot.send_dice(chat_id, emoji="🎲")
        )
        schedule_message_deletion(
            chat_id,
            dice.message_id,
            MESSAGE_DELETE_TIMEOUT,
        )

        keyboard = get_dice_keyboard(
            dice_value=dice.dice.value,
            user_id=user_id,
        )
        message = await telegram_call(
            lambda: bot.send_message(
                chat_id,
                f'<a href="tg://user?id={user_id}">{display_name}</a>, {DICE_SEND_MSG}',
                parse_mode="HTML",
                reply_markup=keyboard.as_markup(),
            )
        )
        schedule_message_deletion(
            chat_id,
            message.message_id,
            MESSAGE_DELETE_TIMEOUT,
        )

        activate_challenge(
            chat_id,
            user_id,
            message.message_id,
            attempt,
            CAPTCHA_TIMEOUT,
        )
    except TelegramAPIError:
        delete(chat_id, user_id)
        raise


async def _process_expired(
    bot: Bot,
    challenge: dict,
) -> None:
    chat_id = challenge["chat_id"]
    user_id = challenge["user_id"]
    message_id = challenge["message_id"]
    attempt = challenge["attempt"]

    try:
        await telegram_call(
            lambda: bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        )
    except TelegramAPIError:
        logging.exception(
            "Failed to deactivate expired captcha message %s",
            message_id,
        )

    if attempt >= MAX_ATTEMPTS:
        await _kick_user(bot, chat_id, user_id)
        return

    mark_waiting(
        chat_id,
        user_id,
        attempt + 1,
        BAN_TIMEOUT,
    )


async def _process_waiting(
    bot: Bot,
    challenge: dict,
) -> None:
    chat_id = challenge["chat_id"]
    user_id = challenge["user_id"]
    attempt = challenge["attempt"]

    try:
        member = await telegram_call(
            lambda: bot.get_chat_member(
                chat_id=chat_id,
                user_id=user_id,
            )
        )
        if member.status.value in {"left", "kicked"}:
            delete(chat_id, user_id)
            return

        await _send_challenge(
            bot,
            chat_id,
            user_id,
            attempt,
        )
    except TelegramAPIError:
        logging.exception(
            "Failed to continue captcha for user %s in chat %s",
            user_id,
            chat_id,
        )
        # Keep the challenge persistent and retry later instead of losing it.
        mark_waiting(
            chat_id,
            user_id,
            attempt,
            BAN_TIMEOUT,
        )


async def captcha_state_worker(bot: Bot) -> None:
    """Recover expirations and delayed retries, including after process restart."""
    while True:
        try:
            for challenge in claim_due_waiting():
                await _process_waiting(bot, challenge)

            for challenge in _claim_expired_batch():
                await _process_expired(bot, challenge)
        except Exception:
            logging.exception("CAPTCHA state worker failed")
        await asyncio.sleep(5)


def _claim_expired_batch():
    # Kept as a small wrapper to make the worker easy to test.
    from captcha_state import due_expired
    return due_expired()


@router.chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
async def member_left_handler(event: ChatMemberUpdated, bot: Bot):
    user_id = event.old_chat_member.user.id
    chat_id = event.chat.id
    delete(chat_id, user_id)


@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def new_member_handler(event: ChatMemberUpdated, bot: Bot):
    user_id = event.new_chat_member.user.id
    chat_id = event.chat.id

    if not await allow_new_join(chat_id):
        logging.warning(
            "Join rate limit exceeded chat=%s user=%s; removing newcomer",
            chat_id,
            user_id,
        )
        await _kick_user(bot, chat_id, user_id)
        return

    if active_count(chat_id) >= MAX_ACTIVE_CAPTCHAS_PER_CHAT:
        logging.warning(
            "Captcha active limit exceeded chat=%s user=%s",
            chat_id,
            user_id,
        )
        await _kick_user(bot, chat_id, user_id)
        return

    try:
        await set_permissions_to(
            user_id=user_id,
            chat_id=chat_id,
            permissions=False,
            bot=bot,
        )
        await _send_challenge(
            bot,
            chat_id,
            user_id,
            attempt=1,
        )
    except TelegramAPIError:
        logging.exception(
            "Failed to initialize captcha for user %s in chat %s",
            user_id,
            chat_id,
        )
        delete(chat_id, user_id)
        await _kick_user(bot, chat_id, user_id)


@router.callback_query(F.data.startswith(CORRECT_ANSWER_PREFIX))
async def correct_answer_handler(
    callback: types.CallbackQuery,
    bot: Bot,
):
    user_id, is_target_user, chat_id = get_callback_user_info(
        callback,
        CORRECT_ANSWER_PREFIX,
    )

    if not is_target_user or user_id is None or chat_id is None:
        await callback.answer(WRONG_USER_MSG, show_alert=True)
        return

    if callback.message is None:
        await callback.answer("Некорректная капча.", show_alert=True)
        return

    # This is the security boundary: UPDATE ... WHERE state='active' makes
    # duplicate/concurrent callbacks mutually exclusive.
    claimed = claim_active(
        chat_id,
        user_id,
        callback.message.message_id,
    )
    if claimed is None:
        await callback.answer(
            "Эта капча устарела. Дождитесь новой.",
            show_alert=True,
        )
        return

    try:
        await set_permissions_to(
            user_id=user_id,
            chat_id=chat_id,
            permissions=True,
            bot=bot,
        )
        set_state(chat_id, user_id, "passed")
        await callback.answer(CORRECT_ANSWER_MSG)
        await telegram_call(
            lambda: callback.message.edit_reply_markup(
                reply_markup=None
            )
        )
        delete(chat_id, user_id)
    except TelegramAPIError:
        # Give the user another chance if Telegram failed transiently.
        reactivate(chat_id, user_id, CAPTCHA_TIMEOUT)
        logging.exception(
            "Failed to verify user %s in chat %s",
            user_id,
            chat_id,
        )
        try:
            await callback.answer(
                "Не удалось подтвердить капчу. Попробуйте ещё раз.",
                show_alert=True,
            )
        except TelegramAPIError:
            pass


@router.callback_query(F.data.startswith(WRONG_ANSWER_PREFIX))
async def wrong_answer_handler(
    callback: types.CallbackQuery,
    bot: Bot,
):
    user_id, is_target_user, chat_id = get_callback_user_info(
        callback,
        WRONG_ANSWER_PREFIX,
    )

    if not is_target_user or user_id is None or chat_id is None:
        await callback.answer(WRONG_USER_MSG, show_alert=True)
        return

    if callback.message is None:
        await callback.answer("Некорректная капча.", show_alert=True)
        return

    claimed = claim_active(
        chat_id,
        user_id,
        callback.message.message_id,
    )
    if claimed is None:
        await callback.answer(
            "Эта капча устарела. Дождитесь новой.",
            show_alert=True,
        )
        return

    attempt = claimed["attempt"]

    try:
        await callback.answer(WRONG_ANSWER_MSG, show_alert=True)
        await set_permissions_to(
            user_id=user_id,
            chat_id=chat_id,
            permissions=False,
            bot=bot,
        )
        await telegram_call(
            lambda: callback.message.edit_reply_markup(
                reply_markup=None
            )
        )

        if attempt >= MAX_ATTEMPTS:
            await _kick_user(bot, chat_id, user_id)
        else:
            mark_waiting(
                chat_id,
                user_id,
                attempt + 1,
                BAN_TIMEOUT,
            )
    except TelegramAPIError:
        reactivate(chat_id, user_id, CAPTCHA_TIMEOUT)
        logging.exception(
            "Failed to process wrong captcha answer for user %s in chat %s",
            user_id,
            chat_id,
        )
        try:
            await callback.answer(
                "Не удалось обработать ответ. Попробуйте ещё раз.",
                show_alert=True,
            )
        except TelegramAPIError:
            pass


def initialize_state() -> None:
    init_db()
    reset_processing()
