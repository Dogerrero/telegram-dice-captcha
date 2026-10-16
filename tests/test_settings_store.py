"""Settings persistence tests; only per-chat greeting text is saved."""

import json
import os
import tempfile
import unittest

from settings_store import DEFAULT_GREETING, clear_greeting, get_greeting, set_greeting


class GreetingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_path = os.environ.get("SETTINGS_FILE")
        os.environ["SETTINGS_FILE"] = os.path.join(self.tempdir.name, "settings.json")

    def tearDown(self):
        if self.previous_path is None:
            os.environ.pop("SETTINGS_FILE", None)
        else:
            os.environ["SETTINGS_FILE"] = self.previous_path
        self.tempdir.cleanup()

    def test_default_and_per_chat_greeting(self):
        self.assertEqual(get_greeting(-1001), DEFAULT_GREETING)
        set_greeting(-1001, "Привет, участники!")
        set_greeting(-1002, "Добро пожаловать во вторую группу")
        self.assertEqual(get_greeting(-1001), "Привет, участники!")
        self.assertEqual(get_greeting(-1002), "Добро пожаловать во вторую группу")

    def test_clear_returns_default(self):
        set_greeting(-1001, "Привет")
        clear_greeting(-1001)
        self.assertEqual(get_greeting(-1001), DEFAULT_GREETING)

    def test_empty_and_too_long_greetings_are_rejected(self):
        with self.assertRaises(ValueError):
            set_greeting(-1001, "   ")
        with self.assertRaises(ValueError):
            set_greeting(-1001, "x" * 1001)

    def test_settings_file_contains_only_greeting_configuration(self):
        set_greeting(-1001, "Текст приветствия")
        with open(os.environ["SETTINGS_FILE"], encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload, {"greetings": {"-1001": "Текст приветствия"}})
        self.assertNotIn("users", payload)
        self.assertNotIn("user_id", str(payload))


if __name__ == "__main__":
    unittest.main()
