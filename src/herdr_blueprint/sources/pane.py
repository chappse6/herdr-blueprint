"""The pane Blueprint was opened next to: who works there, asking them to draw,
and the docs in their folder."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..item import VIEWABLE_SUFFIXES, clean

# What `a` types into the agent. The agent finds the blueprint skill by name.
ASK_PROMPT = (
    "Use the blueprint skill: draw what you are working on now "
    "and send it to Blueprint with --draw."
)

# The file list is for picking, not browsing: the newest few are enough.
MAX_FILES = 200
MAX_DEPTH = 4
SKIP_DIRS = {"node_modules", "venv", "__pycache__", "dist", "build", "target"}

# Seconds before giving up on herdr or git, so a stuck command can't freeze the viewer.
TIMEOUT = 10


@dataclass(frozen=True)
class Source:
    pane_id: str
    agent: str | None = None
    task: str | None = None
    cwd: Path | None = None


def _herdr(herdr: str, args: list[str], run) -> tuple[dict | None, str | None]:
    """(result, None) on success, (None, error code) on failure."""
    try:
        # herdr prints UTF-8; Windows would otherwise decode with the ANSI code page.
        done = run([herdr, *args], capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None, None
    try:
        data = json.loads(done.stdout)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    if done.returncode != 0 or "error" in data:
        error = data.get("error")
        return None, error.get("code") if isinstance(error, dict) else None
    result = data.get("result")
    return (result if isinstance(result, dict) else {}), None


def _pane(herdr: str, pane_id: str, run) -> dict | None:
    result, _code = _herdr(herdr, ["pane", "get", pane_id], run)
    info = result.get("pane") if result else None
    return info if isinstance(info, dict) else None


def pane_exists(herdr: str, pane_id: str, run=subprocess.run) -> bool:
    return _pane(herdr, pane_id, run) is not None


def source_of(herdr: str, pane_id: str | None, run=subprocess.run) -> Source | None:
    """Who is in `pane_id`, or None when herdr can't tell."""
    if not pane_id:
        return None
    info = _pane(herdr, pane_id, run)
    if info is None:
        return None
    agent = info.get("agent") or None
    tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
    # A shell's terminal title is just "zsh" or a path; only an agent's says what it does.
    task = tokens.get("task") or (info.get("terminal_title_stripped") if agent else None)
    folder = info.get("foreground_cwd") or info.get("cwd")
    return Source(
        pane_id=pane_id,
        agent=clean(str(agent)) if agent else None,
        task=clean(str(task)) if task else None,
        cwd=Path(folder) if folder else None,
    )


def ask_to_draw(herdr: str, source: Source, run=subprocess.run) -> tuple[bool, str]:
    """Submit the draw request to the agent: (sent, a line for the header)."""
    agent = source.agent or "the agent"
    result, code = _herdr(herdr, ["agent", "prompt", source.pane_id, ASK_PROMPT], run)
    if result is not None:
        return True, f"Asked {agent} to draw"
    if code == "agent_blocked":
        return False, f"{agent} is waiting for your answer"
    if code == "agent_not_found":
        return False, f"{agent} is not in that pane anymore"
    return False, f"Could not reach {agent}"


def _git_files(folder: Path, run) -> list[Path] | None:
    try:
        done = run(
            ["git", "-C", str(folder), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [folder / name for name in done.stdout.split("\0") if name]


def _walk_files(folder: Path) -> list[Path]:
    found = []
    for root, dirs, files in os.walk(folder):
        depth = len(Path(root).relative_to(folder).parts)
        dirs[:] = [] if depth >= MAX_DEPTH else [
            d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
        ]
        found += [Path(root) / name for name in files]
    return found


def find_documents(folder: Path, limit: int = MAX_FILES, run=subprocess.run) -> list[Path]:
    """Markdown and Mermaid files under `folder`, newest first.

    Inside a git repo .gitignore is respected; elsewhere hidden and dependency
    folders are skipped and the walk stops a few levels down.
    """
    candidates = _git_files(folder, run)
    if candidates is None:
        candidates = _walk_files(folder)
    dated = []
    for path in candidates:
        if path.suffix.lower() not in VIEWABLE_SUFFIXES:
            continue
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:
            continue  # listed by git but deleted since
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _mtime, path in dated[:limit]]


def short_folder(path: Path, home: Path | None = None) -> str:
    """A folder short enough for a side pane: ~/…/parent/name."""
    home = Path.home() if home is None else home
    try:
        prefix, parts = "~", path.relative_to(home).parts
    except ValueError:
        prefix, parts = path.anchor.rstrip("\\/"), path.parts[1:]
    if len(parts) > 2:
        parts = ("…", *parts[-2:])
    return os.sep.join((prefix, *parts)) if parts else (prefix or os.sep)
