"""Editing what Blueprint shows: the editor command, the copy's name, the diff,
and the prompt that hands the change to the agent. No Textual in here.

Blueprint never writes the shown file. The person edits a copy; saving and
quitting sends the change to the agent, which is the only one writing.
"""

from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

# Longer prompt bodies go into a file the agent reads; the prompt names it.
INLINE_LIMIT = 6_000

# herdr's agent kinds, the common ones first. The kind is also the program name.
AGENT_KINDS = (
    "claude", "codex", "gemini", "grok", "cursor", "opencode", "copilot", "amp",
    "qwen", "kimi", "kiro", "droid", "cline", "pi", "devin", "agy", "omp",
    "mastracode", "hermes", "kilo", "qodercli", "letta", "maki", "muse",
)

# GUI editors return at once unless told to wait for the window to close.
_WAITING_FLAGS = {"code": "--wait", "codium": "--wait", "subl": "--wait"}

_DOC_SUFFIXES = (".md", ".markdown", ".mmd", ".mermaid")


def _program(path: str) -> str:
    name = Path(path.replace("\\", "/")).name.lower()
    return name.rsplit(".", 1)[0] if name.endswith((".exe", ".cmd", ".bat")) else name


def editor_command(
    environ: Mapping[str, str] | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    system: str | None = None,
) -> list[str] | None:
    """$VISUAL, else $EDITOR, else vim, vi, nano; notepad last on Windows."""
    environ = os.environ if environ is None else environ
    system = os.name if system is None else system
    command: list[str] | None = None
    for key in ("VISUAL", "EDITOR"):
        raw = environ.get(key, "").strip()
        if raw:
            # Windows keeps backslashes; quotes around a path are not part of it.
            parts = shlex.split(raw, posix=system != "nt")
            command = [part.strip('"').strip("'") for part in parts if part.strip('"').strip("'")]
            break
    if not command:
        fallbacks = ("vim", "vi", "nano", "notepad") if system == "nt" else ("vim", "vi", "nano")
        found = next((path for name in fallbacks if (path := which(name))), None)
        if found is None:
            return None
        return [found]
    # Resolve the program so `EDITOR=code` finds code.cmd on Windows.
    command[0] = which(command[0]) or command[0]
    flag = _WAITING_FLAGS.get(_program(command[0]))
    if flag and flag not in command and "-w" not in command:
        command.append(flag)
    return command


def copy_name(name: str, diagram: bool) -> str:
    """A safe file name for the copy: the original's stem, cleaned, with the right suffix."""
    stem = name
    for suffix in _DOC_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(stem.replace("\\", "/")).name)
    stem = stem.strip(".-")[:60] or "draft"
    return f"{stem}{'.mmd' if diagram else '.md'}"


def unified_diff(original: str, edited: str, name: str) -> str:
    """A unified diff, or "" when the lines are the same."""
    lines = difflib.unified_diff(
        original.splitlines(), edited.splitlines(), fromfile=name, tofile=name, lineterm="",
    )
    body = "\n".join(lines)
    return f"{body}\n" if body else ""


def display_path(path: Path, cwd: Path | None) -> str:
    """The path as the agent should read it: relative to its folder when inside it."""
    if cwd is not None:
        try:
            return path.resolve().relative_to(cwd.resolve()).as_posix()
        except ValueError:
            pass
    return str(path)


def fenced(text: str, lang: str) -> str:
    """`text` in a code fence longer than any backtick run inside it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text.rstrip(chr(10))}\n{fence}"


def needs_attachment(body: str) -> bool:
    return len(body) > INLINE_LIMIT


def file_prompt(label: str, diff: str, *, diverged: bool, attachment: Path | None = None) -> str:
    """What the agent gets after an edit of a file."""
    lines = [
        f"I edited {label} in Blueprint. Apply this change to the file and to anything "
        "that depends on it, then show the result in Blueprint.",
    ]
    if diverged:
        lines.append(
            "The file changed on disk while I was editing, so read the file first and "
            "apply the change by hand rather than as a patch."
        )
    lines.append(f"The diff is in {attachment}." if attachment else fenced(diff, "diff"))
    return "\n".join(lines) + "\n"


def text_prompt(title: str, source: str, *, diagram: bool, attachment: Path | None = None) -> str:
    """What the agent gets after an edit of text it sent: the new version, not a diff."""
    what = "diagram" if diagram else "note"
    lines = [
        f"I edited the {what} you sent ('{title}') in Blueprint. This is what I want instead; "
        "update what it describes, then send the new version to Blueprint.",
        f"The new version is in {attachment}." if attachment else fenced(source, "mermaid" if diagram else "markdown"),
    ]
    return "\n".join(lines) + "\n"


def kinds_on_path(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Agent kinds whose program is installed, the common ones first."""
    return [kind for kind in AGENT_KINDS if which(kind)]


def agent_name(kind: str, pane_id: str) -> str:
    """A herdr agent name ([a-z][a-z0-9_-]{0,31}), one per kind and pane."""
    pane = re.sub(r"[^a-z0-9]+", "-", pane_id.lower()).strip("-")
    return f"bp-{kind}-{pane}"[:32].rstrip("-")
