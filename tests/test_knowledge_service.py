from __future__ import annotations

from pathlib import Path

import pytest

from dennis_bot.agents.knowledge_update.agent import KnowledgeUpdateAgent, SourceChange
from dennis_bot.knowledge.service import KnowledgeService


@pytest.mark.asyncio
async def test_knowledge_service_retrieves_relevant_context(tmp_path: Path) -> None:
    source = tmp_path / "dennis.md"
    source.write_text(
        "# Dennis Toh Knowledge Base\n\n"
        "## Theatre\n\nDennis acted in Four Horse Road.\n\n"
        "## Education\n\nDennis teaches Mass Communication.",
        encoding="utf-8",
    )
    service = KnowledgeService(storage_dir=tmp_path / "store", default_source_path=source)

    state = await service.ensure_default_state()
    context = await service.retrieve_context(query="What theatre work did Dennis do?")

    assert state.version == 1
    assert "Knowledge state: dennis-toh v1" in context
    assert "Four Horse Road" in context


@pytest.mark.asyncio
async def test_knowledge_update_agent_applies_confident_kb_impactful_change(tmp_path: Path) -> None:
    source = tmp_path / "dennis.md"
    source.write_text("# Dennis\n\n## Screen\n\nExisting credit.", encoding="utf-8")
    service = KnowledgeService(storage_dir=tmp_path / "store", default_source_path=source)
    await service.ensure_default_state()
    agent = KnowledgeUpdateAgent(knowledge_service=service, jobs_dir=tmp_path / "jobs")

    decision = await agent.review_change(
        SourceChange(
            source_url="https://www.dennistohsg.com/about",
            source_type="official_site",
            title="New theatre role announced",
            diff="+ Dennis joins a new theatre tour in Hong Kong.",
        )
    )
    state = await service.get_state("dennis-toh")
    context = await service.retrieve_context(query="Hong Kong theatre tour")

    assert decision.classification == "kb_impactful"
    assert decision.status == "applied"
    assert decision.previous_version == 1
    assert decision.new_version == 2
    assert state is not None
    assert state.version == 2
    assert "Hong Kong" in context
    assert list((tmp_path / "jobs").glob("*.json"))


@pytest.mark.asyncio
async def test_knowledge_update_agent_creates_pending_review_for_low_confidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dennis.md"
    source.write_text("# Dennis\n\n## Bio\n\nExisting bio.", encoding="utf-8")
    service = KnowledgeService(storage_dir=tmp_path / "store", default_source_path=source)
    await service.ensure_default_state()
    agent = KnowledgeUpdateAgent(
        knowledge_service=service,
        jobs_dir=tmp_path / "jobs",
        auto_apply_confidence=0.8,
    )

    decision = await agent.review_change(
        SourceChange(
            source_url="https://www.instagram.com/dennistohsg/",
            source_type="instagram",
            title="Text changed",
            diff="+ Updated wording with unclear significance.",
        )
    )
    state = await service.get_state("dennis-toh")

    assert decision.classification == "notifiable"
    assert decision.status == "pending_review"
    assert state is not None
    assert state.version == 1


@pytest.mark.asyncio
async def test_manual_update_can_queue_json_artifact_without_review(tmp_path: Path) -> None:
    source = tmp_path / "dennis.md"
    source.write_text("# Dennis\n\n## Bio\n\nExisting bio.", encoding="utf-8")
    service = KnowledgeService(storage_dir=tmp_path / "store", default_source_path=source)
    await service.ensure_default_state()
    agent = KnowledgeUpdateAgent(knowledge_service=service, jobs_dir=tmp_path / "jobs")

    decision = await agent.request_manual_update(
        SourceChange(
            source_url="manual:/kb update",
            source_type="manual",
            title="Admin requested KB update",
            diff="+ Dennis has a new official profile update.",
        ),
        run_review=False,
    )

    assert decision.status == "queued"
    assert decision.source_type == "manual"
    assert list((tmp_path / "jobs").glob("*.json"))
