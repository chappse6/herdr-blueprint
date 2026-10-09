"""Follow mode: report Markdown and Mermaid files saved in the workspace."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from watchfiles import Change, awatch

from ..history import VIEWABLE_SUFFIXES, Item

SKIP_DIRS = {"node_modules", "dist", "build", "target", "venv", "__pycache__"}


def is_viewable(path: Path, root: Path) -> bool:
    """A Markdown or Mermaid file outside hidden and build folders."""
    if path.suffix.lower() not in VIEWABLE_SUFFIXES:
        return False
    try:
        folders = path.relative_to(root).parts[:-1]
    except ValueError:
        return False
    return not any(part.startswith(".") or part in SKIP_DIRS for part in folders)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


async def follow(root: Path, debounce_ms: int = 300) -> AsyncIterator[Item]:
    """Yield the newest saved file after each burst of changes."""
    root = root.resolve()

    def keep(change: Change, raw: str) -> bool:
        return change != Change.deleted and is_viewable(Path(raw), root)

    async for changes in awatch(root, watch_filter=keep, debounce=debounce_ms):
        newest = max((Path(raw) for _, raw in changes), key=_mtime)
        title = newest.relative_to(root).as_posix()
        yield Item(kind="file", title=title, path=newest, sent_by="follow")
