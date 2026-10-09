"""Agent push: a file-based message queue, one folder per herdr workspace.

Files instead of sockets keep this the same on macOS, Linux and Windows.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_state_dir
from watchfiles import Change, awatch

from ..item import Item, looks_like_mermaid

log = logging.getLogger("herdr_blueprint.inbox")

# Messages older than this are dropped, so yesterday's diagram doesn't pop up today.
MAX_AGE_SECONDS = 3600


@dataclass(frozen=True)
class Refresh:
    """The worker asked to redraw what is on screen."""

    draw: bool = False


def inbox_dir(workspace: str | None = None) -> Path:
    workspace = workspace or os.environ.get("HERDR_WORKSPACE_ID") or "default"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", workspace)
    return Path(user_state_dir("herdr-blueprint")) / "inbox" / safe


def send(message: dict, workspace: str | None = None) -> Path:
    """Write one message. The rename makes it appear complete or not at all."""
    folder = inbox_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
    tmp = folder / f".{name}.tmp"
    tmp.write_text(json.dumps({"sent_at": time.time(), **message}), encoding="utf-8")
    final = folder / name
    os.replace(tmp, final)
    return final


def _sent_at(data: dict, now: float) -> float:
    try:
        return float(data.get("sent_at", now))
    except (TypeError, ValueError):
        return now


def receive(path: Path, now: float | None = None) -> Item | Refresh | None:
    """Claim, read and delete one message.

    The claim is a rename, so when two viewers race only one gets the message.
    Broken, unknown and expired messages are dropped and logged.
    """
    now = time.time() if now is None else now
    claimed = path.with_name(f".{path.name}.{os.getpid()}.claimed")
    try:
        os.replace(path, claimed)
    except OSError:
        return None  # another viewer took it first
    try:
        data = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    finally:
        claimed.unlink(missing_ok=True)
    if not isinstance(data, dict):
        log.warning("Skipped broken message %s", path.name)
        return None
    at = _sent_at(data, now)
    if now - at > MAX_AGE_SECONDS:
        log.info("Skipped old message %s", path.name)
        return None
    kind = data.get("kind")
    draw = data.get("draw") is True
    sent_by = str(data.get("sent_by", "agent"))
    if kind == "refresh":
        return Refresh(draw=draw)
    if kind == "file" and data.get("path"):
        file = Path(data["path"])
        return Item(kind="file", title=file.name, path=file, sent_by=sent_by, at=at, draw=draw)
    if kind in ("text", "mermaid") and data.get("source"):
        source = str(data["source"])
        shape = "mermaid" if kind == "mermaid" or looks_like_mermaid(source) else "markdown"
        title = str(data.get("title") or ("Diagram" if shape == "mermaid" else "Note"))
        return Item(kind=shape, title=title, source=source, sent_by=sent_by, at=at, draw=draw)
    log.warning("Skipped unknown message %s", path.name)
    return None


def _pending(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.json") if not p.name.startswith("."))


async def watch_inbox(workspace: str | None = None) -> AsyncIterator[Item | Refresh]:
    """Yield the newest item already waiting, then each new message.

    Older waiting items and refreshes are dropped: the viewer shows one thing.
    """
    folder = inbox_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    waiting = [item for path in _pending(folder) if isinstance(item := receive(path), Item)]
    if waiting:
        yield waiting[-1]

    # Some platforms report the final rename as "modified", so accept any non-delete.
    def keep(change: Change, raw: str) -> bool:
        return change != Change.deleted and raw.endswith(".json") and not Path(raw).name.startswith(".")

    async for _changes in awatch(folder, watch_filter=keep, debounce=50):
        for path in _pending(folder):
            if message := receive(path):
                yield message
