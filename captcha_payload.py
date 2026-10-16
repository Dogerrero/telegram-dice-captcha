"""Opaque CAPTCHA callback payload helpers (independent of aiogram)."""

import re
import secrets
from typing import Optional

from constants import CAPTCHA_ANSWER_PREFIX

_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")


def new_challenge_token() -> str:
    return secrets.token_hex(8)


def build_callback_data(user_id: int, challenge_token: str, selected_number: int) -> str:
    if user_id <= 0 or not _TOKEN_RE.fullmatch(challenge_token):
        raise ValueError("Invalid CAPTCHA user ID or challenge token")
    if selected_number not in range(1, 7):
        raise ValueError("Selected number must be in 1..6")
    payload = f"{CAPTCHA_ANSWER_PREFIX}{user_id}:{challenge_token}:{selected_number}"
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("Telegram callback_data exceeds the 64-byte limit")
    return payload


def parse_captcha_callback(callback_data: Optional[str]):
    """Return target user, nonce and selected number; never return a verdict."""
    if not callback_data or not callback_data.startswith(CAPTCHA_ANSWER_PREFIX):
        return None
    parts = callback_data.split(":")
    if len(parts) != 4 or parts[0] != "c":
        return None
    try:
        user_id = int(parts[1])
        answer = int(parts[3])
    except ValueError:
        return None
    token = parts[2]
    if user_id <= 0 or answer not in range(1, 7) or not _TOKEN_RE.fullmatch(token):
        return None
    return user_id, token, answer
