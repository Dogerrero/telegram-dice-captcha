"""
Telegram dice CAPTCHA bot for groups.
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError, TelegramServerError
from dotenv import load_dotenv

import handlers
from cleanup import init_cleanup_db, message_deletion_worker

# Keep dotenv for local development. Production should use systemd EnvironmentFile.
if os.getenv("ENVIRONMENT", "development") != "production":
    load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("API_TOKEN is not configured")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def main():
    handlers.initialize_state()
    init_cleanup_db()

    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()
    dp.include_routers(handlers.router)

    deletion_task = asyncio.create_task(message_deletion_worker(bot))
    captcha_task = asyncio.create_task(handlers.captcha_state_worker(bot))

    try:
        while True:
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                break
            except (TelegramNetworkError, TelegramServerError):
                logging.exception(
                    "Telegram API unavailable while clearing webhook; retrying in 10s"
                )
                await asyncio.sleep(10)

        await dp.start_polling(bot)
    finally:
        deletion_task.cancel()
        captcha_task.cancel()
        await asyncio.gather(
            deletion_task,
            captcha_task,
            return_exceptions=True,
        )
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
