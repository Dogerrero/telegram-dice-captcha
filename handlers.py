import asyncio
import logging
import time
from html import escape

from aiogram import F, Bot, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER, ChatMemberUpdatedFilter, Command
from aiogram.types import ChatMemberUpdated
from aiogram.utils.keyboard import InlineKeyboardBuilder

from constants import (
    API_RETRY_DELAY,
    BAN_TIMEOUT,
    CAPTCHA_TIMEOUT,
    CORRECT_ANSWER_MSG,
    DICE_SEND_MSG,
    FAIL_BAN_SECONDS,
    KICK_NOTICE_MSG,
    MAX_ACTIVE_CAPTCHAS_PER_CHAT,
    MAX_ATTEMPTS,
    MESSAGE_DELETE_TIMEOUT,
    SECOND_WRONG_MSG,
    WRONG_ANSWER_MSG,
    WRONG_USER_MSG,
    allowed_chat_ids,
)
from cleanup import schedule_message_deletion
from captcha_payload import new_challenge_token, parse_captcha_callback
from captcha_state import (
    activate_challenge,
    active_count,
    resolve_active_answer,
    claim_due_pending,
    claim_due_waiting,
    claim_pending_for,
    delete,
    due_expired,
    get,
    mark_notice_sent,
    mark_kick_ban_issued,
    mark_waiting,
    pop_left_attempt,
    queue_challenge,
    recover_stale_states,
    remember_left_attempt,
    retry_state,
    set_state,
    start_sending,
)
from rate_limit import allow_new_join
from settings_store import (
    add_allowed_chat_id,
    clear_greeting,
    get_greeting,
    remove_allowed_chat_id,
    set_greeting,
)
from utils import (
    apply_captcha_restriction,
    get_dice_keyboard,
    telegram_call,
)

router = Router()
GREETING_EDIT_TIMEOUT_SECONDS = 300
_pending_greeting_edits: dict[tuple[int, int], dict] = {}


def _chat_is_allowed(chat_id: int) -> bool:
    ids = allowed_chat_ids()
    if not ids:
        logging.error(
            "ALLOWED_CHAT_IDS is empty; refusing to process chat %s", chat_id
        )
        return False
    if chat_id not in ids:
        logging.warning("Ignoring event from non-allow-listed chat %s", chat_id)
        return False
    return True


def _status(member) -> str:
    status = getattr(member, "status", None)
    return getattr(status, "value", status) or ""


async def _ensure_bot_can_restrict(bot: Bot, chat_id: int) -> None:
    """Fail loudly and retry later if Telegram admin rights are misconfigured."""
    bot_member = await telegram_call(
        lambda: bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
    )
    if _status(bot_member) != "administrator" or not bool(
        getattr(bot_member, "can_restrict_members", False)
    ):
        raise RuntimeError(
            f"Bot must be an administrator with Restrict members permission in chat {chat_id}"
        )


async def _remove_keyboard(bot: Bot, chat_id: int, message_id: int) -> None:
    if not message_id:
        return
    try:
        await telegram_call(
            lambda: bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        )
    except TelegramAPIError:
        # The database state already invalidates this challenge's callbacks.
        logging.info(
            "Could not remove keyboard chat=%s message=%s; stale callbacks are rejected",
            chat_id,
            message_id,
        )


