from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from dennis_bot.monitors.models import ActivityType, MonitorDefinition, NormalizedRecord


class _TextExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: set[str] = set()
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.add(urljoin(self.base_url, href))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = normalize_text(data)
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        else:
            self.text_parts.append(value)


def normalize_official_site_response(
    monitor: MonitorDefinition,
    provider_record: dict[str, Any],
) -> NormalizedRecord:
    body = str(provider_record.get("body") or provider_record.get("html") or provider_record)
    extractor = _TextExtractor(monitor.url)
    extractor.feed(body)
    text = normalize_text(" ".join(extractor.text_parts) or html.unescape(body))
    links = sorted(link for link in extractor.links if link.startswith(("http://", "https://")))
    title = normalize_text(" ".join(extractor.title_parts)) or monitor.name
    content = "\n".join(
        part
        for part in [
            f"title: {title}",
            f"url: {monitor.url}",
            f"text: {text}",
            "links: " + " ".join(links) if links else "",
        ]
        if part
    )
    return NormalizedRecord(
        monitor_name=monitor.name,
        source_type="official_site",
        url=monitor.url,
        title=title,
        summary=excerpt(text),
        content=text,
        content_hash=stable_hash(content),
        permalink=monitor.url,
        raw_provider_record=provider_record,
    )


def normalize_instagram_response(
    monitor: MonitorDefinition,
    provider_records: list[dict[str, Any]],
) -> list[NormalizedRecord]:
    return [
        _normalize_instagram_record(monitor, record)
        for record in provider_records
        if not _is_provider_error_record(record)
    ]


def _normalize_instagram_record(
    monitor: MonitorDefinition,
    record: dict[str, Any],
) -> NormalizedRecord:
    activity_type = _activity_type(record)
    external_id = _first_string(
        record,
        "id",
        "pk",
        "post_id",
        "content_id",
        "shortcode",
        "code",
        "comment_id",
        "profile_id",
        "username",
        "handle",
        "url",
    )
    permalink = _first_string(record, "permalink", "url", "post_url", "reel_url") or monitor.url
    caption = normalize_text(
        _first_string(record, "caption", "description", "text", "biography", "bio", "comment_text")
        or ""
    )
    actor = _first_string(
        record,
        "owner_username",
        "username",
        "handle",
        "profile_username",
        "user_posted",
        "account",
        "profile_name",
    )
    media_type = _media_type(record, activity_type)
    published_at = parse_datetime(
        _first_string(
            record,
            "date_posted",
            "taken_at",
            "published_at",
            "created_at",
            "date",
            "timestamp",
        )
    )
    engagement = _engagement_snapshot(record)
    profile_text = normalize_text(
        " ".join(
            value
            for value in [
                _first_string(record, "full_name", "display_name", "name") or "",
                _first_string(record, "biography", "bio") or "",
                _first_string(record, "external_url", "link") or "",
                str(record.get("is_verified", "")),
            ]
            if value
        )
    )
    content_parts = {
        "activity_type": activity_type,
        "external_id": external_id or "",
        "permalink": permalink,
        "actor": actor or "",
        "media_type": media_type,
        "caption": caption,
        "profile": profile_text,
    }
    content = "\n".join(f"{key}: {value}" for key, value in sorted(content_parts.items()))
    notify = activity_type != "metric_snapshot"
    title = _title_for_instagram(activity_type, media_type, actor)
    return NormalizedRecord(
        monitor_name=monitor.name,
        source_type="instagram",
        url=monitor.url,
        title=title,
        summary=excerpt(caption or profile_text or title),
        content=caption or profile_text or title,
        content_hash=stable_hash(content),
        external_id=external_id,
        permalink=permalink,
        published_at=published_at,
        activity_type=activity_type,
        media_type=media_type,
        actor_handle=actor,
        caption=caption or None,
        engagement_snapshot=engagement,
        raw_provider_record=record,
        notify=notify,
    )


def normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def excerpt(value: str, limit: int = 240) -> str:
    value = normalize_text(value)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.isdigit():
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _activity_type(record: dict[str, Any]) -> ActivityType:
    explicit = str(
        record.get("activity_type") or record.get("type") or record.get("content_type") or ""
    ).lower()
    if explicit in {
        "profile_update",
        "post",
        "reel",
        "carousel",
        "comment",
        "tagged_media",
        "mention",
        "story",
        "highlight",
        "metric_snapshot",
    }:
        return explicit  # type: ignore[return-value]
    if "comment" in explicit or "comment_text" in record:
        return "comment"
    media_product = str(
        record.get("media_product_type") or record.get("product_type") or ""
    ).lower()
    media_type = str(record.get("media_type") or record.get("__typename") or "").lower()
    if "reel" in media_product or "reel" in media_type:
        return "reel"
    if "carousel" in media_type or record.get("children") or record.get("sidecar"):
        return "carousel"
    if record.keys() & {"followers", "follower_count", "following_count"} and not record.keys() & {
        "caption",
        "description",
        "text",
        "biography",
        "bio",
    }:
        return "metric_snapshot"
    if record.keys() & {"biography", "bio", "full_name", "external_url"}:
        return "profile_update"
    if record.keys() & {"caption", "description", "shortcode", "taken_at"}:
        return "post"
    return "unknown"


def _media_type(record: dict[str, Any], activity_type: ActivityType) -> str:
    explicit = str(
        record.get("media_type") or record.get("type") or record.get("content_type") or ""
    ).lower()
    if "carousel" in explicit:
        return "carousel"
    if "reel" in explicit:
        return "reel"
    if activity_type in {"post", "reel", "carousel", "story"}:
        return activity_type
    return "unknown"


def _engagement_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "likes",
        "like_count",
        "comments",
        "comment_count",
        "num_comments",
        "views",
        "view_count",
        "plays",
        "play_count",
        "followers",
        "follower_count",
    }
    return {key: record[key] for key in keys if key in record}


def _first_string(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return str(value)
    return None


def _is_provider_error_record(record: dict[str, Any]) -> bool:
    if not (record.get("error") or record.get("error_code")):
        return False
    content_keys = {
        "id",
        "pk",
        "post_id",
        "content_id",
        "shortcode",
        "code",
        "caption",
        "description",
        "biography",
        "bio",
        "post_url",
        "permalink",
        "url",
    }
    return not any(record.get(key) for key in content_keys)


def _title_for_instagram(activity_type: ActivityType, media_type: str, actor: str | None) -> str:
    actor_part = f" by @{actor.lstrip('@')}" if actor else ""
    if activity_type == "profile_update":
        return "Instagram profile update"
    if activity_type == "metric_snapshot":
        return "Instagram metric snapshot"
    return f"Instagram {media_type if media_type != 'unknown' else activity_type}{actor_part}"
