"""Tests for the deliberately in-memory CAPTCHA lifecycle."""

import time
import unittest

import captcha_state as state
from captcha_state import (
    activate_challenge,
    active_count,
    claim_due_waiting,
    delete,
    due_expired,
    get,
    pop_left_attempt,
    queue_challenge,
    remember_left_attempt,
    resolve_active_answer,
    set_state,
    start_sending,
)


class InMemoryCaptchaStateTests(unittest.TestCase):
    def setUp(self):
        state._challenges.clear()
        state._left_attempts.clear()
        self.chat_id = -100111
        self.user_id = 222

    def make_active(self, expected=4, attempt=1):
        self.assertTrue(queue_challenge(self.chat_id, self.user_id, attempt=attempt))
        set_state(self.chat_id, self.user_id, "queued", next_action_at=time.time() - 1)
        claimed = claim_due_waiting({self.chat_id})
        self.assertEqual(len(claimed), 1)
        self.assertTrue(start_sending(
            self.chat_id, self.user_id, attempt, expected, "0123456789abcdef"
        ))
        self.assertTrue(activate_challenge(
            self.chat_id, self.user_id, message_id=999, attempt=attempt, timeout_seconds=120
        ))

    def test_join_status_is_kept_for_fail_closed_api_retry(self):
        self.assertTrue(
            queue_challenge(
                self.chat_id,
                self.user_id,
                observed_join_status="member",
            )
        )
        self.assertEqual(
            get(self.chat_id, self.user_id)["observed_join_status"], "member"
        )

    def test_duplicate_join_does_not_reset_existing_flow(self):
        self.assertTrue(queue_challenge(self.chat_id, self.user_id))
        self.assertFalse(queue_challenge(self.chat_id, self.user_id))
        self.assertEqual(active_count(self.chat_id), 0)
        self.assertIsNotNone(get(self.chat_id, self.user_id))

    def test_correct_answer_is_consumed_and_replay_rejected(self):
        self.make_active(expected=5)
        resolved = resolve_active_answer(
            self.chat_id, self.user_id, 999, "0123456789abcdef", 5, 2, 30
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["state"], "passed_pending")
        self.assertIsNone(resolve_active_answer(
            self.chat_id, self.user_id, 999, "0123456789abcdef", 5, 2, 30
        ))

    def test_wrong_first_answer_waits_thirty_seconds(self):
        self.make_active(expected=4)
        before = time.time()
        resolved = resolve_active_answer(
            self.chat_id, self.user_id, 999, "0123456789abcdef", 2, 2, 30
        )
        after = time.time()
        self.assertEqual(resolved["state"], "waiting")
        self.assertEqual(resolved["attempt"], 2)
        self.assertGreaterEqual(resolved["next_action_at"], before + 30)
        self.assertLessEqual(resolved["next_action_at"], after + 30.1)
        self.assertIsNone(resolved["expected_value"])
        self.assertIsNone(resolved["challenge_token"])

    def test_wrong_second_answer_schedules_removal(self):
        self.make_active(expected=4, attempt=2)
        resolved = resolve_active_answer(
            self.chat_id, self.user_id, 999, "0123456789abcdef", 2, 2, 30
        )
        self.assertEqual(resolved["state"], "kick_pending")
        self.assertLessEqual(resolved["next_action_at"], time.time())

    def test_expired_challenge_is_claimed_once(self):
        self.make_active(expected=4)
        row = state._challenges[(self.chat_id, self.user_id)]
        row["expires_at"] = time.time() - 1
        self.assertEqual(len(due_expired({self.chat_id})), 1)
        self.assertEqual(due_expired({self.chat_id}), [])
        self.assertEqual(get(self.chat_id, self.user_id)["state"], "timeout_processing")

    def test_completed_user_state_is_deleted(self):
        queue_challenge(self.chat_id, self.user_id)
        delete(self.chat_id, self.user_id)
        self.assertIsNone(get(self.chat_id, self.user_id))

    def test_rejoin_after_leaving_keeps_reached_attempt(self):
        remember_left_attempt(self.chat_id, self.user_id, 2, ttl_seconds=1800)
        self.assertEqual(pop_left_attempt(self.chat_id, self.user_id), 2)
        # Consumed once: a second rejoin without a new leave starts fresh.
        self.assertIsNone(pop_left_attempt(self.chat_id, self.user_id))

    def test_left_attempt_expires(self):
        remember_left_attempt(self.chat_id, self.user_id, 2, ttl_seconds=1800)
        key = (self.chat_id, self.user_id)
        state._left_attempts[key] = (2, time.time() - 1)
        self.assertIsNone(pop_left_attempt(self.chat_id, self.user_id))

    def test_left_attempt_not_kept_in_plain_kick_mode(self):
        remember_left_attempt(self.chat_id, self.user_id, 2, ttl_seconds=0)
        self.assertIsNone(pop_left_attempt(self.chat_id, self.user_id))


if __name__ == "__main__":
    unittest.main()