async def _send_challenge(
    bot: Bot,
    chat_id: int,
    user_id: int,
    attempt: int,
) -> None:
    # Queued users do not consume active slots; exclude this user's own dispatch.
    if active_count(chat_id, exclude_user_id=user_id) >= MAX_ACTIVE_CAPTCHAS_PER_CHAT:
        set_state(
            chat_id,
            user_id,
            "queued",
            next_action_at=time.time() + 5,
        )
        return

    display_name = "Пользователь"
    try:
        member = await telegram_call(
            lambda: bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        )
        display_name = escape(
            " ".join(
                part for part in (member.user.first_name, member.user.last_name) if part
            )
        ) or display_name
    except TelegramAPIError:
        logging.exception(
            "Could not fetch display name for captcha user %s in chat %s",
            user_id,
            chat_id,
        )

    try:
        dice = await telegram_call(lambda: bot.send_dice(chat_id, emoji="🎲"))
        schedule_message_deletion(chat_id, dice.message_id, MESSAGE_DELETE_TIMEOUT)

        token = new_challenge_token()
        if not start_sending(
            chat_id,
            user_id,
            attempt,
            dice.dice.value,
            token,
        ):
            logging.warning(
                "Challenge state changed before send chat=%s user=%s", chat_id, user_id
            )
            return

        keyboard = get_dice_keyboard(challenge_token=token, user_id=user_id)
        message = await telegram_call(
            lambda: bot.send_message(
                chat_id,
                f'{escape(get_greeting(chat_id))}\n<a href="tg://user?id={user_id}">{display_name}</a>, {DICE_SEND_MSG}',
                parse_mode="HTML",
                reply_markup=keyboard.as_markup(),
            )
        )
        schedule_message_deletion(chat_id, message.message_id, MESSAGE_DELETE_TIMEOUT)

        if not activate_challenge(
            chat_id,
            user_id,
            message.message_id,
            attempt,
            CAPTCHA_TIMEOUT,
        ):
            logging.error(
                "Could not activate sent challenge chat=%s user=%s message=%s",
                chat_id,
                user_id,
                message.message_id,
            )
    except Exception:
        logging.exception(
            "Failed to send captcha for user %s in chat %s; it will be retried",
            user_id,
            chat_id,
        )
        # Keep the row and retry it. Never lose a restricted newcomer because
        # a Telegram API call or the process failed halfway through sending.
        retry_state(chat_id, user_id, "queued", API_RETRY_DELAY)


async def _process_expired(bot: Bot, challenge: dict) -> None:
    chat_id = challenge["chat_id"]
    user_id = challenge["user_id"]
    attempt = challenge["attempt"]
    message_id = challenge["message_id"]

    # Persist the transition before external API calls, so a crash is recoverable.
    if attempt >= MAX_ATTEMPTS:
        set_state(chat_id, user_id, "kick_pending")
    else:
        # Timeout on the first challenge immediately starts the second one.
        mark_waiting(chat_id, user_id, attempt + 1, 0)
    await _remove_keyboard(bot, chat_id, message_id)


async def _process_waiting(bot: Bot, challenge: dict) -> None:
    chat_id = challenge["chat_id"]
    user_id = challenge["user_id"]
    attempt = challenge["attempt"]

    try:
        try:
            member = await telegram_call(
                lambda: bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            )
        except Exception:
            # The join update already told us this was an ordinary member.
            # If only getChatMember is failing transiently, do not abandon the
            # join: try restricting directly, then keep retrying on any failure.
            # Telegram itself will reject restrictions that are not applicable.
            if challenge.get("observed_join_status") not in {"member", "restricted"}:
                raise
            logging.exception(
                "Could not re-check newcomer status; attempting fail-closed restriction "
                "chat=%s user=%s",
                chat_id,
                user_id,
            )
            await apply_captcha_restriction(
                bot,
                chat_id,
                user_id,
                restrict=True,
            )
            await _send_challenge(bot, chat_id, user_id, attempt)
            return

        status = _status(member)
        if status in {"left", "kicked"}:
            delete(chat_id, user_id)
            return
        if status in {"administrator", "creator"}:
            # Do not modify a user who was promoted while queued for a CAPTCHA.
            logging.warning(
                "Skipping CAPTCHA for administrator chat=%s user=%s", chat_id, user_id
            )
            delete(chat_id, user_id)
            return

        await _ensure_bot_can_restrict(bot, chat_id)
        await apply_captcha_restriction(
            bot,
            chat_id,
            user_id,
            restrict=True,
        )
        await _send_challenge(bot, chat_id, user_id, attempt)
    except Exception:
        logging.exception(
            "Failed to continue captcha for user %s in chat %s; retrying",
            user_id,
            chat_id,
        )
        retry_state(chat_id, user_id, "queued", API_RETRY_DELAY)


