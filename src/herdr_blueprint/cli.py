"""Command line: run the viewer, or send something to a running viewer."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .item import VIEWABLE_SUFFIXES
from .sources import inbox, pane

PLUGIN_ID = "seeun.blueprint"

DRAW_HELP = "draw Mermaid diagrams (slower; without it they stay as text)"


def focused_pane_id(herdr: str, pane_id: str | None, run=subprocess.run) -> str | None:
    """`pane_id`, or the focused pane's id when it is None (None if herdr can't tell)."""
    if pane_id:
        return pane_id
    try:
        # herdr prints UTF-8; Windows would otherwise decode with the ANSI code page.
        result = run([herdr, "pane", "current"], capture_output=True, encoding="utf-8", errors="replace")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)["result"]["pane"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        return None


# Set on the viewer by `open`: the pane it was opened next to.
SOURCE_ENV = "BLUEPRINT_SOURCE_PANE"


def open_command(herdr: str, pane_id: str | None) -> list[str]:
    """herdr command that opens the viewer to the right of `pane_id` and focuses it."""
    cmd = [
        herdr, "plugin", "pane", "open",
        "--plugin", PLUGIN_ID, "--entrypoint", "viewer",
        "--placement", "split", "--direction", "right", "--focus",
    ]
    if pane_id:
        cmd += ["--target-pane", pane_id, "--env", f"{SOURCE_ENV}={pane_id}"]
    return cmd


def viewer_command(herdr: str, pane_id: str | None, exists=pane.pane_exists) -> list[str]:
    """Focus the viewer already open in this workspace, or open one next to `pane_id`."""
    existing = inbox.viewer_pane()
    if existing and exists(herdr, existing):
        return [herdr, "plugin", "pane", "focus", existing]
    return open_command(herdr, pane_id)


def read_stdin() -> str:
    """stdin as UTF-8 whatever the console code page is."""
    raw = getattr(sys.stdin, "buffer", None)
    if raw is None:
        return sys.stdin.read()
    return raw.read().decode("utf-8-sig", errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herdr-blueprint",
        description="Live Markdown and Mermaid viewer for the herdr side pane.",
    )
    commands = parser.add_subparsers(dest="command")
    show = commands.add_parser("show", help="show a Markdown or Mermaid file in the viewer")
    show.add_argument("path", type=Path)
    show.add_argument("--draw", action="store_true", help=DRAW_HELP)
    send = commands.add_parser("send", help="send Markdown or Mermaid from stdin to the viewer")
    send.add_argument("--title", default=None, help="label in the header")
    send.add_argument("--draw", action="store_true", help=DRAW_HELP)
    refresh = commands.add_parser("refresh", help="re-read and redraw what the viewer shows")
    refresh.add_argument("--draw", action="store_true", help=DRAW_HELP)
    commands.add_parser("open", help="open the viewer pane in herdr")
    commands.add_parser("install-skill", help="add the Blueprint skill to Claude Code and Codex")
    commands.add_parser("uninstall-skill", help="remove the Blueprint skill")
    return parser


def main(argv: list[str] | None = None) -> int:
    # A narrow console encoding must not turn a sent message into a crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)

    if args.command == "show":
        path = args.path.expanduser().resolve()
        if not path.is_file():
            print(f"herdr-blueprint: file not found: {path}", file=sys.stderr)
            return 1
        if path.suffix.lower() not in VIEWABLE_SUFFIXES:
            print("herdr-blueprint: only .md, .markdown, .mmd and .mermaid files", file=sys.stderr)
            return 2
        inbox.send({"kind": "file", "path": str(path), "draw": args.draw, "sent_by": "agent"})
        print(f"Sent to Blueprint: {path.name}")
        return 0

    if args.command == "send":
        source = read_stdin().strip()
        if not source:
            print("herdr-blueprint: nothing on stdin", file=sys.stderr)
            return 2
        inbox.send({"kind": "text", "source": source, "title": args.title, "draw": args.draw, "sent_by": "agent"})
        print(f"Sent to Blueprint: {args.title or 'snippet'}")
        return 0

    if args.command == "refresh":
        inbox.send({"kind": "refresh", "draw": args.draw})
        print("Asked Blueprint to refresh")
        return 0

    if args.command in ("install-skill", "uninstall-skill"):
        from . import skills_install

        if not skills_install.SKILL_DIR.is_dir():
            print("herdr-blueprint: skill folder not found; run from the plugin folder", file=sys.stderr)
            return 1
        step = skills_install.install if args.command == "install-skill" else skills_install.uninstall
        for line in step(skills_install.SKILL_DIR, Path.home()) or ["nothing to do"]:
            print(line)
        return 0

    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    if args.command == "open":
        return subprocess.call(viewer_command(herdr, focused_pane_id(herdr, os.environ.get("HERDR_PANE_ID"))))

    from .app import BlueprintApp

    # Inside its herdr pane, the viewer records itself so `open` focuses it next time.
    viewer_id = os.environ.get("HERDR_PANE_ID")
    if viewer_id:
        inbox.mark_viewer(viewer_id)
    try:
        BlueprintApp(source=pane.source_of(herdr, os.environ.get(SOURCE_ENV))).run()
    finally:
        if viewer_id:
            inbox.clear_viewer(viewer_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
