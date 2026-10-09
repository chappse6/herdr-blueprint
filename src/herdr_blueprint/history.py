"""Recent items shown in the viewer, newest last."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DIAGRAM_SUFFIXES = {".mmd", ".mermaid"}
DOCUMENT_SUFFIXES = {".md", ".markdown"}
VIEWABLE_SUFFIXES = DIAGRAM_SUFFIXES | DOCUMENT_SUFFIXES

# Larger files are cut so a huge generated doc can't freeze the viewer.
MAX_BYTES = 1_000_000

# Documents past either limit are shown as plain text: Textual lays out one
# widget per Markdown block, which takes seconds for big generated docs.
LARGE_DOC_CHARS = 100_000
LARGE_DOC_LINES = 2_000

# C0 controls except tab and newline, plus DEL and C1: these would reach the
# terminal as raw escape sequences.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(text: str) -> str:
    """Normalize line endings and replace control characters with U+FFFD."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("\ufffd", text)


def is_large(text: str) -> bool:
    return len(text) > LARGE_DOC_CHARS or text.count("\n") > LARGE_DOC_LINES


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
        """Text to render. Raises OSError when a file cannot be read.

        Bad bytes become U+FFFD and long files are cut, so odd files never
        crash the viewer. Documents get a notice; diagrams stay valid Mermaid.
        """
        if self.kind == "mermaid":
            return clean(self.source or "")
        assert self.path is not None
        with self.path.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        text = data[:MAX_BYTES].decode("utf-8", errors="replace")
        if len(data) > MAX_BYTES and not self.is_diagram:
            text += f"\n\n> This file is large. Showing the first {MAX_BYTES:,} bytes.\n"
        return clean(text)


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
