from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ChatRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ChatMessage:
    role: ChatRole
    content: str
    name: str | None = None

    def to_openai(self) -> dict[str, str]:
        payload = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(frozen=True)
class ChatResponse:
    content: str
    model: str
    raw: dict[str, Any] = field(default_factory=dict)
