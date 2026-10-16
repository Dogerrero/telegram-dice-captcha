"""Persistent administrator text settings stored as JSON, never member records."""

import json
import logging
import os
from pathlib import Path

DEFAULT_GREETING = "Добро пожаловать в группу!"


def _settings_path() -> Path:
    return Path(os.getenv("SETTINGS_FILE", "/var/lib/telegram-dice-captcha/settings.json"))


def _read() -> dict:
    path = _settings_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {"greetings": {}}
        greetings = data.get("greetings", {})
        return {"greetings": greetings if isinstance(greetings, dict) else {}}
    except FileNotFoundError:
        return {"greetings": {}}
    except (OSError, json.JSONDecodeError):
        logging.exception("Could not read greeting settings from %s", path)
        return {"greetings": {}}


def _write(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def get_greeting(chat_id: int) -> str:
    value = _read()["greetings"].get(str(chat_id), DEFAULT_GREETING)
    return value if isinstance(value, str) and value.strip() else DEFAULT_GREETING


def set_greeting(chat_id: int, greeting: str) -> None:
    clean = greeting.strip()
    if not clean:
        raise ValueError("Greeting must not be empty")
    if len(clean) > 1000:
        raise ValueError("Greeting must not exceed 1000 characters")
    data = _read()
    data["greetings"][str(chat_id)] = clean
    _write(data)


def clear_greeting(chat_id: int) -> None:
    data = _read()
    data["greetings"].pop(str(chat_id), None)
    _write(data)
