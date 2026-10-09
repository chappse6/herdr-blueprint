"""Recent items shown in the viewer, newest last."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DIAGRAM_SUFFIXES = {".mmd", ".mermaid"}
DOCUMENT_SUFFIXES = {".md", ".markdown"}
VIEWABLE_SUFFIXES = DIAGRAM_SUFFIXES | DOCUMENT_SUFFIXES


@dataclass(frozen=True)
class Item:
    kind: Literal["file", "mermaid"]
    title: str
    path: Path | None = None
    source: str | None = None
    sent_by: str = "follow"
    at: float = field(default_factory=time.time)

    @property
    def is_diagram(self) -> bool:
        if self.kind == "mermaid":
            return True
        return self.path is not None and self.path.suffix.lower() in DIAGRAM_SUFFIXES

    def read(self) -> str:
        """Text to render. Raises OSError when a file cannot be read."""
        if self.kind == "mermaid":
            return self.source or ""
        assert self.path is not None
        return self.path.read_text(encoding="utf-8")


class History:
    """Back/forward list. Pushing a file that is already listed moves it to the end."""

    def __init__(self, limit: int = 20) -> None:
        self.limit = limit
        self.items: list[Item] = []
        self.index = -1

    @property
    def current(self) -> Item | None:
        return self.items[self.index] if self.items else None

    def push(self, item: Item, focus: bool = True) -> Item:
        """Add an item. With focus=False the current item stays selected."""
        previous = self.current
        if item.path is not None:
            self.items = [i for i in self.items if i.path != item.path]
        self.items.append(item)
        self.items = self.items[-self.limit :]
        if focus or previous not in self.items:
            self.index = len(self.items) - 1
        else:
            self.index = self.items.index(previous)
        return item

    def back(self) -> Item | None:
        if self.index > 0:
            self.index -= 1
        return self.current

    def forward(self) -> Item | None:
        if self.index < len(self.items) - 1:
            self.index += 1
        return self.current
