"""
telegram captcha bot for groups

bot sending dice for new user and ask dice value
"""


import logging
from aiogram import Bot, Dispatcher
import asyncio
import os
from dotenv import load_dotenv
from aiogram.exceptions import TelegramNetworkError, TelegramServerError
import handlers
from cleanup import message_deletion_worker

load_dotenv()


API_TOKEN = os.getenv("API_TOKEN")
logging.basicConfig(level=logging.INFO)


async def main():
    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()

    dp.include_routers(handlers.router)

    deletion_task = asyncio.create_task(message_deletion_worker(bot))
    try:
        while True:
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                break
            except (TelegramNetworkError, TelegramServerError):
                logging.exception("Telegram API unavailable while clearing webhook; retrying in 10s")
                await asyncio.sleep(10)
        await dp.start_polling(bot)
    finally:
        deletion_task.cancel()
        await asyncio.gather(deletion_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
