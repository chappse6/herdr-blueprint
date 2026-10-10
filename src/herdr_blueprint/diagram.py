"""Mermaid rendering. This is the only module that imports termaid."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from rich.cells import cell_len
from rich.text import Text
from termaid import render_rich
from termaid.renderer import themes as termaid_themes
from termaid.renderer.canvas import Canvas

from .themes import PALETTES, Palette

# From roomy to compact: (padding_x, padding_y, gap). The first one that fits wins.
_LAYOUTS = [(4, 2, 4), (2, 1, 3), (1, 0, 2)]

# termaid gets slow fast: a 200-node tree takes ~9 s. Larger sources are shown as text.
MAX_DIAGRAM_CHARS = 10_000


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


for _palette in PALETTES.values():
    _register(_palette)


_cell_pairs = Canvas.to_styled_pairs


def _char_pairs(canvas: Canvas) -> list[list[tuple[str, str]]]:
    # termaid puts an empty shadow cell after each wide (CJK) character, joins
    # the characters without it, then styles by cell index, so every style after
    # a wide character lands one letter late. Dropping the shadow cells keeps
    # one (char, style) pair per character. Only termaid's Rich output calls this.
    return [[(ch, key) for ch, key in row if ch != ""] for row in _cell_pairs(canvas)]


Canvas.to_styled_pairs = _char_pairs


@dataclass(frozen=True)
class Drawing:
    """A drawn diagram and the layout (padding_x, padding_y, gap) it was drawn with."""

    text: Text
    padding: tuple[int, int, int]


@lru_cache(maxsize=64)
def _render(source: str, theme_name: str, max_width: int | None) -> Drawing:
    # Cached because a diagram is redrawn on mount, theme change and resize.
    # Callers must not modify the returned Text.
    text = Text()
    layout = _LAYOUTS[0]
    for layout in _LAYOUTS:
        padding_x, padding_y, gap = layout
        try:
            text = render_rich(
                source,
                theme=theme_name,
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
    return Drawing(text, layout)


def render_diagram(source: str, palette: Palette, max_width: int | None = None) -> Text:
    """Draw Mermaid source as styled text that fits `max_width` when possible."""
    return draw_diagram(source, palette, max_width).text


def draw_diagram(source: str, palette: Palette, max_width: int | None = None) -> Drawing:
    """Like render_diagram, and say which layout won (point mode needs it)."""
    if len(source) > MAX_DIAGRAM_CHARS:
        raise DiagramError(
            f"This diagram is too large to draw ({len(source):,} characters, "
            f"limit {MAX_DIAGRAM_CHARS:,})"
        )
    if palette.theme_name not in termaid_themes.THEMES:
        _register(palette)
    return _render(source, palette.theme_name, max_width)
