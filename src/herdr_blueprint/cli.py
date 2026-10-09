"""Command line: run the viewer, or send something to a running viewer."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .history import VIEWABLE_SUFFIXES
from .sources import inbox

PLUGIN_ID = "seeun.blueprint"


def detect_root() -> Path:
    """Workspace folder: the focused pane's folder when started by herdr, else cwd."""
    context = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if context:
        try:
            data = json.loads(context)
        except ValueError:
            data = None
        for key in ("foreground_cwd", "cwd"):
            found = _find_key(data, key)
            if found and Path(found).is_dir():
                return Path(found)
    return Path.cwd()


def _find_key(data: object, key: str) -> str | None:
    if isinstance(data, dict):
        if isinstance(data.get(key), str):
            return data[key]
        values = data.values()
    elif isinstance(data, list):
        values = data
    else:
        return None
    for value in values:
        if found := _find_key(value, key):
            return found
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herdr-blueprint",
        description="Live Markdown and Mermaid viewer for the herdr side pane.",
    )
    parser.add_argument("--root", type=Path, help="folder to follow (default: workspace folder)")
    commands = parser.add_subparsers(dest="command")
    show = commands.add_parser("show", help="show a Markdown or Mermaid file in the viewer")
    show.add_argument("path", type=Path)
    draw = commands.add_parser("draw", help="send Mermaid from stdin to the viewer")
    draw.add_argument("--title", default="Diagram", help="label in the history bar")
    commands.add_parser("open", help="open the viewer pane in herdr")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "show":
        path = args.path.expanduser().resolve()
        if not path.is_file():
            print(f"herdr-blueprint: file not found: {path}", file=sys.stderr)
            return 1
        if path.suffix.lower() not in VIEWABLE_SUFFIXES:
            print("herdr-blueprint: only .md, .markdown, .mmd and .mermaid files", file=sys.stderr)
            return 2
        inbox.send({"kind": "file", "path": str(path), "sent_by": "agent"})
        print(f"Sent to Blueprint: {path.name}")
        return 0

    if args.command == "draw":
        source = sys.stdin.read().strip()
        if not source:
            print("herdr-blueprint: no Mermaid on stdin", file=sys.stderr)
            return 2
        inbox.send({"kind": "mermaid", "source": source, "title": args.title, "sent_by": "agent"})
        print(f"Sent to Blueprint: {args.title}")
        return 0

    if args.command == "open":
        herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
        cmd = [herdr, "plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", "viewer"]
        return subprocess.call(cmd)

    from .app import BlueprintApp

    BlueprintApp(root=(args.root or detect_root()).resolve()).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
