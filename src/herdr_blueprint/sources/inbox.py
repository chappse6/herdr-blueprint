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
from pathlib import Path

from platformdirs import user_state_dir
from watchfiles import Change, awatch

from ..history import Item

log = logging.getLogger("herdr_blueprint.inbox")

# Messages older than this are dropped, so yesterday's diagram doesn't pop up today.
MAX_AGE_SECONDS = 3600


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


def receive(path: Path, now: float | None = None) -> Item | None:
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
    sent_by = str(data.get("sent_by", "agent"))
    if data.get("kind") == "file" and data.get("path"):
        file = Path(data["path"])
        return Item(kind="file", title=file.name, path=file, sent_by=sent_by, at=at)
    if data.get("kind") == "mermaid" and data.get("source"):
        title = str(data.get("title") or "Diagram")
        return Item(kind="mermaid", title=title, source=str(data["source"]), sent_by=sent_by, at=at)
    log.warning("Skipped unknown message %s", path.name)
    return None


def _pending(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.json") if not p.name.startswith("."))


async def watch_inbox(workspace: str | None = None) -> AsyncIterator[Item]:
    """Yield messages already waiting, then each new one."""
    folder = inbox_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    for path in _pending(folder):
        if item := receive(path):
            yield item

    # Some platforms report the final rename as "modified", so accept any non-delete.
    def keep(change: Change, raw: str) -> bool:
        return change != Change.deleted and raw.endswith(".json") and not Path(raw).name.startswith(".")

    async for _changes in awatch(folder, watch_filter=keep, debounce=50):
        for path in _pending(folder):
            if item := receive(path):
                yield item
