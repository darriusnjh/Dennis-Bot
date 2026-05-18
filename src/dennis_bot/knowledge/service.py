from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass
class KnowledgeState:
    name: str
    description: str
    id: int | None = None
    access_scope: str = "global"
    enabled: bool = True
    version: int = 1
    source_refs: list[str] = field(default_factory=list)
    content_path: str = ""
    index_status: str = "indexed"
    last_indexed_at: str | None = None
    last_updated_by_agent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class KnowledgeStateRepository(Protocol):
    async def upsert_state(
        self,
        *,
        name: str,
        description: str | None = None,
        access_scope: str = "global",
        version: int = 1,
        enabled: bool = True,
        source_refs: str | None = None,
        index_status: str = "indexed",
        last_updated_by_agent_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_state(self, state_id: int) -> dict[str, Any]: ...

    async def get_state_by_name(
        self, name: str, *, version: int | None = None
    ) -> dict[str, Any] | None: ...

    async def list_states(self, *, enabled_only: bool = False) -> list[dict[str, Any]]: ...


class ChatKnowledgeRepository(Protocol):
    async def get(self, telegram_chat_id: int) -> dict[str, Any] | None: ...

    async def set_active_knowledge_state(
        self, telegram_chat_id: int, knowledge_state_id: int | None
    ) -> None: ...


class KnowledgeService:
    """File-backed knowledge state service.

    State content remains in immutable markdown snapshots. When a repository is
    supplied, state/version metadata is mirrored to SQLite for command routing
    and chat-level active-state selection.
    """

    def __init__(
        self,
        *,
        storage_dir: Path | str = Path("data/knowledge"),
        default_source_path: Path | str = Path("knowledge_base/dennis-toh.md"),
        default_state_name: str = "dennis-toh",
        repository: KnowledgeStateRepository | None = None,
        chat_repository: ChatKnowledgeRepository | None = None,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.default_source_path = Path(default_source_path)
        self.default_state_name = default_state_name
        self.repository = repository
        self.chat_repository = chat_repository
        self._metadata_path = self.storage_dir / "states.json"

    async def ensure_default_state(self) -> KnowledgeState:
        states = self._load_states()
        if self.default_state_name in states:
            return await self._mirror_state(states[self.default_state_name])
        content = self.default_source_path.read_text(encoding="utf-8")
        return await self.create_or_update_state(
            name=self.default_state_name,
            description="Dennis Toh public-profile knowledge base",
            content=content,
            source_refs=[str(self.default_source_path)],
            create_new_version=False,
        )

    async def retrieve_context(
        self,
        *,
        query: str,
        chat_id: int | None = None,
        state_name: str | None = None,
    ) -> str:
        state = await self.get_state(state_name) if state_name else await self.resolve_active_state(chat_id=chat_id)
        if not state or not state.enabled:
            return ""
        if not state.content_path or not Path(state.content_path).exists():
            return ""
        content = self._read_state_content(state)
        excerpts = _ranked_excerpts(content, query)
        if not excerpts:
            return ""
        source = ", ".join(state.source_refs) or state.name
        return "\n\n".join(
            [f"Knowledge state: {state.name} v{state.version}", f"Source: {source}", *excerpts]
        )

    async def get_state(self, name: str) -> KnowledgeState | None:
        state = self._load_states().get(name)
        if state is not None:
            return state
        if self.repository is None:
            return None
        row = await self.repository.get_state_by_name(name)
        return self._state_from_row(row) if row else None

    async def list_states(self, *, enabled_only: bool = False) -> list[KnowledgeState]:
        if self.repository is not None:
            rows = await self.repository.list_states(enabled_only=enabled_only)
            return [self._state_from_row(row) for row in rows]
        states = list(self._load_states().values())
        if enabled_only:
            states = [state for state in states if state.enabled]
        return sorted(states, key=lambda state: (state.name, -state.version))

    async def inspect_state(self, name: str | None = None) -> KnowledgeState | None:
        return await self.get_state(name or self.default_state_name)

    async def status(self, *, chat_id: int | None = None) -> dict[str, Any]:
        state = await self.resolve_active_state(chat_id=chat_id)
        states = await self.list_states()
        return {
            "active_state": asdict(state) if state else None,
            "state_count": len(states),
            "repository_mirror_enabled": self.repository is not None,
        }

    async def resolve_active_state(self, *, chat_id: int | None = None) -> KnowledgeState | None:
        if chat_id is not None and self.chat_repository is not None and self.repository is not None:
            chat = await self.chat_repository.get(chat_id)
            state_id = chat.get("active_knowledge_state_id") if chat else None
            if state_id is not None:
                row = await self.repository.get_state(int(state_id))
                return self._state_from_row(row)
        return await self.get_state(self.default_state_name) or await self.ensure_default_state()

    async def switch_active_state(self, *, chat_id: int, state_name: str) -> KnowledgeState:
        if self.chat_repository is None or self.repository is None:
            raise RuntimeError("Knowledge state switching requires knowledge and chat repositories.")
        state = await self.get_state(state_name)
        if state is None:
            raise KeyError(f"Unknown knowledge state: {state_name}")
        mirrored = await self._mirror_state(state)
        if mirrored.id is None:
            raise RuntimeError(f"Knowledge state is not mirrored to the repository: {state_name}")
        await self.chat_repository.set_active_knowledge_state(chat_id, mirrored.id)
        return mirrored

    async def create_or_update_state(
        self,
        *,
        name: str,
        description: str,
        content: str,
        source_refs: list[str],
        access_scope: str = "global",
        enabled: bool = True,
        create_new_version: bool = True,
    ) -> KnowledgeState:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        states = self._load_states()
        previous = states.get(name)
        if previous and create_new_version:
            version = previous.version + 1
        elif previous:
            version = previous.version
        else:
            version = 1
        version_path = self._version_path(name, version)
        version_path.parent.mkdir(parents=True, exist_ok=True)
        version_path.write_text(content, encoding="utf-8")
        now = datetime.now(UTC).isoformat()
        state = KnowledgeState(
            name=name,
            description=description,
            access_scope=access_scope,
            enabled=enabled,
            version=version,
            source_refs=source_refs,
            content_path=str(version_path),
            index_status="indexed",
            last_indexed_at=now,
            updated_at=now,
        )
        states[name] = state
        self._save_states(states)
        return await self._mirror_state(state)

    async def append_version(
        self,
        *,
        state_name: str,
        update_markdown: str,
        source_ref: str,
        summary: str,
    ) -> KnowledgeState:
        states = self._load_states()
        previous = states.get(state_name)
        if not previous:
            raise KeyError(f"Unknown knowledge state: {state_name}")
        current_content = self._read_state_content(previous)
        updated_content = "\n\n".join(
            [
                current_content.rstrip(),
                "## Agent-Applied Update",
                f"Summary: {summary}",
                f"Source: {source_ref}",
                update_markdown.strip(),
                "",
            ]
        )
        refs = [*previous.source_refs]
        if source_ref not in refs:
            refs.append(source_ref)
        return await self.create_or_update_state(
            name=state_name,
            description=previous.description,
            content=updated_content,
            source_refs=refs,
            access_scope=previous.access_scope,
            enabled=previous.enabled,
            create_new_version=True,
        )

    async def _mirror_state(self, state: KnowledgeState) -> KnowledgeState:
        if self.repository is None:
            return state
        row = await self.repository.upsert_state(
            name=state.name,
            description=state.description,
            access_scope=state.access_scope,
            version=state.version,
            enabled=state.enabled,
            source_refs=json.dumps(state.source_refs, sort_keys=True),
            index_status=state.index_status,
            last_updated_by_agent_id=state.last_updated_by_agent_id,
        )
        mirrored = self._state_from_row(row)
        mirrored.content_path = state.content_path
        if not mirrored.last_indexed_at:
            mirrored.last_indexed_at = state.last_indexed_at
        return mirrored

    def _load_states(self) -> dict[str, KnowledgeState]:
        if not self._metadata_path.exists():
            return {}
        raw = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        return {name: KnowledgeState(**value) for name, value in raw.items()}

    def _save_states(self, states: dict[str, KnowledgeState]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {name: asdict(state) for name, state in states.items()}
        self._metadata_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _version_path(self, name: str, version: int) -> Path:
        safe_name = "".join(char if char.isalnum() or char in "-_" else "-" for char in name)
        return self.storage_dir / safe_name / f"v{version}.md"

    def _read_state_content(self, state: KnowledgeState) -> str:
        return Path(state.content_path).read_text(encoding="utf-8")

    def _state_from_row(self, row: dict[str, Any]) -> KnowledgeState:
        source_refs = row.get("source_refs")
        refs: list[str]
        if not source_refs:
            refs = []
        else:
            try:
                loaded = json.loads(str(source_refs))
                refs = loaded if isinstance(loaded, list) else [str(source_refs)]
            except json.JSONDecodeError:
                refs = [part.strip() for part in str(source_refs).split(",") if part.strip()]
        file_state = self._load_states().get(str(row["name"]))
        return KnowledgeState(
            id=int(row["id"]) if row.get("id") is not None else None,
            name=str(row["name"]),
            description=row.get("description") or "",
            access_scope=row.get("access_scope") or "global",
            enabled=bool(row.get("enabled", True)),
            version=int(row.get("version", 1)),
            source_refs=refs,
            content_path=file_state.content_path if file_state else "",
            index_status=row.get("index_status") or "indexed",
            last_indexed_at=row.get("last_indexed_at"),
            last_updated_by_agent_id=row.get("last_updated_by_agent_id"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


def _ranked_excerpts(content: str, query: str, *, limit: int = 3) -> list[str]:
    terms = {
        term.lower()
        for term in query.replace("?", " ").replace(",", " ").split()
        if len(term) > 2
    }
    sections = [section.strip() for section in content.split("\n## ") if section.strip()]
    scored: list[tuple[int, str]] = []
    for section in sections:
        lowered = section.lower()
        score = sum(1 for term in terms if term in lowered)
        if score:
            text = section if section.startswith("#") else f"## {section}"
            scored.append((score, _truncate(text)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:limit]]


def _truncate(text: str, *, max_chars: int = 1200) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."