async def _process_passed(bot: Bot, challenge: dict) -> None:
    chat_id = challenge["chat_id"]
    user_id = challenge["user_id"]
    message_id = challenge["message_id"]
    try:
        member = await telegram_call(
            lambda: bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        )
        status = _status(member)
        if status in {"left", "kicked"}:
            delete(chat_id, user_id)
            return
        if status in {"administrator", "creator"}:
            # Do not touch rights of an administrator promoted during the check.
            delete(chat_id, user_id)
            return

        await apply_captcha_restriction(
            bot,
            chat_id,
            user_id,
            restrict=False,
        )
        await _remove_keyboard(bot, chat_id, message_id)
        delete(chat_id, user_id)
    except Exception:
        logging.exception(
            "Failed to apply verified-member permissions for user %s in chat %s; retrying",
            user_id,
            chat_id,
        )
        retry_state(chat_id, user_id, "passed_pending", API_RETRY_DELAY)


async def _process_kick(bot: Bot, challenge: dict) -> None:
    chat_id = challenge["chat_id"]
    user_id = challenge["user_id"]

    try:
        if not challenge.get("notice_sent"):
            try:
                notice = await telegram_call(
                    lambda: bot.send_message(
                        chat_id,
                        KICK_NOTICE_MSG.format(user_id=user_id),
                        parse_mode="HTML",
                    )
                )
                schedule_message_deletion(
                    chat_id, notice.message_id, MESSAGE_DELETE_TIMEOUT
                )
                mark_notice_sent(chat_id, user_id)
            except TelegramAPIError:
                # Announcements are non-critical; the moderation action proceeds.
                logging.exception(
                    "Could not announce CAPTCHA removal chat=%s user=%s", chat_id, user_id
                )
                mark_notice_sent(chat_id, user_id)

        member = await telegram_call(
            lambda: bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        )
        status = _status(member)
        if status == "left":
            delete(chat_id, user_id)
            return
        if status in {"administrator", "creator"}:
            logging.error(
                "Refusing automatic removal of administrator chat=%s user=%s",
                chat_id,
                user_id,
            )
            delete(chat_id, user_id)
            return
        if status == "kicked":
            if not challenge.get("kick_ban_issued"):
                # A moderator had already banned this user. Don't undo that ban.
                logging.warning(
                    "User already banned outside this CAPTCHA; preserving ban "
                    "chat=%s user=%s", chat_id, user_id
                )
                delete(chat_id, user_id)
                return
            if FAIL_BAN_SECONDS:
                # Our temporary ban is already in place; it expires by itself.
                delete(chat_id, user_id)
                return
            # Recovery after our ban succeeded but unban did not complete.
            await telegram_call(
                lambda: bot.unban_chat_member(
                    chat_id=chat_id, user_id=user_id, only_if_banned=True
                )
            )
        else:
            # Only mark the ban as issued after Telegram confirms success. If
            # the response is ambiguous or the call fails, recovery must not
            # unban a possible moderator-issued ban. With FAIL_BAN_SECONDS the
            # ban is temporary and stays; with 0 it is a plain kick (ban + unban).
            until_date = int(time.time()) + FAIL_BAN_SECONDS if FAIL_BAN_SECONDS else None
            await telegram_call(
                lambda: bot.ban_chat_member(
                    chat_id=chat_id, user_id=user_id, until_date=until_date
                )
            )
            mark_kick_ban_issued(chat_id, user_id)
            if not FAIL_BAN_SECONDS:
                await telegram_call(
                    lambda: bot.unban_chat_member(
                        chat_id=chat_id, user_id=user_id, only_if_banned=True
                    )
                )
        delete(chat_id, user_id)
    except Exception:
        logging.exception(
            "Failed to remove user %s from chat %s; removal will be retried",
            user_id,
            chat_id,
        )
        retry_state(chat_id, user_id, "kick_pending", API_RETRY_DELAY)


