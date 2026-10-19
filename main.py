"""Telegram dice CAPTCHA bot for explicitly configured supergroups."""

import asyncio
import logging
import os

from dotenv import load_dotenv

# Load dotenv before importing application modules that read configuration.
if os.getenv("ENVIRONMENT", "development") != "production":
    load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramServerError

from cleanup import message_deletion_worker
from constants import allowed_chat_ids
import handlers

API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("API_TOKEN is not configured")
if not allowed_chat_ids():
    raise RuntimeError(
        "ALLOWED_CHAT_IDS is empty. Configure comma-separated Telegram supergroup IDs; "
        "the bot deliberately refuses to moderate unspecified chats."
    )

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def main():
    handlers.initialize_state()

    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()
    dp.include_routers(handlers.router)

    # Validate permissions at startup, but do not crash the service solely
    # because Telegram is temporarily unavailable. Runtime join handling retries.
    await handlers.validate_configured_chat_rights(bot)

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="captcha_settings", description="Настройки капчи (администратор)"),
                BotCommand(command="cancel_greeting", description="Отменить изменение приветствия"),
            ],
            scope=BotCommandScopeAllGroupChats(),
        )
    except TelegramAPIError:
        logging.exception("Could not register bot commands; commands can still be typed manually")

    deletion_task = asyncio.create_task(message_deletion_worker(bot))
    captcha_task = asyncio.create_task(handlers.captcha_state_worker(bot))

    try:
        while True:
            try:
                # Do not discard Telegram updates; CAPTCHA state itself is intentionally in-memory.
                await bot.delete_webhook(drop_pending_updates=False)
                break
            except (TelegramNetworkError, TelegramServerError):
                logging.exception(
                    "Telegram API unavailable while configuring polling; retrying in 10s"
                )
                await asyncio.sleep(10)

        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        deletion_task.cancel()
        captcha_task.cancel()
        await asyncio.gather(deletion_task, captcha_task, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
