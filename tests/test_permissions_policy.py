"""Checks for the explicit permissions applied after CAPTCHA."""
import unittest

from permissions_policy import (
    CAPTCHA_LOCKED_PERMISSIONS,
    PERMISSION_FIELDS,
    VERIFIED_MEMBER_PERMISSIONS,
    validate_permissions_policy,
)


class PermissionsPolicyTests(unittest.TestCase):
    def test_every_telegram_permission_is_explicit(self):
        validate_permissions_policy()
        self.assertEqual(set(VERIFIED_MEMBER_PERMISSIONS), set(PERMISSION_FIELDS))
        self.assertEqual(set(CAPTCHA_LOCKED_PERMISSIONS), set(PERMISSION_FIELDS))

    def test_content_permissions_requested_by_owner_are_enabled(self):
        for field in (
            "can_send_messages",
            "can_send_audios",
            "can_send_documents",
            "can_send_photos",
            "can_send_videos",
            "can_send_other_messages",  # stickers and GIF/animations
            "can_send_polls",
            "can_react_to_messages",
            "can_add_web_page_previews",
        ):
            with self.subTest(field=field):
                self.assertIs(VERIFIED_MEMBER_PERMISSIONS[field], True)

    def test_moderation_and_group_management_permissions_are_disabled(self):
        for field in (
            "can_invite_users",
            "can_manage_topics",
            "can_pin_messages",
            "can_edit_tag",
            "can_change_info",
        ):
            with self.subTest(field=field):
                self.assertIs(VERIFIED_MEMBER_PERMISSIONS[field], False)

    def test_voice_and_video_notes_are_not_allowed_by_default(self):
        self.assertFalse(VERIFIED_MEMBER_PERMISSIONS["can_send_voice_notes"])
        self.assertFalse(VERIFIED_MEMBER_PERMISSIONS["can_send_video_notes"])

    def test_all_permissions_are_disabled_during_captcha(self):
        self.assertTrue(all(value is False for value in CAPTCHA_LOCKED_PERMISSIONS.values()))


if __name__ == "__main__":
    unittest.main()
