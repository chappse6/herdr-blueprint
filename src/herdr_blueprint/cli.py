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

# The open action passes the workspace folder to the viewer pane in this variable.
ROOT_ENV = "HERDR_BLUEPRINT_ROOT"


def detect_root() -> Path:
    """Workspace folder: HERDR_BLUEPRINT_ROOT, then the herdr plugin context, then cwd."""
    env_root = os.environ.get(ROOT_ENV)
    if env_root and Path(env_root).is_dir():
        return Path(env_root)
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


def focused_pane(herdr: str, pane_id: str | None, run=subprocess.run) -> tuple[str | None, Path | None]:
    """Pane id and folder of `pane_id`, or of the focused pane when it is None."""
    cmd = [herdr, "pane", "get", pane_id] if pane_id else [herdr, "pane", "current"]
    try:
        result = run(cmd, capture_output=True, text=True)
    except OSError:
        return pane_id, None
    if result.returncode != 0:
        return pane_id, None
    try:
        pane = json.loads(result.stdout)["result"]["pane"]
    except (ValueError, KeyError, TypeError):
        return pane_id, None
    for key in ("foreground_cwd", "cwd"):
        folder = pane.get(key)
        if folder and Path(folder).is_dir():
            return pane.get("pane_id", pane_id), Path(folder)
    return pane.get("pane_id", pane_id), None


def open_command(herdr: str, pane_id: str | None, root: Path | None) -> list[str]:
    """herdr command that opens the viewer to the right without moving focus."""
    cmd = [
        herdr, "plugin", "pane", "open",
        "--plugin", PLUGIN_ID, "--entrypoint", "viewer",
        "--placement", "split", "--direction", "right", "--no-focus",
    ]
    if pane_id:
        cmd += ["--target-pane", pane_id]
    if root:
        cmd += ["--env", f"{ROOT_ENV}={root}"]
    return cmd


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
    commands.add_parser("install-skill", help="add the Blueprint skill to Claude Code and Codex")
    commands.add_parser("uninstall-skill", help="remove the Blueprint skill")
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

    if args.command in ("install-skill", "uninstall-skill"):
        from . import skills_install

        if not skills_install.SKILL_DIR.is_dir():
            print("herdr-blueprint: skill folder not found; run from the plugin folder", file=sys.stderr)
            return 1
        step = skills_install.install if args.command == "install-skill" else skills_install.uninstall
        for line in step(skills_install.SKILL_DIR, Path.home()) or ["nothing to do"]:
            print(line)
        return 0

    if args.command == "open":
        herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
        pane_id, root = focused_pane(herdr, os.environ.get("HERDR_PANE_ID"))
        return subprocess.call(open_command(herdr, pane_id, root))

    from .app import BlueprintApp

    BlueprintApp(root=(args.root or detect_root()).resolve()).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
