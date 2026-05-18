from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import aiosqlite


@dataclass(frozen=True)
class InstagramActivityRecord:
    activity_type: str
    media_type: str
    caption: str | None = None
    permalink: str | None = None
    actor_handle: str | None = None
    published_at: str | None = None
    detected_at: str | None = None
    engagement_snapshot: dict[str, Any] | None = None


class InstagramActivityRepositoryProtocol(Protocol):
    async def list_latest(self, *, limit: int = 5) -> list[InstagramActivityRecord]: ...


class SQLiteInstagramActivityRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def list_latest(self, *, limit: int = 5) -> list[InstagramActivityRecord]:
        cursor = await self.connection.execute(
            """
            SELECT
                activity_type,
                media_type,
                caption,
                permalink,
                actor_handle,
                published_at,
                detected_at,
                engagement_snapshot
            FROM social_activity_items
            WHERE platform = 'instagram'
            ORDER BY COALESCE(published_at, detected_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [_record_from_row(row) for row in rows]


class InstagramActivityTool:
    name = "instagram_activity_cache"
    description = (
        "Retrieve cached public Instagram activity for Dennis Toh from the local "
        "monitor cache. This does not live-fetch Instagram."
    )

    def __init__(self, repository: InstagramActivityRepositoryProtocol, *, default_limit: int = 5) -> None:
        self.repository = repository
        self.default_limit = default_limit

    async def retrieve(self, *, limit: int | None = None) -> str:
        records = await self.repository.list_latest(limit=limit or self.default_limit)
        if not records:
            return (
                "Instagram activity cache: no cached public Instagram activity is available yet. "
                "Ask an admin to run /watch run dennis_instagram to refresh the cache."
            )
        lines = [
            "Instagram activity cache:",
            "Source: local social_activity_items cache populated by the Dennis Instagram monitor.",
            "Freshness: cached snapshot; not a live Instagram fetch.",
        ]
        for index, record in enumerate(records, start=1):
            bits = [
                f"{index}. {record.activity_type or 'activity'}",
                f"media={record.media_type or 'unknown'}",
            ]
            if record.published_at:
                bits.append(f"published={record.published_at}")
            elif record.detected_at:
                bits.append(f"detected={record.detected_at}")
            if record.permalink:
                bits.append(f"url={record.permalink}")
            lines.append(" | ".join(bits))
            if record.caption:
                lines.append(f"   caption: {_truncate(record.caption, 320)}")
            if record.engagement_snapshot:
                lines.append(f"   engagement: {json.dumps(record.engagement_snapshot, sort_keys=True)}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ToolDecision:
    use_instagram_activity: bool = False
    reason: str = ""


class RuntimeToolPlanner:
    """Cheap router for runtime cache tools.

    This deliberately avoids a model call. The main response model will still
    decide how to use injected context after this router selects a cache.
    """

    async def decide(self, *, user_text: str) -> ToolDecision:
        text = " ".join(user_text.lower().split())
        if not text:
            return ToolDecision(reason="empty message")
        if _mentions_instagram(text) and _mentions_activity(text):
            return ToolDecision(
                use_instagram_activity=True,
                reason="instagram activity keyword match",
            )
        if _mentions_recent_post(text):
            return ToolDecision(
                use_instagram_activity=True,
                reason="recent post keyword match",
            )
        return ToolDecision(reason="no instagram activity keywords")


def _mentions_instagram(text: str) -> bool:
    return bool(re.search(r"\b(instagram|insta|ig)\b", text))


def _mentions_activity(text: str) -> bool:
    activity_terms = (
        "activity",
        "post",
        "posts",
        "posted",
        "posting",
        "reel",
        "reels",
        "caption",
        "captions",
        "social",
        "update",
        "updates",
        "latest",
        "recent",
        "new",
        "current",
        "story",
        "stories",
    )
    return any(term in text for term in activity_terms)


def _mentions_recent_post(text: str) -> bool:
    recent_terms = ("latest", "recent", "new", "last", "current", "just")
    post_terms = ("post", "posts", "posted", "posting", "reel", "reels", "caption", "captions")
    direct_phrases = (
        "what did you post",
        "what have you posted",
        "what you posted",
        "you just posted",
        "latest activity",
        "recent activity",
        "social update",
        "social updates",
    )
    return any(phrase in text for phrase in direct_phrases) or (
        any(term in text for term in recent_terms)
        and any(term in text for term in post_terms)
    )


def _record_from_row(row: Any) -> InstagramActivityRecord:
    engagement = row["engagement_snapshot"]
    parsed_engagement: dict[str, Any] | None = None
    if engagement:
        try:
            loaded = json.loads(str(engagement))
            parsed_engagement = loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            parsed_engagement = None
    return InstagramActivityRecord(
        activity_type=str(row["activity_type"] or "unknown"),
        media_type=str(row["media_type"] or "unknown"),
        caption=row["caption"],
        permalink=row["permalink"],
        actor_handle=row["actor_handle"],
        published_at=_format_datetime(row["published_at"]),
        detected_at=_format_datetime(row["detected_at"]),
        engagement_snapshot=parsed_engagement,
    )


def _format_datetime(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _truncate(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."
