"""Turn a terminal.py snapshot into a framed SVG and a 2x PNG, without a browser.

Usage: python render.py screen.json OUT_STEM [--title TITLE] [--crop X,Y,W,H]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.style import Style
from rich.terminal_theme import TerminalTheme
from rich.text import Text

# Rose Pine, the theme the demo herdr uses, for colors given by ANSI name.
ROSE_PINE = TerminalTheme(
    (25, 23, 36),
    (224, 222, 244),
    [
        (38, 35, 58), (235, 111, 146), (49, 116, 143), (246, 193, 119),
        (156, 207, 216), (196, 167, 231), (235, 188, 186), (224, 222, 244),
    ],
    [
        (110, 106, 134), (235, 111, 146), (49, 116, 143), (246, 193, 119),
        (156, 207, 216), (196, 167, 231), (235, 188, 186), (224, 222, 244),
    ],
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from png import local_fonts, write_png  # noqa: E402


def color(value: str) -> str | None:
    """pyte color (default, a name, or rrggbb) as a Rich color."""
    if value == "default":
        return None
    if re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return f"#{value}"
    name = value.replace("brown", "yellow")
    return f"bright_{name[6:]}" if name.startswith("bright") else name


def to_text(rows: list[list[list]], crop: tuple[int, int, int, int] | None) -> Text:
    if crop:
        x, y, w, h = crop
        rows = [row[x:x + w] for row in rows[y:y + h]]
    text = Text()
    for i, row in enumerate(rows):
        for data, fg, bg, bold, italics, underscore, reverse in row:
            if data == "":
                continue  # second half of a wide character
            text.append(data, Style(
                color=color(fg), bgcolor=color(bg), bold=bold, italic=italics,
                underline=underscore, reverse=reverse,
            ))
        if i < len(rows) - 1:
            text.append("\n")
    return text


def to_svg(rows: list[list[list]], title: str = "herdr", crop: tuple[int, int, int, int] | None = None) -> str:
    """A snapshot as a framed terminal SVG with local fonts."""
    width = crop[2] if crop else len(rows[0])
    console = Console(record=True, width=width, file=io.StringIO(), color_system="truecolor", force_terminal=True)
    console.print(to_text(rows, crop), soft_wrap=True)
    return local_fonts(console.export_svg(title=title, theme=ROSE_PINE))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("screen", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--title", default="herdr")
    parser.add_argument("--crop", default=None, help="X,Y,W,H in cells")
    parser.add_argument("--zoom", type=float, default=2, help="PNG scale")
    parser.add_argument("--no-svg", action="store_true", help="write only the PNG")
    args = parser.parse_args()

    rows = json.loads(args.screen.read_text())
    crop = tuple(int(n) for n in args.crop.split(",")) if args.crop else None
    svg = to_svg(rows, args.title, crop)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not args.no_svg:
        args.out.with_suffix(".svg").write_text(svg, encoding="utf-8")
    write_png(svg, args.out.with_suffix(".png"), zoom=args.zoom)
    print(args.out.with_suffix(".png"))


if __name__ == "__main__":
    main()
