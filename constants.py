import os

CAPTCHA_ANSWER_PREFIX = "c:"
BAN_TIMEOUT = int(os.getenv("BAN_TIMEOUT_SECONDS", "30"))
CAPTCHA_TIMEOUT = int(os.getenv("CAPTCHA_TIMEOUT_SECONDS", "120"))
MESSAGE_DELETE_TIMEOUT = int(os.getenv("MESSAGE_DELETE_TIMEOUT_SECONDS", "300"))
API_RETRY_DELAY = int(os.getenv("API_RETRY_DELAY_SECONDS", "30"))
MAX_ATTEMPTS = int(os.getenv("MAX_CAPTCHA_ATTEMPTS", "2"))
MAX_ACTIVE_CAPTCHAS_PER_CHAT = int(os.getenv("MAX_ACTIVE_CAPTCHAS_PER_CHAT", "50"))

# The CAPTCHA lock expires on Telegram's side, so a restart or crash cannot
# leave a newcomer muted forever. It is renewed before every attempt and covers
# one answer window, the retry pause and a margin for slow API calls.
CAPTCHA_LOCK_MARGIN = int(os.getenv("CAPTCHA_LOCK_MARGIN_SECONDS", "300"))
CAPTCHA_LOCK_SECONDS = CAPTCHA_TIMEOUT + BAN_TIMEOUT + max(60, CAPTCHA_LOCK_MARGIN)

# Temporary ban after a failed CAPTCHA; 0 restores the plain kick (ban + unban).
# Telegram treats bans shorter than 30 seconds or longer than 366 days as
# permanent, so only an explicit 0 or a safe range is accepted.
FAIL_BAN_SECONDS = int(os.getenv("FAIL_BAN_SECONDS", "1800"))
if FAIL_BAN_SECONDS != 0 and not 60 <= FAIL_BAN_SECONDS <= 366 * 24 * 3600:
    raise RuntimeError("FAIL_BAN_SECONDS must be 0 or between 60 seconds and 366 days")

# No chats are processed unless an explicit allow-list is configured.
def allowed_chat_ids() -> set[int]:
    raw = os.getenv("ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return set()
    result = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError:
            raise RuntimeError(f"Invalid chat ID in ALLOWED_CHAT_IDS: {item!r}")
    return result


# Texts
DICE_SEND_MSG = "Какое число на верхней грани кубика?"
CORRECT_ANSWER_MSG = "Ответ принят. Добро пожаловать!"
WRONG_USER_MSG = "Эта капча предназначена другому пользователю."
WRONG_ANSWER_MSG = f"Неверно. Следующая попытка будет доступна через {BAN_TIMEOUT} секунд."
_FAIL_BAN_SUFFIX = f" на {-(-FAIL_BAN_SECONDS // 60)} мин." if FAIL_BAN_SECONDS else "."
KICK_NOTICE_MSG = "Пользователь <a href=\"tg://user?id={user_id}\">не прошёл капчу</a> и будет исключён" + _FAIL_BAN_SUFFIX
SECOND_WRONG_MSG = "Вторая попытка неверна. Вы будете исключены" + _FAIL_BAN_SUFFIX
