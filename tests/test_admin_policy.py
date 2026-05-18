from __future__ import annotations

from dennis_bot.admin.policy import AdminPolicy
from dennis_bot.telegram.normalize import normalize_update


def test_admin_policy_from_settings_values() -> None:
    policy = AdminPolicy(admin_user_ids=frozenset({1, 2}), trusted_group_chat_id=-100)

    assert policy.is_admin_user(1)
    assert not policy.is_admin_user(3)
    assert policy.is_trusted_group(-100)
    assert not policy.is_trusted_group(-200)


def test_full_memory_access_requires_admin_dm_or_trusted_group() -> None:
    policy = AdminPolicy(admin_user_ids=frozenset({1}), trusted_group_chat_id=-100)
    admin_dm = normalize_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "text": "/status",
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 1, "is_bot": False},
            },
        }
    )
    trusted_group = normalize_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 2,
                "text": "/status",
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 9, "is_bot": False},
            },
        }
    )
    untrusted_group = normalize_update(
        {
            "update_id": 3,
            "message": {
                "message_id": 3,
                "text": "/status",
                "chat": {"id": -200, "type": "supergroup"},
                "from": {"id": 9, "is_bot": False},
            },
        }
    )

    assert admin_dm is not None
    assert trusted_group is not None
    assert untrusted_group is not None
    assert policy.has_full_memory_access(admin_dm)
    assert policy.has_full_memory_access(trusted_group)
    assert not policy.has_full_memory_access(untrusted_group)
