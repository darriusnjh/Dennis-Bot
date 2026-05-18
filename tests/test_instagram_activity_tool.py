from __future__ import annotations

import json
from pathlib import Path

from dennis_bot.db import Database, run_migrations
from dennis_bot.tools.instagram_activity import (
    InstagramActivityTool,
    RuntimeToolPlanner,
    SQLiteInstagramActivityRepository,
)


async def test_instagram_activity_tool_reads_latest_cached_rows(tmp_path: Path) -> None:
    database = Database(tmp_path / "dennis.sqlite3")

    async with database.connect() as connection:
        await run_migrations(connection)
        await connection.execute(
            """
            INSERT INTO web_monitors (name, url, monitor_type, provider, schedule, change_detection_strategy)
            VALUES ('dennis_instagram', 'https://www.instagram.com/dennistohsg/', 'instagram_posts',
                    'brightdata', 'interval:minutes=60', 'normalized_content_hash')
            """
        )
        await connection.execute(
            """
            INSERT INTO social_activity_items (
                web_monitor_id, platform, external_id, activity_type, actor_handle, permalink,
                media_type, caption, caption_hash, published_at, engagement_snapshot
            )
            VALUES (1, 'instagram', 'post-older', 'post', 'dennistohsg',
                    'https://instagram.com/p/older', 'post', 'Older caption', 'older',
                    '2026-05-01T00:00:00Z', ?)
            """,
            (json.dumps({"likes": 5}),),
        )
        await connection.execute(
            """
            INSERT INTO social_activity_items (
                web_monitor_id, platform, external_id, activity_type, actor_handle, permalink,
                media_type, caption, caption_hash, published_at, engagement_snapshot
            )
            VALUES (1, 'instagram', 'post-newer', 'post', 'dennistohsg',
                    'https://instagram.com/p/newer', 'post', 'Newer caption', 'newer',
                    '2026-05-02T00:00:00Z', ?)
            """,
            (json.dumps({"likes": 12, "num_comments": 2}),),
        )
        await connection.commit()

        tool = InstagramActivityTool(SQLiteInstagramActivityRepository(connection), default_limit=1)
        context = await tool.retrieve()

    assert "Instagram activity cache" in context
    assert "Newer caption" in context
    assert "Older caption" not in context
    assert "not a live Instagram fetch" in context


async def test_runtime_tool_planner_uses_keywords_without_model_call() -> None:
    planner = RuntimeToolPlanner()

    decision = await planner.decide(user_text="Show me Dennis's latest post")

    assert decision.use_instagram_activity is True
    assert decision.reason == "recent post keyword match"


async def test_runtime_tool_planner_skips_unrelated_messages() -> None:
    planner = RuntimeToolPlanner()

    decision = await planner.decide(user_text="help me plan my day")

    assert decision.use_instagram_activity is False
    assert decision.reason == "no instagram activity keywords"