async def validate_configured_chat_rights(bot: Bot) -> None:
    """Log setup errors for configured chats without preventing bot startup."""
    for chat_id in sorted(allowed_chat_ids()):
        try:
            chat = await telegram_call(lambda chat_id=chat_id: bot.get_chat(chat_id))
            if getattr(chat.type, "value", chat.type) != "supergroup":
                logging.error(
                    "Configured chat %s is not a supergroup (type=%s); CAPTCHA will be ignored",
                    chat_id,
                    getattr(chat.type, "value", chat.type),
                )
                continue
            member = await telegram_call(
                lambda chat_id=chat_id: bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
            )
            if _status(member) != "administrator":
                logging.error(
                    "Bot is not an administrator in configured chat %s; CAPTCHA cannot operate",
                    chat_id,
                )
                continue
            missing = []
            if not bool(getattr(member, "can_restrict_members", False)):
                missing.append("Restrict members")
            if not bool(getattr(member, "can_delete_messages", False)):
                missing.append("Delete messages")
            if missing:
                logging.error(
                    "Bot is missing required admin rights in chat %s: %s",
                    chat_id,
                    ", ".join(missing),
                )
            else:
                logging.info("Required bot rights verified in chat %s", chat_id)
        except Exception:
            logging.exception(
                "Could not validate bot permissions for configured chat %s; "
                "newcomer checks will retry when join events arrive",
                chat_id,
            )


@router.my_chat_member()
async def bot_membership_handler(event: ChatMemberUpdated, bot: Bot):
    """Persist groups that add the bot; stop processing after its removal."""
    chat_id = event.chat.id
    old_status = _status(event.old_chat_member)
    new_status = _status(event.new_chat_member)

    if event.chat.type != "supergroup":
        logging.info("Ignoring bot membership change outside a supergroup chat=%s", chat_id)
        return

    if new_status in {"member", "administrator"}:
        if add_allowed_chat_id(chat_id):
            logging.info("Automatically allow-listed supergroup %s", chat_id)
        if new_status != "administrator":
            logging.warning(
                "Bot is in supergroup %s but is not an administrator; CAPTCHA requires admin rights",
                chat_id,
            )
        else:
            try:
                await _ensure_bot_can_restrict(bot, chat_id)
            except Exception:
                logging.exception("Bot lacks required moderation rights in supergroup %s", chat_id)
        return

    if old_status in {"member", "administrator"} and remove_allowed_chat_id(chat_id):
        logging.info("Removed supergroup %s from the automatic allow-list", chat_id)


async def captcha_state_worker(bot: Bot) -> None:
    """Retry due work and recover in-flight states after an interrupted process."""
    while True:
        try:
            recover_stale_states()
            allow_list = allowed_chat_ids()
            for challenge in claim_due_waiting(allow_list):
                await _process_waiting(bot, challenge)
            for challenge in due_expired(allow_list):
                await _process_expired(bot, challenge)
            for challenge in claim_due_pending(allow_list):
                if challenge["state"] == "passed_processing":
                    await _process_passed(bot, challenge)
                elif challenge["state"] == "kick_processing":
                    await _process_kick(bot, challenge)
        except Exception:
            logging.exception("CAPTCHA state worker failed")
        await asyncio.sleep(5)


@router.chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
async def member_left_handler(event: ChatMemberUpdated, bot: Bot):
    user_id = event.old_chat_member.user.id
    chat_id = event.chat.id
    challenge = get(chat_id, user_id)
    if challenge and challenge["state"] in {"kick_pending", "kick_processing"}:
        # Keep the durable kick job; a member-left event may actually be the ban
        # half of our ban/unban sequence after a previous process crash.
        return
    if challenge and challenge["state"] not in {"passed_pending", "passed_processing"}:
        # Leaving mid-CAPTCHA must not reset the attempts on rejoin.
        remember_left_attempt(chat_id, user_id, challenge["attempt"], FAIL_BAN_SECONDS)
    delete(chat_id, user_id)


