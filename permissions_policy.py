"""Explicit permissions for newcomers before and after CAPTCHA.

Telegram's ChatPermissions are chat-wide member permissions, not per-topic
permissions. With independent permissions enabled, every field below is set
explicitly to avoid implicit permission dependencies.
"""

PERMISSION_FIELDS = (
    "can_send_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_react_to_messages",
    "can_edit_tag",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_topics",
)

# Rights granted after the member successfully passes CAPTCHA.
# `can_send_other_messages` is Telegram's permission bundle for stickers,
# animations/GIFs, games, and inline bots.
VERIFIED_MEMBER_PERMISSIONS = {
    "can_send_messages": True,
    "can_send_audios": True,
    "can_send_documents": True,
    "can_send_photos": True,
    "can_send_videos": True,
    "can_send_video_notes": False,
    "can_send_voice_notes": False,
    "can_send_polls": True,
    "can_send_other_messages": True,
    "can_add_web_page_previews": True,
    "can_react_to_messages": True,
    "can_edit_tag": False,
    "can_change_info": False,
    "can_invite_users": False,
    "can_pin_messages": False,
    "can_manage_topics": False,
}

# No posting or group actions while CAPTCHA is pending.
CAPTCHA_LOCKED_PERMISSIONS = {field: False for field in PERMISSION_FIELDS}


def validate_permissions_policy() -> None:
    """Fail fast if a policy misses a permission supported by this project."""
    if set(VERIFIED_MEMBER_PERMISSIONS) != set(PERMISSION_FIELDS):
        raise RuntimeError("Verified-member permissions must define every permission field")
    if set(CAPTCHA_LOCKED_PERMISSIONS) != set(PERMISSION_FIELDS):
        raise RuntimeError("CAPTCHA locked permissions must define every permission field")
    if any(type(value) is not bool for value in VERIFIED_MEMBER_PERMISSIONS.values()):
        raise RuntimeError("All verified-member permission values must be booleans")
