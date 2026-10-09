"""Mermaid rendering. This is the only module that imports termaid."""

from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text
from termaid import render_rich
from termaid.renderer import themes as termaid_themes

from .themes import Palette

# From roomy to compact: (padding_x, padding_y, gap). The first one that fits wins.
_LAYOUTS = [(4, 2, 4), (2, 1, 3), (1, 0, 2)]


class DiagramError(Exception):
    """termaid could not draw the source."""


def width_of(text: Text) -> int:
    """Terminal columns of the widest line (wide CJK characters count as 2)."""
    return max((cell_len(line) for line in text.plain.splitlines()), default=0)


def _register(palette: Palette) -> None:
    # termaid only takes theme names, so palettes go into its theme table.
    # This is an internal API; the termaid version is pinned in pyproject.toml.
    termaid_themes.THEMES[palette.theme_name] = termaid_themes.Theme(
        name=palette.theme_name,
        node=palette.node,
        edge=palette.edge,
        arrow=palette.arrow,
        subgraph=palette.muted,
        label=palette.foreground,
        edge_label=palette.edge_label,
        subgraph_label=f"bold {palette.secondary}",
    )


def render_diagram(source: str, palette: Palette, max_width: int | None = None) -> Text:
    """Draw Mermaid source as styled text that fits `max_width` when possible."""
    _register(palette)
    text = Text()
    for padding_x, padding_y, gap in _LAYOUTS:
        try:
            text = render_rich(
                source,
                theme=palette.theme_name,
                padding_x=padding_x,
                padding_y=padding_y,
                gap=gap,
            )
        except Exception as exc:  # termaid raises plain exceptions on bad input
            raise DiagramError(str(exc) or type(exc).__name__) from exc
        if max_width is None or width_of(text) <= max_width:
            break
    if not text.plain.strip():
        raise DiagramError("termaid returned no output")
    return text
