from __future__ import annotations

from pathlib import Path

from dennis_bot.agents.knowledge_update.agent import KnowledgeUpdateAgent, SourceChange
from dennis_bot.db import Database, run_migrations
from dennis_bot.knowledge.service import KnowledgeService
from dennis_bot.repositories.core import ChatRepository, KnowledgeRepository


async def test_knowledge_state_versions_are_mirrored_to_database(tmp_path: Path) -> None:
    source = tmp_path / "dennis.md"
    source.write_text("# Dennis\n\n## Bio\n\nExisting bio.", encoding="utf-8")
    database = Database(tmp_path / "dennis.sqlite3")

    async with database.connect() as connection:
        await run_migrations(connection)
        repository = KnowledgeRepository(connection)
        service = KnowledgeService(
            storage_dir=tmp_path / "store",
            default_source_path=source,
            repository=repository,
        )

        initial = await service.ensure_default_state()
        updated = await service.append_version(
            state_name="dennis-toh",
            update_markdown="- New official biography line.",
            source_ref="https://www.dennistohsg.com/about",
            summary="Biography update",
        )
        rows = await repository.list_states()

    assert initial.id is not None
    assert updated.id is not None
    assert [row["version"] for row in rows] == [2, 1]
    assert rows[0]["name"] == "dennis-toh"
    assert rows[0]["source_refs"]


async def test_knowledge_update_jobs_persist_applied_and_pending_review_outcomes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dennis.md"
    source.write_text("# Dennis\n\n## Screen\n\nExisting credit.", encoding="utf-8")
    database = Database(tmp_path / "dennis.sqlite3")

    async with database.connect() as connection:
        await run_migrations(connection)
        repository = KnowledgeRepository(connection)
        service = KnowledgeService(
            storage_dir=tmp_path / "store",
            default_source_path=source,
            repository=repository,
        )
        await service.ensure_default_state()
        agent = KnowledgeUpdateAgent(
            knowledge_service=service,
            jobs_dir=tmp_path / "jobs",
            repository=repository,
            auto_apply_confidence=0.8,
        )

        applied = await agent.review_change(
            SourceChange(
                source_url="https://www.dennistohsg.com/about",
                source_type="official_site",
                title="New official role",
                diff="+ Dennis has an official film role in Hong Kong.",
            )
        )
        pending = await agent.review_change(
            SourceChange(
                source_url="https://www.instagram.com/dennistohsg/",
                source_type="instagram",
                title="Text changed",
                diff="+ Updated wording with unclear significance.",
            )
        )
        applied_job = await repository.get_update_job(int(applied.job_id))
        pending_job = await repository.get_update_job(int(pending.job_id))

    assert applied.status == "applied"
    assert applied_job["status"] == "applied"
    assert applied_job["source_url"] == "https://www.dennistohsg.com/about"
    assert applied_job["source_type"] == "official_site"
    assert applied_job["previous_version"] == 1
    assert applied_job["new_version"] == 2
    assert applied_job["impact_classification"] == "kb_impactful"
    assert pending.status == "pending_review"
    assert pending_job["status"] == "pending_review"
    assert pending_job["source_type"] == "instagram"
    assert pending_job["previous_version"] == 2
    assert pending_job["new_version"] is None


async def test_active_state_switching_and_resolution_use_chat_repository(tmp_path: Path) -> None:
    source = tmp_path / "dennis.md"
    source.write_text("# Dennis\n\n## Bio\n\nDefault bio.", encoding="utf-8")
    database = Database(tmp_path / "dennis.sqlite3")

    async with database.connect() as connection:
        await run_migrations(connection)
        knowledge_repository = KnowledgeRepository(connection)
        chat_repository = ChatRepository(connection)
        await chat_repository.upsert(456, chat_type="direct", title="Dennis DM")
        service = KnowledgeService(
            storage_dir=tmp_path / "store",
            default_source_path=source,
            repository=knowledge_repository,
            chat_repository=chat_repository,
        )
        await service.ensure_default_state()
        project = await service.create_or_update_state(
            name="project-x",
            description="Project X context",
            content="# Project X\n\n## Notes\n\nProject-specific context.",
            source_refs=["manual:/project-x"],
        )

        switched = await service.switch_active_state(chat_id=456, state_name="project-x")
        resolved = await service.resolve_active_state(chat_id=456)
        context = await service.retrieve_context(query="project specific", chat_id=456)

    assert project.id is not None
    assert switched.name == "project-x"
    assert resolved is not None
    assert resolved.name == "project-x"
    assert "Project-specific context" in context
