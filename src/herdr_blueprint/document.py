"""Views for documents and diagrams. Mermaid is drawn with the active palette.

termaid can take seconds on a big diagram, so drawing runs in a thread and the
view shows a placeholder until it is done.
"""

from __future__ import annotations

import asyncio
from functools import partial

from rich.text import Text
from textual.app import App
from textual.content import Content
from textual.widgets import Log, Markdown, Static
from textual.widgets.markdown import MarkdownFence
from textual.worker import get_current_worker

from .diagram import DiagramError, render_diagram
from .themes import palette_for_theme

PLACEHOLDER = "Drawing diagram…"


def draw(app: App, source: str, max_width: int | None = None) -> Content:
    """Mermaid source as themed content, or an error notice followed by the source."""
    palette = palette_for_theme(app.theme)
    try:
        return Content.from_rich_text(render_diagram(source, palette, max_width))
    except DiagramError as exc:
        notice = Text(f"Could not draw this diagram: {exc}\n\n", style=f"bold {palette.error}")
        return Content.from_rich_text(notice + Text(source, style=palette.muted))


class DiagramFence(MarkdownFence):
    """A fence that draws ```mermaid blocks and highlights all other code."""

    # Columns taken by the body padding and the fence padding around a diagram.
    FRAME_WIDTH = 10

    def __init__(self, markdown: Markdown, token, code: str) -> None:
        super().__init__(markdown, token, code)
        self._requested: tuple[str, int] | None = None
        if self.is_mermaid:
            self._highlighted_code = Content(PLACEHOLDER)

    @property
    def is_mermaid(self) -> bool:
        return (self.lexer or "").strip().lower() == "mermaid"

    def on_mount(self) -> None:
        if self.is_mermaid:
            self._redraw()

    def notify_style_update(self) -> None:
        if not self.is_mermaid:
            super().notify_style_update()
            return
        # Redraw with the new palette; skip MarkdownFence's syntax re-highlight.
        self._redraw()
        super(MarkdownFence, self).notify_style_update()

    def _redraw(self) -> None:
        width = max(20, self.app.size.width - self.FRAME_WIDTH)
        if self._requested == (self.app.theme, width):
            return
        self._requested = (self.app.theme, width)
        self.run_worker(partial(self._draw, width), thread=True, exclusive=True, group="draw")

    def _draw(self, width: int) -> None:
        content = draw(self.app, self.code, width)
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self._show, content)

    def _show(self, content: Content) -> None:
        self._highlighted_code = content
        self.set_content(content)


class DocumentView(Markdown):
    """Markdown with Mermaid fences drawn as diagrams."""

    BLOCKS = {**Markdown.BLOCKS, "fence": DiagramFence}


class PlainView(Log):
    """Plain text for files too large to lay out as Markdown."""

    # Lines written per step; the event loop runs between steps.
    CHUNK_LINES = 1_000

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=False, auto_scroll=False, **kwargs)

    async def load(self, text: str) -> None:
        """Write text in steps so a 1 MB file never freezes the screen."""
        lines = text.split("\n")
        for start in range(0, len(lines), self.CHUNK_LINES):
            self.write_lines(lines[start : start + self.CHUNK_LINES], scroll_end=False)
            await asyncio.sleep(0)


class DiagramView(Static):
    """A single Mermaid diagram that redraws on theme change and resize."""

    def __init__(self, source: str, **kwargs) -> None:
        super().__init__(PLACEHOLDER, markup=False, **kwargs)
        self.source = source
        self._requested: tuple[str, int | None] | None = None

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._redraw, init=True)

    def on_resize(self) -> None:
        self._redraw()

    def _redraw(self, _theme: str | None = None) -> None:
        width = (self.parent.size.width if self.parent else 0) or None
        if self._requested == (self.app.theme, width):
            return
        self._requested = (self.app.theme, width)
        self.run_worker(partial(self._draw, width), thread=True, exclusive=True, group="draw")

    def _draw(self, width: int | None) -> None:
        content = draw(self.app, self.source, width)
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self.update, content)
