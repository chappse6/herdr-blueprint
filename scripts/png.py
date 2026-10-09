"""SVG to a 2x PNG with resvg, so README images need no browser."""

from __future__ import annotations

import re
from pathlib import Path

import resvg_py

FONT = "SF Mono"


def local_fonts(svg: str) -> str:
    """Swap the web font Textual and Rich link to for fonts on this machine."""
    svg = re.sub(r"@font-face \{.*?\}\s*", "", svg, flags=re.S)
    svg = svg.replace("font-family: Fira Code, monospace", f"font-family: '{FONT}', Menlo, monospace")
    # resvg can't embolden the variable SF Mono, so the bold title uses Menlo.
    return svg.replace("font-family: arial", "font-family: Menlo")


def write_png(svg: str, png: Path) -> None:
    """Box-drawing lines look dashed when GitHub scales an SVG down; a PNG doesn't."""
    data = resvg_py.svg_to_bytes(svg_string=svg, zoom=2, font_family=FONT, monospace_family=FONT)
    png.write_bytes(bytes(data))
