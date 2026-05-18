from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MarkdownDocument:
    path: Path
    title: str
    content: str


def load_markdown_document(path: Path | str) -> MarkdownDocument:
    resolved = Path(path)
    content = resolved.read_text(encoding="utf-8")
    title = _first_heading(content) or resolved.stem.replace("-", " ").title()
    return MarkdownDocument(path=resolved, title=title, content=content)


def _first_heading(content: str) -> str | None:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None
