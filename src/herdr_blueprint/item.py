"""What the viewer shows: one file or snippet the worker sent."""

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

# First words of Mermaid diagrams; a snippet starting with one is a diagram.
_MERMAID_STARTS = (
    "graph", "flowchart", "sequenceDiagram", "classDiagram", "stateDiagram",
    "erDiagram", "journey", "gantt", "pie", "quadrantChart", "requirementDiagram",
    "gitGraph", "mindmap", "timeline", "sankey", "xychart", "block", "packet",
    "kanban", "architecture", "zenuml", "radar", "treemap",
    "C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment",
)
# Variants such as stateDiagram-v2, xychart-beta, flowchart-elk.
_MERMAID_SUFFIXES = ("", "-v2", "-beta", "-elk")


def clean(text: str) -> str:
    """Normalize line endings and replace control characters with U+FFFD."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("\ufffd", text)


def is_large(text: str) -> bool:
    return len(text) > LARGE_DOC_CHARS or text.count("\n") > LARGE_DOC_LINES


def looks_like_mermaid(source: str) -> bool:
    """True when the first line after front matter, blanks and %% lines opens a diagram."""
    lines = source.lstrip("\ufeff").splitlines()
    # Mermaid front matter: a block between two "---" lines before the diagram.
    first_text = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_text is not None and lines[first_text].strip() == "---":
        closing = next((i for i in range(first_text + 1, len(lines)) if lines[i].strip() == "---"), None)
        if closing is None:
            return False
        lines = lines[closing + 1 :]
    for line in lines:
        words = line.split()
        if not words or words[0].startswith("%%"):
            continue
        return words[0] in {start + suffix for start in _MERMAID_STARTS for suffix in _MERMAID_SUFFIXES}
    return False


@dataclass(frozen=True)
class Item:
    kind: Literal["file", "mermaid", "markdown"]
    title: str
    path: Path | None = None
    source: str | None = None
    sent_by: str = "agent"
    at: float = field(default_factory=time.time)
    # Draw Mermaid with termaid. Off by default: drawing is the expensive part.
    draw: bool = False

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
        if self.kind != "file":
            return clean(self.source or "")
        assert self.path is not None
        with self.path.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        text = data[:MAX_BYTES].decode("utf-8-sig", errors="replace")
        if len(data) > MAX_BYTES and not self.is_diagram:
            text += f"\n\n> This file is large. Showing the first {MAX_BYTES:,} bytes.\n"
        return clean(text)