@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def new_member_handler(event: ChatMemberUpdated, bot: Bot):
    user = event.new_chat_member.user
    user_id = user.id
    chat_id = event.chat.id

    if not _chat_is_allowed(chat_id):
        return
    if event.chat.type != "supergroup":
        logging.error(
            "CAPTCHA only supports Telegram supergroups; ignoring chat=%s type=%s",
            chat_id,
            event.chat.type,
        )
        return
    if user.is_bot:
        logging.info("Ignoring bot account joining chat=%s user=%s", chat_id, user_id)
        return
    if get(chat_id, user_id) is not None:
        # Duplicate/replayed join updates must not reset a live challenge.
        return

    # The update itself is the first source of truth. Do not make CAPTCHA
    # enrollment depend on an additional getChatMember request that can fail
    # transiently and silently let a newcomer bypass the check.
    observed_join_status = _status(event.new_chat_member)
    if observed_join_status in {"administrator", "creator"}:
        logging.info(
            "Skipping CAPTCHA for joining administrator chat=%s user=%s status=%s",
            chat_id,
            user_id,
            observed_join_status,
        )
        return

    delay = 0 if await allow_new_join(chat_id) else BAN_TIMEOUT
    if not queue_challenge(
        chat_id,
        user_id,
        attempt=pop_left_attempt(chat_id, user_id) or 1,
        delay_seconds=API_RETRY_DELAY,
        observed_join_status=observed_join_status,
    ):
        return

    # Lock immediately. If Telegram rejects this due to missing rights/network
    # issues, the in-memory queue retries it while the process is running. The
    # state is already queued, so no transient API error can lose the join event.
    try:
        await _ensure_bot_can_restrict(bot, chat_id)
        await apply_captcha_restriction(
            bot,
            chat_id,
            user_id,
            restrict=True,
        )
    except Exception:
        logging.exception(
            "Could not restrict newcomer chat=%s user=%s; keeping in-memory retry",
            chat_id,
            user_id,
        )
        retry_state(chat_id, user_id, "queued", API_RETRY_DELAY)
        return

    # Release the queued state to the worker only after restrictions succeed.
    set_state(
        chat_id,
        user_id,
        "queued",
        next_action_at=time.time() + max(0, delay),
    )


def _settings_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить приветствие", callback_data="greet:edit")
    builder.button(text="🔄 Сбросить приветствие", callback_data="greet:clear")
    builder.button(text="👁 Показать текущее", callback_data="greet:show")
    builder.adjust(1)
    return builder.as_markup()


def _settings_text(chat_id: int) -> str:
    greeting = escape(get_greeting(chat_id))
    return (
        "<b>Настройки капчи</b>\n\n"
        "Текст приветствия, который будет добавляться к каждой капче:\n"
        f"<blockquote>{greeting}</blockquote>\n\n"
        "Изменения сохраняются в JSON-файле настроек. Данные участников не сохраняются."
    )


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await telegram_call(
            lambda: bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        )
    except TelegramAPIError:
        logging.exception("Could not verify admin status chat=%s user=%s", chat_id, user_id)
        return False
    return _status(member) in {"administrator", "creator"}


@router.message(Command("captcha_settings"))
async def captcha_settings_command(message: types.Message, bot: Bot):
    chat_id = message.chat.id
    user = message.from_user
    if user is None or not _chat_is_allowed(chat_id):
        return
    if message.chat.type != "supergroup":
        await message.answer("Настройки доступны только в супергруппе.")
        return
    if not await _is_chat_admin(bot, chat_id, user.id):
        await message.reply("Настройки капчи доступны только администраторам группы.")
        return

    menu = await message.answer(
        _settings_text(chat_id),
        parse_mode="HTML",
        reply_markup=_settings_keyboard(),
    )
    schedule_message_deletion(chat_id, menu.message_id, MESSAGE_DELETE_TIMEOUT)


