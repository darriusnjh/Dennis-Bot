from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from dennis_bot.knowledge.service import KnowledgeService

ImpactClassification = Literal["minor", "notifiable", "kb_impactful"]
UpdateStatus = Literal["queued", "running", "applied", "pending_review", "rejected", "failed", "ignored"]


class KnowledgeUpdateJobRepository(Protocol):
    async def create_update_job(
        self,
        *,
        source_type: str,
        source_monitor_id: int | None = None,
        source_url: str | None = None,
        detected_change_ref: str | None = None,
        impact_classification: str | None = None,
        status: str = "queued",
        summary: str | None = None,
        knowledge_state_id: int | None = None,
        previous_version: int | None = None,
        new_version: int | None = None,
    ) -> dict[str, Any]: ...

    async def update_job_status(
        self,
        job_id: int,
        status: str,
        *,
        summary: str | None = None,
        impact_classification: str | None = None,
        knowledge_state_id: int | None = None,
        previous_version: int | None = None,
        new_version: int | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SourceChange:
    source_url: str
    source_type: str
    title: str
    before: str = ""
    after: str = ""
    diff: str = ""
    detected_at: str | None = None


@dataclass(frozen=True)
class KnowledgeUpdateDecision:
    job_id: str
    classification: ImpactClassification
    confidence: float
    status: UpdateStatus
    summary: str
    knowledge_state_name: str
    previous_version: int | None = None
    new_version: int | None = None
    source_url: str = ""
    source_type: str = ""
    created_at: str = ""


class KnowledgeUpdateAgent:
    def __init__(
        self,
        *,
        knowledge_service: KnowledgeService,
        jobs_dir: Path | str = Path("data/knowledge_update_jobs"),
        auto_apply_confidence: float = 0.72,
        repository: KnowledgeUpdateJobRepository | None = None,
    ) -> None:
        self._knowledge_service = knowledge_service
        self._jobs_dir = Path(jobs_dir)
        self._auto_apply_confidence = auto_apply_confidence
        self._repository = repository or knowledge_service.repository

    async def review_change(
        self,
        change: SourceChange,
        *,
        knowledge_state_name: str = "dennis-toh",
        persist_job: bool = True,
        existing_job_id: int | None = None,
    ) -> KnowledgeUpdateDecision:
        state = await self._knowledge_service.get_state(knowledge_state_name)
        if state is None:
            state = await self._knowledge_service.ensure_default_state()
        elif self._repository is not None:
            state = await self._knowledge_service._mirror_state(state)

        classification, confidence, summary = self.classify(change)
        status: UpdateStatus = "ignored"
        new_version: int | None = None
        previous_version = state.version
        knowledge_state_id = state.id
        db_job_id = existing_job_id

        if persist_job and self._repository is not None and db_job_id is None:
            job = await self._repository.create_update_job(
                source_type=change.source_type,
                source_url=change.source_url,
                detected_change_ref=self._change_ref(change),
                impact_classification=classification,
                status="running",
                summary=summary,
                knowledge_state_id=knowledge_state_id,
                previous_version=previous_version,
            )
            db_job_id = int(job["id"])
        elif persist_job and self._repository is not None and db_job_id is not None:
            await self._repository.update_job_status(
                db_job_id,
                "running",
                summary=summary,
                impact_classification=classification,
                knowledge_state_id=knowledge_state_id,
                previous_version=previous_version,
            )

        if confidence < self._auto_apply_confidence and classification != "minor":
            status = "pending_review"
        elif classification == "kb_impactful":
            updated_state = await self._knowledge_service.append_version(
                state_name=state.name,
                update_markdown=self._update_markdown(change),
                source_ref=change.source_url,
                summary=summary,
            )
            status = "applied"
            new_version = updated_state.version
            knowledge_state_id = updated_state.id
        elif classification == "notifiable":
            status = "ignored"

        if persist_job and self._repository is not None and db_job_id is not None:
            await self._repository.update_job_status(
                db_job_id,
                self._db_status(status),
                summary=summary,
                impact_classification=classification,
                knowledge_state_id=knowledge_state_id,
                previous_version=previous_version,
                new_version=new_version,
            )

        decision = KnowledgeUpdateDecision(
            job_id=str(db_job_id or uuid4()),
            classification=classification,
            confidence=confidence,
            status=status,
            summary=summary,
            knowledge_state_name=state.name,
            previous_version=previous_version,
            new_version=new_version,
            source_url=change.source_url,
            source_type=change.source_type,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._write_job(decision, change)
        return decision

    async def request_manual_update(
        self,
        change: SourceChange,
        *,
        knowledge_state_name: str = "dennis-toh",
        run_review: bool = True,
    ) -> KnowledgeUpdateDecision:
        manual_change = SourceChange(
            source_url=change.source_url,
            source_type="manual",
            title=change.title,
            before=change.before,
            after=change.after,
            diff=change.diff,
            detected_at=change.detected_at,
        )
        state = await self._knowledge_service.get_state(knowledge_state_name)
        if state is None:
            state = await self._knowledge_service.ensure_default_state()
        elif self._repository is not None:
            state = await self._knowledge_service._mirror_state(state)
        db_job_id: int | None = None
        if self._repository is not None:
            job = await self._repository.create_update_job(
                source_type="manual",
                source_url=manual_change.source_url,
                detected_change_ref=self._change_ref(manual_change),
                status="queued",
                summary=manual_change.title,
                knowledge_state_id=state.id,
                previous_version=state.version,
            )
            db_job_id = int(job["id"])
        if run_review:
            return await self.review_change(
                manual_change,
                knowledge_state_name=knowledge_state_name,
                persist_job=True,
                existing_job_id=db_job_id,
            )
        decision = KnowledgeUpdateDecision(
            job_id=str(db_job_id or uuid4()),
            classification="notifiable",
            confidence=0.0,
            status="queued",
            summary=manual_change.title,
            knowledge_state_name=state.name,
            previous_version=state.version,
            source_url=manual_change.source_url,
            source_type="manual",
            created_at=datetime.now(UTC).isoformat(),
        )
        self._write_job(decision, manual_change)
        return decision

    def classify(self, change: SourceChange) -> tuple[ImpactClassification, float, str]:
        text = "\n".join([change.title, change.diff, change.after]).lower()
        if not text.strip():
            return "minor", 0.95, "No substantive content was supplied."

        minor_terms = ["navigation", "font", "layout", "spacing", "css", "menu", "footer", "typo"]
        impactful_terms = [
            "award",
            "role",
            "cast",
            "film",
            "series",
            "drama",
            "theatre",
            "tour",
            "hong kong",
            "shenzhen",
            "biography",
            "profile",
            "lecturer",
            "business",
            "company",
            "founded",
            "producer",
            "official",
        ]
        notifiable_terms = [
            "new post",
            "caption",
            "photo",
            "reel",
            "event",
            "announcement",
            "media",
        ]

        impactful_hits = sum(1 for term in impactful_terms if term in text)
        notifiable_hits = sum(1 for term in notifiable_terms if term in text)
        minor_hits = sum(1 for term in minor_terms if term in text)

        if impactful_hits:
            confidence = min(0.96, 0.68 + impactful_hits * 0.08)
            return (
                "kb_impactful",
                confidence,
                "Detected factual public-profile or career information.",
            )
        if notifiable_hits:
            confidence = min(0.9, 0.64 + notifiable_hits * 0.08)
            return (
                "notifiable",
                confidence,
                "Detected a public update worth notifying but not canonical KB storage.",
            )
        if minor_hits:
            return (
                "minor",
                min(0.94, 0.7 + minor_hits * 0.05),
                "Detected presentation or low-value wording changes.",
            )
        return "notifiable", 0.55, "Detected content movement, but impact is uncertain."

    def _update_markdown(self, change: SourceChange) -> str:
        detected_at = change.detected_at or datetime.now(UTC).isoformat()
        parts = [
            f"- Detected at: {detected_at}",
            f"- Title: {change.title}",
        ]
        if change.diff:
            parts.append("- Source diff:\n\n```text\n" + change.diff.strip() + "\n```")
        elif change.after:
            excerpt = change.after.strip()[:2000]
            parts.append("- Source content excerpt:\n\n```text\n" + excerpt + "\n```")
        return "\n".join(parts)

    def _write_job(self, decision: KnowledgeUpdateDecision, change: SourceChange) -> None:
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "decision": asdict(decision),
            "change": asdict(change),
        }
        path = self._jobs_dir / f"{decision.job_id}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _change_ref(self, change: SourceChange) -> str:
        return json.dumps(asdict(change), sort_keys=True)

    def _db_status(self, status: UpdateStatus) -> str:
        if status == "ignored":
            return "rejected"
        return status
