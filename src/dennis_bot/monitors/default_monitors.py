from __future__ import annotations

from dennis_bot.config import Settings
from dennis_bot.monitors.models import MonitorDefinition


DENNIS_OFFICIAL_SITE_URL = "https://www.dennistohsg.com/"
DENNIS_OFFICIAL_SITE_ABOUT_URL = "https://www.dennistohsg.com/about"
DENNIS_INSTAGRAM_URL = "https://www.instagram.com/dennistohsg/"


def default_dennis_monitors(settings: Settings | None = None) -> list[MonitorDefinition]:
    target_chat_id = settings.trusted_group_chat_id if settings else None
    instagram_dataset_id = None
    instagram_monitor_type = "instagram_profile"
    if settings:
        if settings.brightdata_instagram_dataset_id_posts:
            instagram_dataset_id = settings.brightdata_instagram_dataset_id_posts
            instagram_monitor_type = "instagram_posts"
        else:
            instagram_dataset_id = (
                settings.brightdata_instagram_dataset_id_profile
                or settings.brightdata_instagram_dataset_id_reels
            )
    return [
        MonitorDefinition(
            name="dennis_official_site",
            url=DENNIS_OFFICIAL_SITE_URL,
            monitor_type="website",
            schedule="interval:hours=6",
            knowledge_update_enabled=True,
            target_chat_id=target_chat_id,
        ),
        MonitorDefinition(
            name="dennis_official_site_about",
            url=DENNIS_OFFICIAL_SITE_ABOUT_URL,
            monitor_type="website",
            schedule="interval:hours=6",
            knowledge_update_enabled=True,
            target_chat_id=target_chat_id,
        ),
        MonitorDefinition(
            name="dennis_instagram",
            url=DENNIS_INSTAGRAM_URL,
            monitor_type=instagram_monitor_type,
            schedule="interval:minutes=60",
            impact_policy="notify_group_and_dispatch_kb_when_classified_impactful",
            knowledge_update_enabled=True,
            target_chat_id=target_chat_id,
            source_handle="dennistohsg",
            provider_dataset_id=instagram_dataset_id,
        ),
    ]