@router.callback_query(F.data.startswith("greet:"))
async def greeting_settings_callback(callback: types.CallbackQuery, bot: Bot):
    if callback.message is None:
        await callback.answer("Не удалось определить группу.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    if not _chat_is_allowed(chat_id):
        await callback.answer("Бот не обслуживает эту группу.", show_alert=True)
        return
    if callback.message.chat.type != "supergroup":
        await callback.answer("Настройки доступны только в супергруппе.", show_alert=True)
        return
    if not await _is_chat_admin(bot, chat_id, user_id):
        await callback.answer("Настройки доступны только администраторам.", show_alert=True)
        return

    action = (callback.data or "").split(":", 1)[1]
    if action == "edit":
        prompt = await bot.send_message(
            chat_id,
            "Ответьте именно на это сообщение новым текстом приветствия (до 1000 символов).\n"
            f"Ожидание действует {GREETING_EDIT_TIMEOUT_SECONDS // 60} минут. "
            "Для отмены: /cancel_greeting",
        )
        schedule_message_deletion(chat_id, prompt.message_id, MESSAGE_DELETE_TIMEOUT)
        _pending_greeting_edits[(chat_id, user_id)] = {
            "menu_message_id": callback.message.message_id,
            "prompt_message_id": prompt.message_id,
            "expires_at": time.time() + GREETING_EDIT_TIMEOUT_SECONDS,
        }
        await callback.answer("Ответьте на сообщение бота новым приветствием.")
        return

    if action == "clear":
        _pending_greeting_edits.pop((chat_id, user_id), None)
        try:
            clear_greeting(chat_id)
        except OSError:
            logging.exception("Could not reset greeting chat=%s", chat_id)
            await callback.answer("Не удалось сохранить настройки. Проверьте права на файл.", show_alert=True)
            return
        await callback.answer("Установлен текст приветствия по умолчанию.")
    elif action == "show":
        await callback.answer("Текущее приветствие показано в меню.")
    else:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    try:
        await callback.message.edit_text(
            _settings_text(chat_id),
            parse_mode="HTML",
            reply_markup=_settings_keyboard(),
        )
    except TelegramAPIError:
        logging.info("Could not refresh greeting settings menu chat=%s", chat_id)


@router.message(Command("cancel_greeting"))
async def cancel_greeting_edit(message: types.Message):
    user = message.from_user
    if user is None:
        return
    key = (message.chat.id, user.id)
    if key in _pending_greeting_edits:
        _pending_greeting_edits.pop(key, None)
        sent = await message.answer("Изменение приветствия отменено.")
        schedule_message_deletion(message.chat.id, sent.message_id, MESSAGE_DELETE_TIMEOUT)


@router.message(F.text & ~F.text.startswith("/"))
async def receive_greeting_text(message: types.Message, bot: Bot):
    user = message.from_user
    if user is None:
        return
    chat_id = message.chat.id
    key = (chat_id, user.id)
    if key not in _pending_greeting_edits:
        return
    pending = _pending_greeting_edits[key]
    if time.time() >= pending["expires_at"]:
        _pending_greeting_edits.pop(key, None)
        if (
            message.reply_to_message is not None
            and message.reply_to_message.message_id == pending["prompt_message_id"]
        ):
            expired_notice = await message.reply(
                "Время изменения приветствия истекло. Откройте /captcha_settings и начните заново."
            )
            schedule_message_deletion(chat_id, expired_notice.message_id, MESSAGE_DELETE_TIMEOUT)
        return

    # Do not consume ordinary administrator messages as configuration. Only a
    # direct reply to the active bot prompt is accepted.
    if (
        message.reply_to_message is None
        or message.reply_to_message.message_id != pending["prompt_message_id"]
    ):
        return

    if not _chat_is_allowed(chat_id) or not await _is_chat_admin(bot, chat_id, user.id):
        _pending_greeting_edits.pop(key, None)
        return

    greeting = (message.text or "").strip()
    if not greeting:
        await message.reply("Текст не должен быть пустым. Отправьте приветствие ещё раз.")
        return
    if len(greeting) > 1000:
        await message.reply("Слишком длинный текст. Максимум — 1000 символов.")
        return

    try:
        set_greeting(chat_id, greeting)
    except (OSError, ValueError):
        logging.exception("Could not save greeting chat=%s", chat_id)
        await message.reply("Не удалось сохранить приветствие. Проверьте права на файл настроек.")
        return

    _pending_greeting_edits.pop(key, None)
    # Refresh the menu if it still exists. It remains scheduled for deletion at
    # the original five-minute deadline.
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=pending["menu_message_id"],
            text=_settings_text(chat_id),
            parse_mode="HTML",
            reply_markup=_settings_keyboard(),
        )
    except TelegramAPIError:
        logging.info("Could not refresh greeting menu after save chat=%s", chat_id)

    # The admin's message contains configuration text; remove it to avoid clutter.
    try:
        await telegram_call(
            lambda: bot.delete_message(chat_id=chat_id, message_id=message.message_id)
        )
    except TelegramAPIError:
        logging.info("Could not delete administrator greeting input chat=%s", chat_id)

    confirmation = await bot.send_message(chat_id, "Приветствие капчи сохранено.")
    schedule_message_deletion(chat_id, confirmation.message_id, MESSAGE_DELETE_TIMEOUT)


