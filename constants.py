import os

CAPTCHA_ANSWER_PREFIX = "c:"
BAN_TIMEOUT = int(os.getenv("BAN_TIMEOUT_SECONDS", "30"))
CAPTCHA_TIMEOUT = int(os.getenv("CAPTCHA_TIMEOUT_SECONDS", "120"))
MESSAGE_DELETE_TIMEOUT = int(os.getenv("MESSAGE_DELETE_TIMEOUT_SECONDS", "300"))
API_RETRY_DELAY = int(os.getenv("API_RETRY_DELAY_SECONDS", "30"))
MAX_ATTEMPTS = int(os.getenv("MAX_CAPTCHA_ATTEMPTS", "2"))
MAX_ACTIVE_CAPTCHAS_PER_CHAT = int(os.getenv("MAX_ACTIVE_CAPTCHAS_PER_CHAT", "50"))

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
