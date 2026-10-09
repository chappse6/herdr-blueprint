"""Views for documents and diagrams. Mermaid is drawn with the active palette."""

from __future__ import annotations

from rich.text import Text
from textual.app import App
from textual.content import Content
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownFence

from .diagram import DiagramError, render_diagram
from .themes import palette_for_theme


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
        if self.is_mermaid:
            self._highlighted_code = self._draw()

    def _draw(self) -> Content:
        return draw(self.app, self.code, max(20, self.app.size.width - self.FRAME_WIDTH))

    @property
    def is_mermaid(self) -> bool:
        return (self.lexer or "").strip().lower() == "mermaid"

    def notify_style_update(self) -> None:
        if not self.is_mermaid:
            super().notify_style_update()
            return
        # Redraw with the new palette; skip MarkdownFence's syntax re-highlight.
        self._highlighted_code = self._draw()
        self.set_content(self._highlighted_code)
        super(MarkdownFence, self).notify_style_update()


class DocumentView(Markdown):
    """Markdown with Mermaid fences drawn as diagrams."""

    BLOCKS = {**Markdown.BLOCKS, "fence": DiagramFence}


class DiagramView(Static):
    """A single Mermaid diagram that redraws on theme change and resize."""

    def __init__(self, source: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.source = source

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._redraw, init=True)

    def on_resize(self) -> None:
        self._redraw()

    def _redraw(self, _theme: str | None = None) -> None:
        width = self.parent.size.width if self.parent else None
        self.update(draw(self.app, self.source, width or None))