@router.callback_query(
    F.data.startswith("correct_answer_from_") | F.data.startswith("wrong_answer_from_")
)
async def legacy_captcha_callback_handler(callback: types.CallbackQuery):
    """Old buttons are invalidated on migration; give a clear response if clicked."""
    await callback.answer(
        "Эта капча устарела. Дождитесь нового сообщения от бота.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("c:"))
async def captcha_answer_handler(callback: types.CallbackQuery, bot: Bot):
    parsed = parse_captcha_callback(callback.data)
    if parsed is None or callback.message is None:
        await callback.answer("Некорректная капча.", show_alert=True)
        return

    user_id, challenge_token, selected_number = parsed
    if callback.from_user.id != user_id:
        await callback.answer(WRONG_USER_MSG, show_alert=True)
        return

    chat_id = callback.message.chat.id
    if not _chat_is_allowed(chat_id):
        await callback.answer("Бот не обслуживает эту группу.", show_alert=True)
        return

    resolved = resolve_active_answer(
        chat_id,
        user_id,
        callback.message.message_id,
        challenge_token,
        selected_number,
        MAX_ATTEMPTS,
        BAN_TIMEOUT,
    )
    if resolved is None:
        try:
            await callback.answer(
                "Эта капча устарела или уже обработана. Дождитесь новой.",
                show_alert=True,
            )
        except TelegramAPIError:
            pass
        return

    state = resolved["state"]
    if state == "passed_pending":
        try:
            await callback.answer(CORRECT_ANSWER_MSG)
        except TelegramAPIError:
            logging.info("Could not answer callback for passing user=%s", user_id)
        pending = claim_pending_for(chat_id, user_id, "pass")
        if pending:
            await _process_passed(bot, pending)
        return

    if state == "kick_pending":
        try:
            await callback.answer(SECOND_WRONG_MSG, show_alert=True)
        except TelegramAPIError:
            pass
        pending = claim_pending_for(chat_id, user_id, "kick")
        if pending:
            await _process_kick(bot, pending)
        return

    try:
        await callback.answer(WRONG_ANSWER_MSG, show_alert=True)
    except TelegramAPIError:
        logging.info("Could not answer wrong CAPTCHA callback user=%s", user_id)
    await _remove_keyboard(bot, chat_id, callback.message.message_id)


def initialize_state() -> None:
    """No persistent CAPTCHA state is loaded; each process starts empty."""
    return
