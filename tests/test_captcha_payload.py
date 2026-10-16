import unittest

from captcha_payload import build_callback_data, new_challenge_token, parse_captcha_callback


class CaptchaPayloadTests(unittest.TestCase):
    def test_payload_contains_selected_number_not_a_verdict(self):
        token = new_challenge_token()
        for number in range(1, 7):
            payload = build_callback_data(123456789, token, number)
            self.assertLessEqual(len(payload.encode("utf-8")), 64)
            self.assertNotIn("correct", payload.lower())
            self.assertNotIn("wrong", payload.lower())
            self.assertEqual(parse_captcha_callback(payload), (123456789, token, number))

    def test_invalid_payloads_are_rejected(self):
        for payload in (None, "correct_answer_from_123", "c:0:0123456789abcdef:1", "c:123:bad:1", "c:123:0123456789abcdef:7", "c:123:0123456789abcdef:x"):
            self.assertIsNone(parse_captcha_callback(payload))

    def test_builder_rejects_invalid_number(self):
        with self.assertRaises(ValueError):
            build_callback_data(12, "0123456789abcdef", 7)


if __name__ == "__main__":
    unittest.main()
