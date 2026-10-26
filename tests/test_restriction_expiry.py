"""The CAPTCHA lock must expire on Telegram's side so restarts cannot mute forever."""

import time
import unittest

from constants import CAPTCHA_LOCK_SECONDS, CAPTCHA_TIMEOUT, BAN_TIMEOUT
from utils import apply_captcha_restriction


class _RecordingBot:
    def __init__(self):
        self.calls = []

    async def restrict_chat_member(self, **kwargs):
        self.calls.append(kwargs)
        return True


class RestrictionExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_captcha_lock_expires_after_one_attempt_window(self):
        bot = _RecordingBot()
        before = int(time.time())
        await apply_captcha_restriction(bot, -100111, 222, restrict=True)
        until_date = bot.calls[0]["until_date"]
        self.assertGreaterEqual(until_date, before + CAPTCHA_LOCK_SECONDS)
        self.assertLessEqual(until_date, int(time.time()) + CAPTCHA_LOCK_SECONDS)

    def test_lock_outlasts_answer_window_and_retry_pause(self):
        # Telegram treats restrictions shorter than 30 seconds as permanent.
        self.assertGreater(CAPTCHA_LOCK_SECONDS, CAPTCHA_TIMEOUT + BAN_TIMEOUT + 30)

    async def test_verified_permissions_have_no_expiry(self):
        bot = _RecordingBot()
        await apply_captcha_restriction(bot, -100111, 222, restrict=False)
        self.assertIsNone(bot.calls[0]["until_date"])


if __name__ == "__main__":
    unittest.main()
